"""Beat Saber serialisation and ZIP packaging."""

from __future__ import annotations

import json
import zipfile

import pytest

from app.models.beatmap import (
    BeatNote,
    Bomb,
    GeneratedBeatmap,
    LightEvent,
    MapStatistics,
    Obstacle,
)
from app.models.enums import (
    CutDirection,
    Difficulty,
    Hand,
    MappingStyle,
    ObstacleKind,
)
from app.models.musical import BeatGrid
from app.services.export.beatsaber_exporter import (
    BEATMAP_VERSION,
    COVER_FILENAME,
    INFO_FILENAME,
    SONG_FILENAME,
    MapMetadata,
    dumps,
    serialize_difficulty,
    serialize_info,
)
from app.services.export.cover_art import generate_cover
from app.services.export.package_builder import build_package, safe_package_name


@pytest.fixture
def simple_map() -> GeneratedBeatmap:
    return GeneratedBeatmap(
        difficulty=Difficulty.EXPERT,
        style=MappingStyle.BALANCED,
        intensity=0.6,
        seed=1,
        bpm=128.0,
        duration=60.0,
        notes=[
            BeatNote(beat=0.0, hand=Hand.LEFT, x=1, y=0, direction=CutDirection.DOWN),
            BeatNote(beat=1.0, hand=Hand.RIGHT, x=2, y=1, direction=CutDirection.UP),
        ],
        obstacles=[
            Obstacle(beat=4.0, duration=1.0, x=0, y=0, width=1, height=5,
                     kind=ObstacleKind.DODGE_LEFT)
        ],
        bombs=[Bomb(beat=8.0, x=1, y=2)],
        lights=[LightEvent(beat=0.0, event_type=4, value=5)],
        note_jump_speed=16.5,
        note_jump_offset=-0.2,
        statistics=MapStatistics(total_notes=2),
    )


class TestDifficultySerialization:
    GRID = BeatGrid(bpm=128.0, offset=0.0)

    def test_uses_the_v3_schema(self, simple_map):
        document = serialize_difficulty(simple_map, self.GRID)
        assert document["version"] == BEATMAP_VERSION

    def test_includes_every_required_v3_collection(self, simple_map):
        document = serialize_difficulty(simple_map, self.GRID)
        for key in (
            "bpmEvents", "rotationEvents", "colorNotes", "bombNotes", "obstacles",
            "sliders", "burstSliders", "waypoints", "basicBeatmapEvents",
            "colorBoostBeatmapEvents", "lightColorEventBoxGroups",
            "lightRotationEventBoxGroups", "lightTranslationEventBoxGroups",
            "basicEventTypesWithKeywords", "useNormalEventsAsCompatibleEvents",
        ):
            assert key in document, key

    def test_note_fields_use_the_v3_keys(self, simple_map):
        note = serialize_difficulty(simple_map, self.GRID)["colorNotes"][0]
        assert set(note) == {"b", "x", "y", "c", "d", "a"}
        assert note["c"] == 0 and note["d"] == CutDirection.DOWN.value

    def test_obstacle_fields_use_the_v3_keys(self, simple_map):
        obstacle = serialize_difficulty(simple_map, self.GRID)["obstacles"][0]
        assert set(obstacle) == {"b", "d", "x", "y", "w", "h"}

    def test_collections_are_sorted_by_beat(self, simple_map):
        simple_map.notes.append(
            BeatNote(beat=0.5, hand=Hand.LEFT, x=0, y=0, direction=CutDirection.DOWN)
        )
        notes = serialize_difficulty(simple_map, self.GRID)["colorNotes"]
        assert [note["b"] for note in notes] == sorted(note["b"] for note in notes)

    def test_grid_offset_is_folded_into_exported_beats(self):
        """Beat Saber counts beat 0 at t=0, so our grid offset must be added."""
        offset_grid = BeatGrid(bpm=120.0, offset=0.5)
        beatmap = GeneratedBeatmap(
            difficulty=Difficulty.EXPERT, style=MappingStyle.BALANCED, intensity=0.5,
            seed=1, bpm=120.0, duration=30.0,
            notes=[BeatNote(beat=0.0, hand=Hand.LEFT, x=1, y=1,
                            direction=CutDirection.DOWN)],
        )
        exported = serialize_difficulty(beatmap, offset_grid)["colorNotes"][0]["b"]
        # Grid beat 0 lands at t=0.5s, which is one beat at 120 BPM.
        assert exported == pytest.approx(1.0)

    def test_exported_beats_are_never_negative(self):
        grid = BeatGrid(bpm=120.0, offset=2.0)
        beatmap = GeneratedBeatmap(
            difficulty=Difficulty.EASY, style=MappingStyle.BALANCED, intensity=0.5,
            seed=1, bpm=120.0, duration=30.0,
            notes=[BeatNote(beat=-8.0, hand=Hand.LEFT, x=1, y=1,
                            direction=CutDirection.DOWN)],
        )
        assert serialize_difficulty(beatmap, grid)["colorNotes"][0]["b"] >= 0

    def test_serialization_is_byte_stable(self, simple_map):
        first = dumps(serialize_difficulty(simple_map, self.GRID))
        second = dumps(serialize_difficulty(simple_map, self.GRID))
        assert first == second


