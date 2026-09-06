"""Beat Saber ZIP packaging.

Beat Saber expects the level files at the *root* of the archive — a wrapping
folder makes the level invisible to the game. Everything here writes flat.
"""

from __future__ import annotations

import logging
import re
import secrets
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.models.beatmap import GeneratedBeatmap
from app.models.musical import BeatGrid
from app.services.export.beatsaber_exporter import (
    COVER_FILENAME,
    INFO_FILENAME,
    SONG_FILENAME,
    MapMetadata,
    dumps,
    serialize_difficulty,
    serialize_info,
)
from app.services.export.cover_art import generate_cover

logger = logging.getLogger(__name__)

#: Characters that are genuinely unsafe in a filename on some platform:
#: path separators, the Windows reserved set, and control codes. Everything
#: else — including every non-Latin script — is kept.
_UNSAFE = re.compile(r'[/\\<>:"|?*\x00-\x1f]+')

#: Windows refuses these as filenames whatever the extension.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{digit}" for digit in range(1, 10)}
    | {f"LPT{digit}" for digit in range(1, 10)}
)

#: Filesystems cap filenames in *bytes*, not characters, and one CJK character
#: costs three of them.
_MAX_FILENAME_BYTES = 180


@dataclass(slots=True)
class PackageResult:
    zip_path: Path
    filename: str
    contents: list[str]
    size_bytes: int


def _truncate_bytes(value: str, limit: int) -> str:
    """Trim to a byte budget without splitting a character in half."""
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore").rstrip()


#: Range of the leading id. Five digits, never zero-padded down to four.
_MAP_ID_MIN = 10000
_MAP_ID_SPAN = 90000


def random_map_id() -> str:
    """A fresh five-digit id for a map.

    Community maps are distributed as ``<id> (Song - Mapper)``, where the id
    comes from BeatSaver's catalogue. We have none to draw on, so each build
    gets a new random one — which also means regenerating a song never
    overwrites the folder you already installed.
    """
    return f"{secrets.randbelow(_MAP_ID_SPAN) + _MAP_ID_MIN:05d}"


def safe_package_name(
    title: str,
    artist: str,
    difficulty_label: str,
    *,
    mapper: str = "SaberMapper AI",
    map_id: str | None = None,
) -> str:
    """Build a download filename in the community's own convention.

    ``12603 (Song Name - Mapper).zip`` — five digits, one space, then the song
    and the mapper in brackets. This is the shape BeatSaver ships and every map
    manager expects, so a download drops into a library beside everything else
    instead of standing out. The artist is deliberately absent: it is already
    in Info.dat, and the slot after the dash belongs to whoever made the map.

    Only characters that are actually dangerous are removed. An earlier version
    kept an ASCII allowlist, which quietly destroyed any title that was not
    written in Latin script — a Cyrillic song came out as ``- (Expert).zip``.
    Modern filesystems handle Unicode, and the download header already encodes
    the name per RFC 5987, so there is nothing to protect against by dropping
    it.
    """

    def clean(value: str) -> str:
        return re.sub(r"\s+", " ", _UNSAFE.sub(" ", value or "")).strip(" .")

    # The convention carries the song and the *mapper*, not the artist — the
    # artist already lives in Info.dat, and repeating it here just crowds the
    # name (a YouTube title usually starts with it anyway). It stays in the
    # signature because it belongs to the concept and callers already pass it.
    del artist
    song = clean(title)
    clean_mapper = clean(mapper) or "SaberMapper AI"
    identifier = map_id or random_map_id()

    # Trim the song, never the frame: the id and the mapper are what make the
    # name recognisable and unique, so they have to survive truncation.
    frame = len(f"{identifier} ( - {clean_mapper}).zip".encode())
    song = _truncate_bytes(song, max(_MAX_FILENAME_BYTES - frame, 16)).strip(" .-")

    if not song or song.split(".")[0].upper() in _RESERVED_NAMES:
        song = f"SaberMapper Map ({difficulty_label})"
    return f"{identifier} ({song} - {clean_mapper}).zip"


