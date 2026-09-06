"""Adapter for user-uploaded audio files."""

from __future__ import annotations

import re
from pathlib import Path

from app.models.enums import SourceType
from app.services.media.base import AudioSource, MediaAcquisitionError, NormalizedMedia
from app.services.media.probe import probe_audio

#: Extensions we are willing to hand to ffmpeg.
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus", ".webm"}

ALLOWED_MIME_PREFIXES = ("audio/", "video/mp4", "video/webm", "application/octet-stream")

#: Junk that download tools and rippers leave on filenames.
_LEADING_TRACK_NUMBER = re.compile(r"^\s*\d{1,3}\s*[.\-_)]\s*")
_DOWNLOADER_PREFIX = re.compile(
    r"^\s*(?:www\.)?[a-z0-9-]+\.(?:com|cc|net|to|io|org)\s*[-\u2013\u2014]\s*",
    re.IGNORECASE,
)
_QUALITY_SUFFIX = re.compile(
    r"[\s_\-]*(?:\(|\[)?(?:\d{2,3}\s*kbps|HD|HQ|FHD|4K|"
    r"official\s*(?:mv|m/v|video|audio|lyric\s*video)?|lyrics?(?:\s*video)?|"
    r"audio|mv|m/v)(?:\)|\])?\s*$",
    re.IGNORECASE,
)
_UNDERSCORES = re.compile(r"_+")
_WHITESPACE = re.compile(r"\s+")


def sanitize_filename(raw: str) -> str:
    """Strip a client-supplied filename down to a safe basename.

    The result is only ever used for display and for choosing an extension —
    the file itself is always stored under a UUID path — but it is sanitised
    thoroughly anyway. Note the explicit backslash handling: `Path().name` does
    not treat `\\` as a separator on POSIX, so a Windows-style path would
    otherwise survive as one long "filename" with its dot-dot segments intact.
    """
    candidate = (raw or "").replace("\x00", "")
    # Normalise both separator styles before taking the basename.
    candidate = candidate.replace("\\", "/")
    candidate = Path(candidate).name
    candidate = re.sub(r"[^A-Za-z0-9._ ()\[\]-]", "_", candidate)
    # Collapse any remaining dot runs so no ".." segment can survive.
    candidate = re.sub(r"\.{2,}", ".", candidate)
    candidate = candidate.strip(". ")
    return candidate[:120] or "upload"


def title_from_filename(filename: str) -> str:
    """Derive a readable song title from an uploaded file's name.

    Deliberately *not* routed through `sanitize_filename`. That function exists
    to make a name safe as a filesystem path, and its ASCII allowlist reduced
    any non-Latin title to nothing — a Chinese song called 跳楼机 arrived as
    "Untitled". Titles are only ever displayed and written into Info.dat as
    UTF-8 JSON, so there is nothing here to make safe.

    Rippers and download sites leave a lot of debris on filenames, and stripping
    the common cases is the difference between a title and a mess.
    """
    name = Path((filename or "").replace("\\", "/")).name
    # Not `Path.stem`: for a name that is nothing but an extension (".mp3") it
    # applies dotfile rules and hands back the extension itself.
    stem = name
    for suffix in ALLOWED_EXTENSIONS:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    stem = _UNDERSCORES.sub(" ", stem)
    stem = _DOWNLOADER_PREFIX.sub("", stem)
    stem = _LEADING_TRACK_NUMBER.sub("", stem)
    # Repeated, because "… (Official MV) [HD]" stacks two of them.
    for _pass in range(3):
        cleaned = _QUALITY_SUFFIX.sub("", stem)
        if cleaned == stem:
            break
        stem = cleaned
    stem = _WHITESPACE.sub(" ", stem).strip(" -\u2013\u2014_.")
    return stem[:140] or "Untitled"


class UploadedFileSource(AudioSource):
    """Wraps an already-persisted upload and validates it is real audio."""

    def __init__(
        self,
        stored_path: Path,
        *,
        original_filename: str,
        content_type: str | None,
        max_duration: float,
    ) -> None:
        self._path = stored_path
        self._original_filename = original_filename
        self._content_type = content_type
        self._max_duration = max_duration

    @staticmethod
    def validate_metadata(filename: str, content_type: str | None) -> None:
        """Cheap pre-flight checks run before any bytes are written to disk."""
        suffix = Path(sanitize_filename(filename)).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise MediaAcquisitionError(
                "Unsupported file type. Please upload an MP3 file.",
                detail=f"rejected extension {suffix!r}",
            )
        if content_type and not content_type.lower().startswith(ALLOWED_MIME_PREFIXES):
            raise MediaAcquisitionError(
                "Unsupported file type. Please upload an MP3 file.",
                detail=f"rejected content-type {content_type!r}",
            )

    async def acquire(self) -> NormalizedMedia:
        if not self._path.exists() or self._path.stat().st_size == 0:
            raise MediaAcquisitionError("The uploaded file is empty or could not be read.")

        probe = await probe_audio(self._path)
        if probe.duration <= 0:
            raise MediaAcquisitionError(
                "That file does not contain readable audio.",
                detail="probe reported zero duration",
            )
        if probe.duration > self._max_duration:
            minutes = int(self._max_duration // 60)
            raise MediaAcquisitionError(
                f"Audio is longer than the {minutes} minute limit.",
                detail=f"duration {probe.duration:.1f}s",
            )

        return NormalizedMedia(
            source_type=SourceType.UPLOAD,
            source_path=self._path,
            title=probe.title or title_from_filename(self._original_filename),
            artist=probe.artist or "Unknown Artist",
            duration=probe.duration,
            thumbnail=None,
            original_url=None,
        )