class TestInfoSerialization:
    GRID = BeatGrid(bpm=128.0, offset=0.0)
    METADATA = MapMetadata(title="Test Song", artist="Test Artist")

    def test_contains_the_required_fields(self, simple_map):
        info = serialize_info([simple_map], self.METADATA, self.GRID)
        for key in (
            "_version", "_songName", "_songAuthorName", "_levelAuthorName",
            "_beatsPerMinute", "_songFilename", "_coverImageFilename",
            "_environmentName", "_difficultyBeatmapSets",
        ):
            assert key in info, key

    def test_references_the_packaged_filenames(self, simple_map):
        info = serialize_info([simple_map], self.METADATA, self.GRID)
        assert info["_songFilename"] == SONG_FILENAME
        assert info["_coverImageFilename"] == COVER_FILENAME

    def test_difficulty_entry_matches_the_beatmap(self, simple_map):
        info = serialize_info([simple_map], self.METADATA, self.GRID)
        entry = info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"][0]
        assert entry["_difficulty"] == "Expert"
        assert entry["_difficultyRank"] == 7
        assert entry["_beatmapFilename"] == "ExpertStandard.dat"
        assert entry["_noteJumpMovementSpeed"] == pytest.approx(16.5)

    def test_characteristic_is_standard(self, simple_map):
        info = serialize_info([simple_map], self.METADATA, self.GRID)
        assert info["_difficultyBeatmapSets"][0]["_beatmapCharacteristicName"] == "Standard"

    def test_supports_multiple_difficulties_in_one_info(self, simple_map):
        """The exporter is already shaped for a future 'all difficulties' run."""
        import dataclasses

        easy = dataclasses.replace(simple_map, difficulty=Difficulty.EASY)
        info = serialize_info([simple_map, easy], self.METADATA, self.GRID)
        entries = info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"]
        assert [entry["_difficulty"] for entry in entries] == ["Easy", "Expert"]

    def test_rejects_an_empty_beatmap_list(self):
        with pytest.raises(ValueError):
            serialize_info([], self.METADATA, self.GRID)

    def test_difficulty_ranks_are_the_beat_saber_values(self):
        assert [d.beatsaber_rank for d in Difficulty] == [1, 3, 5, 7, 9]

    def test_expert_plus_uses_its_display_label(self):
        assert Difficulty.EXPERT_PLUS.label == "Expert+"
        assert Difficulty.EXPERT_PLUS.value == "ExpertPlus"
        assert Difficulty.EXPERT_PLUS.beatmap_filename == "ExpertPlusStandard.dat"