def build_bundle(
    sources: list[tuple[str, Path]],
    destination: Path,
) -> PackageResult:
    """Archive several finished maps into one download.

    Each map becomes its own *folder* inside the bundle rather than a nested
    zip, so the whole archive extracts straight into a Beat Saber
    `CustomLevels` directory and every map is installed at once. That is the
    opposite of the single-map rule — a lone map must be flat at its zip root
    — because this archive is a map pack, not a level.
    """
    if not sources:
        raise ValueError("At least one map is required to build a bundle")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    contents: list[str] = []
    used: set[str] = set()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, package_dir in sources:
            if not package_dir.is_dir():
                continue
            folder = _unique_folder(name, used)
            for path in sorted(package_dir.iterdir()):
                if path.is_file():
                    archive.write(path, arcname=f"{folder}/{path.name}")
            contents.append(folder)

    if not contents:
        destination.unlink(missing_ok=True)
        raise FileNotFoundError("None of the selected maps are still available")

    logger.info("bundle_built", extra={"maps": len(contents)})
    return PackageResult(
        zip_path=destination,
        filename=f"SaberMapper Maps ({len(contents)}).zip",
        contents=contents,
        size_bytes=destination.stat().st_size,
    )


def _unique_folder(name: str, used: set[str]) -> str:
    """Folder name for one map inside a bundle, de-duplicated."""
    base = _UNSAFE.sub(" ", name).strip(" .") or "SaberMapper Map"
    base = _truncate_bytes(base, 120).strip(" .") or "SaberMapper Map"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base} ({suffix})"
        suffix += 1
    used.add(candidate)
    return candidate


def build_package(
    *,
    beatmaps: list[GeneratedBeatmap],
    metadata: MapMetadata,
    grid: BeatGrid,
    song_source: Path,
    output_dir: Path,
    cover_source: Path | None = None,
) -> PackageResult:
    """Write the map files and zip them with the level files at the root."""
    if not beatmaps:
        raise ValueError("At least one beatmap is required to build a package")

    build_dir = output_dir / "package"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    # -- audio -------------------------------------------------------------
    if not song_source.exists():
        raise FileNotFoundError("Song audio is missing")
    song_target = build_dir / SONG_FILENAME
    shutil.copyfile(song_source, song_target)

    # -- cover -------------------------------------------------------------
    cover_target = build_dir / COVER_FILENAME
    if cover_source is not None and cover_source.exists():
        shutil.copyfile(cover_source, cover_target)
    else:
        generate_cover(
            cover_target,
            title=metadata.title,
            artist=metadata.artist,
            difficulty_label=beatmaps[0].difficulty.label,
        )

    # -- difficulty documents ----------------------------------------------
    written: list[str] = [SONG_FILENAME, COVER_FILENAME]
    for beatmap in beatmaps:
        filename = beatmap.difficulty.beatmap_filename
        document = serialize_difficulty(beatmap, grid)
        (build_dir / filename).write_text(dumps(document), encoding="utf-8")
        written.append(filename)

    # -- info ---------------------------------------------------------------
    info = serialize_info(beatmaps, metadata, grid)
    (build_dir / INFO_FILENAME).write_text(dumps(info), encoding="utf-8")
    written.append(INFO_FILENAME)

    # -- zip ----------------------------------------------------------------
    filename = safe_package_name(
        metadata.title,
        metadata.artist,
        beatmaps[0].difficulty.label,
        mapper=metadata.mapper,
    )
    zip_path = output_dir / "beatmap.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        # Info.dat first is conventional and helps some level loaders.
        ordered = [INFO_FILENAME, *sorted(name for name in written if name != INFO_FILENAME)]
        for name in ordered:
            archive.write(build_dir / name, arcname=name)

    result = PackageResult(
        zip_path=zip_path,
        filename=filename,
        contents=ordered,
        size_bytes=zip_path.stat().st_size,
    )
    logger.info(
        "package_built",
        extra={"files": len(ordered), "size_bytes": result.size_bytes},
    )
    return result
