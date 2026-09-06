"""Beat Saber serialisation.

The only module that knows Beat Saber's file format. Difficulty data is written
in the v3 schema (widely supported by the current game and by community
tooling) and Info.dat in the v2 schema, which every loader still reads. Adding
a v4 writer later means adding a sibling of `serialize_difficulty` — nothing in
the mapping engine has to change.

One subtlety: the mapping engine works on a beat grid that starts at the song's
first detected beat, but Beat Saber counts beat 0 at t=0 of the audio. Every
beat value is therefore converted through wall-clock time on the way out.

A second subtlety applies to songs that do not hold one tempo. Beat Saber
expresses tempo changes as `bpmEvents`, which *redefine* the beat axis from the
event onward: a note's `b` value is measured in the beat space produced by all
preceding events. `BeatGrid.song_segments()` already models exactly that, so
serialisation is a direct translation — and `_to_song_beat` keeps working
unchanged, because it routes through wall-clock time either way.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.models.beatmap import GeneratedBeatmap
from app.models.enums import Difficulty
from app.models.musical import BeatGrid

logger = logging.getLogger(__name__)

BEATMAP_VERSION = "3.2.0"
INFO_VERSION = "2.1.0"

DEFAULT_ENVIRONMENT = "DefaultEnvironment"
ALL_DIRECTIONS_ENVIRONMENT = "GlassDesertEnvironment"

SONG_FILENAME = "song.ogg"
COVER_FILENAME = "cover.png"
INFO_FILENAME = "Info.dat"

#: Beat values are rounded to this many decimals so files stay small and
#: identical inputs serialise byte-for-byte identically.
BEAT_PRECISION = 5


@dataclass(slots=True)
class MapMetadata:
    """Everything Info.dat needs that is not derived from the beatmap."""

    title: str
    artist: str
    mapper: str = "SaberMapper AI"
    subtitle: str = ""
    environment: str = DEFAULT_ENVIRONMENT
    preview_start: float = 12.0
    preview_duration: float = 10.0
    characteristic: str = "Standard"


def _to_song_beat(beat: float, grid: BeatGrid) -> float:
    """Convert an internal grid beat into a Beat Saber beat (beat 0 = t 0)."""
    return round(max(grid.song_beat(grid.beat_to_time(beat)), 0.0), BEAT_PRECISION)


def serialize_bpm_events(grid: BeatGrid) -> list[dict[str, Any]]:
    """Translate a variable-tempo grid into v3 `bpmEvents`.

    A constant-tempo song emits nothing: `Info.dat`'s `_beatsPerMinute` already
    says everything, and an empty array is what every loader has always seen
    from this exporter. Events are only written when the tempo genuinely moves.
    """
    if not grid.is_variable:
        return []
    return [
        {"b": round(max(segment.start_beat, 0.0), BEAT_PRECISION), "m": round(segment.bpm, 4)}
        for segment in grid.song_segments()
    ]


def serialize_difficulty(beatmap: GeneratedBeatmap, grid: BeatGrid) -> dict[str, Any]:
    """Build the v3 difficulty document."""
    colour_notes = [
        {
            "b": _to_song_beat(note.beat, grid),
            "x": int(note.x),
            "y": int(note.y),
            "c": int(note.hand.value),
            "d": int(note.direction.value),
            "a": 0,
        }
        for note in beatmap.notes
    ]
    bomb_notes = [
        {"b": _to_song_beat(bomb.beat, grid), "x": int(bomb.x), "y": int(bomb.y)}
        for bomb in beatmap.bombs
    ]
    obstacles = [
        {
            "b": _to_song_beat(obstacle.beat, grid),
            "d": round(obstacle.duration, BEAT_PRECISION),
            "x": int(obstacle.x),
            "y": int(obstacle.y),
            "w": int(obstacle.width),
            "h": int(obstacle.height),
        }
        for obstacle in beatmap.obstacles
    ]
    events = [
        {
            "b": _to_song_beat(light.beat, grid),
            "et": int(light.event_type),
            "i": int(light.value),
            "f": round(float(light.float_value), 3),
        }
        for light in beatmap.lights
    ]

    # Beat Saber requires ascending beat order in every collection.
    colour_notes.sort(key=lambda item: (item["b"], item["c"], item["x"], item["y"]))
    bomb_notes.sort(key=lambda item: (item["b"], item["x"], item["y"]))
    obstacles.sort(key=lambda item: (item["b"], item["x"]))
    events.sort(key=lambda item: (item["b"], item["et"]))

    return {
        "version": BEATMAP_VERSION,
        "bpmEvents": serialize_bpm_events(grid),
        "rotationEvents": [],
        "colorNotes": colour_notes,
        "bombNotes": bomb_notes,
        "obstacles": obstacles,
        "sliders": [],
        "burstSliders": [],
        "waypoints": [],
        "basicBeatmapEvents": events,
        "colorBoostBeatmapEvents": [],
        "lightColorEventBoxGroups": [],
        "lightRotationEventBoxGroups": [],
        "lightTranslationEventBoxGroups": [],
        "basicEventTypesWithKeywords": {"d": []},
        "useNormalEventsAsCompatibleEvents": True,
    }


def serialize_info(
    beatmaps: list[GeneratedBeatmap],
    metadata: MapMetadata,
    grid: BeatGrid,
) -> dict[str, Any]:
    """Build Info.dat.

    Takes a *list* of beatmaps so a future "generate all difficulties" run needs
    no changes here; the MVP simply passes one.
    """
    if not beatmaps:
        raise ValueError("At least one beatmap is required to build Info.dat")

    difficulty_entries = []
    for beatmap in sorted(beatmaps, key=lambda item: item.difficulty.rank):
        difficulty_entries.append(
            {
                "_difficulty": beatmap.difficulty.value,
                "_difficultyRank": beatmap.difficulty.beatsaber_rank,
                "_beatmapFilename": beatmap.difficulty.beatmap_filename,
                "_noteJumpMovementSpeed": round(beatmap.note_jump_speed, 2),
                "_noteJumpStartBeatOffset": round(beatmap.note_jump_offset, 2),
                "_customData": {
                    "_difficultyLabel": beatmap.difficulty.label,
                    "_editorOffset": 0,
                    "_editorOldOffset": 0,
                },
            }
        )

    duration = beatmaps[0].duration
    preview_start = min(max(metadata.preview_start, 0.0), max(duration - 12.0, 0.0))

    return {
        "_version": INFO_VERSION,
        "_songName": metadata.title,
        "_songSubName": metadata.subtitle,
        "_songAuthorName": metadata.artist,
        "_levelAuthorName": metadata.mapper,
        "_beatsPerMinute": round(grid.bpm, 3),
        "_shuffle": 0,
        "_shufflePeriod": 0.5,
        "_previewStartTime": round(preview_start, 2),
        "_previewDuration": round(metadata.preview_duration, 2),
        "_songFilename": SONG_FILENAME,
        "_coverImageFilename": COVER_FILENAME,
        "_environmentName": metadata.environment,
        "_allDirectionsEnvironmentName": ALL_DIRECTIONS_ENVIRONMENT,
        "_songTimeOffset": 0,
        "_customData": {
            "_editors": {
                "_lastEditedBy": "SaberMapper AI",
                "SaberMapper AI": {"version": "1.0.0"},
            }
        },
        "_difficultyBeatmapSets": [
            {
                "_beatmapCharacteristicName": metadata.characteristic,
                "_difficultyBeatmaps": difficulty_entries,
            }
        ],
    }


def dumps(document: dict[str, Any]) -> str:
    """Serialise deterministically: no whitespace surprises between runs."""
    return json.dumps(document, separators=(",", ":"), ensure_ascii=False)


def difficulty_filename(difficulty: Difficulty) -> str:
    return difficulty.beatmap_filename