class TestPackaging:
    def test_zip_has_the_level_files_at_its_root(self, tmp_path, simple_map):
        song = tmp_path / "song.ogg"
        song.write_bytes(b"not really vorbis, but the packer only copies it")
        package = build_package(
            beatmaps=[simple_map],
            metadata=MapMetadata(title="Neon", artist="Tester"),
            grid=BeatGrid(bpm=128.0, offset=0.0),
            song_source=song,
            output_dir=tmp_path,
        )
        with zipfile.ZipFile(package.zip_path) as archive:
            names = archive.namelist()

        assert set(names) == {INFO_FILENAME, "ExpertStandard.dat", SONG_FILENAME, COVER_FILENAME}
        # No entry may sit inside a folder — Beat Saber would not see the level.
        assert all("/" not in name for name in names)

    def test_packaged_json_is_readable(self, tmp_path, simple_map):
        song = tmp_path / "song.ogg"
        song.write_bytes(b"audio")
        package = build_package(
            beatmaps=[simple_map],
            metadata=MapMetadata(title="Neon", artist="Tester"),
            grid=BeatGrid(bpm=128.0, offset=0.0),
            song_source=song,
            output_dir=tmp_path,
        )
        with zipfile.ZipFile(package.zip_path) as archive:
            info = json.loads(archive.read(INFO_FILENAME))
            difficulty = json.loads(archive.read("ExpertStandard.dat"))
        assert info["_songName"] == "Neon"
        assert difficulty["version"] == BEATMAP_VERSION

    def test_missing_audio_is_reported(self, tmp_path, simple_map):
        with pytest.raises(FileNotFoundError):
            build_package(
                beatmaps=[simple_map],
                metadata=MapMetadata(title="Neon", artist="Tester"),
                grid=BeatGrid(bpm=128.0, offset=0.0),
                song_source=tmp_path / "missing.ogg",
                output_dir=tmp_path,
            )


class TestPackageNaming:
    """Community convention: `<id> (Song - Mapper).zip`."""

    def test_uses_the_community_convention(self):
        name = safe_package_name("Song", "Artist", "Expert", map_id="12603")
        assert name == "12603 (Song - SaberMapper AI).zip"

    def test_the_mapper_takes_the_slot_after_the_dash(self):
        name = safe_package_name("Song", "Artist", "Expert", mapper="SomeMapper",
                                 map_id="12603")
        assert name == "12603 (Song - SomeMapper).zip"

    def test_the_artist_is_left_to_info_dat(self):
        """It is already stored in the map; repeating it just crowds the name."""
        assert "Artist" not in safe_package_name("Song", "Artist", "Expert")

    def test_the_id_is_five_digits(self):
        from app.services.export.package_builder import random_map_id

        for _ in range(200):
            value = random_map_id()
            assert len(value) == 5 and value.isdigit()
            assert 10000 <= int(value) <= 99999

    def test_the_id_is_random_per_build(self):
        """Regenerating a song gets a new folder rather than overwriting the
        one already installed."""
        from app.services.export.package_builder import random_map_id

        assert len({random_map_id() for _ in range(200)}) > 150

    def test_the_name_starts_with_five_digits_and_one_space(self):
        import re

        name = safe_package_name("Song", "Artist", "Expert")
        assert re.fullmatch(r"[0-9]{5} \(.+\)\.zip", name), name

    def test_strips_path_separators_and_control_characters(self):
        name = safe_package_name("../../etc/passwd", "a\\b", "Expert")
        assert "/" not in name and "\\" not in name
        assert "\x00" not in name
        assert name.endswith(".zip")

    def test_strips_characters_windows_refuses(self):
        name = safe_package_name('a<b>c:d"e|f?g*h', "Artist", "Expert")
        assert not set(name) & set('<>:"|?*')

    @pytest.mark.parametrize(
        "title,artist",
        [
            ("萬能和弦 - 我不該動情", "Loop Pop Music"),
            ("Гимн", "Артист"),
            ("恋", "星野源"),
            ("Übergang", "Kraftwerk"),
        ],
    )
    def test_non_latin_titles_survive(self, title, artist):
        """An ASCII allowlist used to reduce a Cyrillic song to '- (Expert).zip'.

        The name is only ever used for the download header, which encodes it
        per RFC 5987, so there was nothing gained by discarding it.
        """
        name = safe_package_name(title, artist, "Expert")
        assert title.split()[0] in name

    def test_never_produces_an_empty_name(self):
        name = safe_package_name("///", "", "Expert", map_id="12603")
        assert name == "12603 (SaberMapper Map (Expert) - SaberMapper AI).zip"

    def test_avoids_windows_reserved_device_names(self):
        assert not safe_package_name("CON", "", "Expert").startswith("CON")
        assert "SaberMapper Map" in safe_package_name("NUL", "", "Normal")

    def test_a_youtube_title_is_not_doubled_up(self):
        """YouTube titles already read "Artist - Song"."""
        name = safe_package_name(
            "Luis Fonsi - Despacito ft. Daddy Yankee", "Luis Fonsi", "Expert"
        )
        assert name.count("Luis Fonsi") == 1

    def test_the_mapper_survives_truncation(self):
        """The id and the mapper are what make the name recognisable."""
        name = safe_package_name("音" * 300, "楽" * 300, "ExpertPlus", map_id="12603")
        assert name.startswith("12603 (")
        assert name.endswith("- SaberMapper AI).zip")
        assert len(name.encode("utf-8")) <= 200

    def test_caps_the_length_in_bytes_not_characters(self):
        """Filesystems budget bytes, and one CJK character costs three."""
        for title in ("x" * 500, "音" * 500):
            name = safe_package_name(title, "y" * 500, "Expert")
            assert len(name.encode("utf-8")) <= 200

    def test_truncation_never_splits_a_character(self):
        name = safe_package_name("音" * 300, "楽" * 300, "Expert")
        name.encode("utf-8").decode("utf-8")  # raises if a character was cut


class TestCoverArt:
    def test_generates_a_512px_png(self, tmp_path):
        from PIL import Image

        path = generate_cover(
            tmp_path / "cover.png", title="Neon Circuit", artist="Tester",
            difficulty_label="Expert",
        )
        assert path.exists()
        with Image.open(path) as image:
            assert image.size == (512, 512)
            assert image.format == "PNG"

    def test_handles_long_and_unicode_titles(self, tmp_path):
        path = generate_cover(
            tmp_path / "cover.png",
            title="A Really Extremely Very Long Song Title That Will Not Fit ★ 音楽",
            artist="An Artist With A Similarly Excessive Name",
            difficulty_label="Expert+",
        )
        assert path.stat().st_size > 0

    def test_is_deterministic_for_the_same_song(self, tmp_path):
        first = generate_cover(tmp_path / "a.png", title="X", artist="Y",
                              difficulty_label="Expert")
        second = generate_cover(tmp_path / "b.png", title="X", artist="Y",
                               difficulty_label="Expert")
        assert first.read_bytes() == second.read_bytes()


class TestValidationAssetChecks:
    """Presence checks must reflect what was actually verified."""

    @staticmethod
    def _beatmap():
        return GeneratedBeatmap(
            difficulty=Difficulty.EXPERT, style=MappingStyle.BALANCED, intensity=0.5,
            seed=1, bpm=120.0, duration=60.0,
            notes=[BeatNote(beat=0.0, hand=Hand.LEFT, x=1, y=1,
                            direction=CutDirection.DOWN)],
        )

    def test_asset_checks_are_omitted_when_no_paths_are_given(self):
        from app.services.mapping.difficulty_profiles import PROFILES
        from app.services.mapping.map_validator import validate_beatmap

        report = validate_beatmap(self._beatmap(), PROFILES[Difficulty.EXPERT])
        # Absent, not False: a check that never ran must not read as a failure.
        assert "audio_present" not in report.checks
        assert "cover_present" not in report.checks

    def test_asset_checks_pass_when_the_files_exist(self, tmp_path):
        from app.services.mapping.difficulty_profiles import PROFILES
        from app.services.mapping.map_validator import validate_beatmap

        song = tmp_path / "song.ogg"
        song.write_bytes(b"audio")
        cover = tmp_path / "cover.png"
        cover.write_bytes(b"image")

        report = validate_beatmap(
            self._beatmap(), PROFILES[Difficulty.EXPERT],
            song_path=song, cover_path=cover,
        )
        assert report.checks["audio_present"] is True
        assert report.checks["cover_present"] is True

    def test_missing_assets_are_errors(self, tmp_path):
        from app.services.mapping.difficulty_profiles import PROFILES
        from app.services.mapping.map_validator import validate_beatmap

        report = validate_beatmap(
            self._beatmap(), PROFILES[Difficulty.EXPERT],
            song_path=tmp_path / "gone.ogg", cover_path=tmp_path / "gone.png",
        )
        assert report.checks["audio_present"] is False
        assert not report.ok
        assert any("audio" in error.lower() for error in report.errors)


class TestBundle:
    """A bundle is a map *pack*: one folder per map, not a flat level."""

    @staticmethod
    def _package(root, name: str):
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "Info.dat").write_text("{}")
        (directory / "ExpertStandard.dat").write_text("{}")
        (directory / "song.ogg").write_bytes(b"audio")
        (directory / "cover.png").write_bytes(b"image")
        return directory

    def test_each_map_gets_its_own_folder(self, tmp_path):
        from app.services.export.package_builder import build_bundle

        sources = [
            ("Artist - One (Expert)", self._package(tmp_path, "a")),
            ("Artist - Two (Hard)", self._package(tmp_path, "b")),
        ]
        result = build_bundle(sources, tmp_path / "bundle.zip")
        with zipfile.ZipFile(result.zip_path) as archive:
            names = archive.namelist()

        assert all("/" in name for name in names), "a bundle nests, a level does not"
        folders = {name.split("/")[0] for name in names}
        assert folders == {"Artist - One (Expert)", "Artist - Two (Hard)"}
        for folder in folders:
            assert f"{folder}/Info.dat" in names
            assert f"{folder}/song.ogg" in names

    def test_duplicate_names_are_kept_apart(self, tmp_path):
        from app.services.export.package_builder import build_bundle

        sources = [
            ("Same Name", self._package(tmp_path, "a")),
            ("Same Name", self._package(tmp_path, "b")),
        ]
        result = build_bundle(sources, tmp_path / "bundle.zip")
        assert len(set(result.contents)) == 2

    def test_non_latin_folder_names_survive(self, tmp_path):
        from app.services.export.package_builder import build_bundle

        result = build_bundle(
            [("跳楼机 - 爱吃大苹果 (Expert+)", self._package(tmp_path, "a"))],
            tmp_path / "bundle.zip",
        )
        assert "跳楼机" in result.contents[0]

    def test_missing_maps_are_skipped_not_fatal(self, tmp_path):
        from app.services.export.package_builder import build_bundle

        result = build_bundle(
            [("Gone", tmp_path / "nope"), ("Here", self._package(tmp_path, "a"))],
            tmp_path / "bundle.zip",
        )
        assert result.contents == ["Here"]

    def test_an_entirely_missing_selection_raises(self, tmp_path):
        from app.services.export.package_builder import build_bundle

        with pytest.raises(FileNotFoundError):
            build_bundle([("Gone", tmp_path / "nope")], tmp_path / "bundle.zip")

    def test_an_empty_selection_raises(self, tmp_path):
        from app.services.export.package_builder import build_bundle

        with pytest.raises(ValueError):
            build_bundle([], tmp_path / "bundle.zip")
