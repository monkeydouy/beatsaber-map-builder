"""Game-agnostic beatmap model.

The mapping engine only ever produces these objects; turning them into Beat
Saber JSON is the exporter's job. Keeping the two apart is what makes it
possible to add a v4 (or non-Beat-Saber) serialiser without touching mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.enums import (
    CutDirection,
    Difficulty,
    Hand,
    MappingStyle,
    ObstacleKind,
    Parity,
)

GRID_COLUMNS = 4
GRID_ROWS = 3


@dataclass(slots=True)
class BeatNote:
    """A single coloured note on the 4x3 grid."""

    beat: float
    hand: Hand
    x: int
    y: int
    direction: CutDirection
    source_event_index: int = -1
    confidence: float = 1.0
    #: Parity the hand is left in after this swing; filled by the parity engine.
    resulting_parity: Parity | None = None
    #: Name of the pattern that produced this note (debug output only).
    pattern: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "beat": round(self.beat, 6),
            "hand": self.hand.name,
            "x": self.x,
            "y": self.y,
            "direction": self.direction.name,
            "pattern": self.pattern,
            "confidence": round(self.confidence, 4),
        }


@dataclass(slots=True)
class Bomb:
    beat: float
    x: int
    y: int


@dataclass(slots=True)
class Obstacle:
    """A wall. ``duration`` is expressed in beats."""

    beat: float
    duration: float
    x: int
    y: int
    width: int
    height: int
    kind: ObstacleKind

    @property
    def end_beat(self) -> float:
        return self.beat + self.duration

    def covers_column(self, column: int) -> bool:
        return self.x <= column < self.x + self.width


@dataclass(slots=True)
class LightEvent:
    """A vanilla lighting event (`basicBeatmapEvents` in v3)."""

    beat: float
    event_type: int
    value: int
    float_value: float = 1.0


@dataclass(slots=True)
class MapStatistics:
    total_notes: int = 0
    left_notes: int = 0
    right_notes: int = 0
    total_walls: int = 0
    total_bombs: int = 0
    total_lights: int = 0
    average_nps: float = 0.0
    #: Notes divided by the *full song length*, which is how BeatSaver and the
    #: community report NPS. `average_nps` measures the mapped span instead,
    #: so the two differ on songs with long silent intros — and only this one
    #: can be compared against published maps.
    song_nps: float = 0.0
    peak_nps: float = 0.0
    max_local_nps: float = 0.0
    mapped_duration: float = 0.0
    doubles: int = 0
    crossovers: int = 0
    resets: int = 0
    estimated_difficulty: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_notes": self.total_notes,
            "left_notes": self.left_notes,
            "right_notes": self.right_notes,
            "total_walls": self.total_walls,
            "total_bombs": self.total_bombs,
            "total_lights": self.total_lights,
            "average_nps": round(self.average_nps, 3),
            "song_nps": round(self.song_nps, 3),
            "peak_nps": round(self.peak_nps, 3),
            "max_local_nps": round(self.max_local_nps, 3),
            "mapped_duration": round(self.mapped_duration, 2),
            "doubles": self.doubles,
            "crossovers": self.crossovers,
            "resets": self.resets,
            "estimated_difficulty": self.estimated_difficulty,
        }


@dataclass(slots=True)
class GeneratedBeatmap:
    """The complete mapping result for one difficulty."""

    difficulty: Difficulty
    style: MappingStyle
    intensity: float
    seed: int
    bpm: float
    duration: float
    notes: list[BeatNote] = field(default_factory=list)
    obstacles: list[Obstacle] = field(default_factory=list)
    bombs: list[Bomb] = field(default_factory=list)
    lights: list[LightEvent] = field(default_factory=list)
    note_jump_speed: float = 16.0
    note_jump_offset: float = 0.0
    statistics: MapStatistics = field(default_factory=MapStatistics)

    def sort(self) -> None:
        self.notes.sort(key=lambda note: (note.beat, note.hand.value, note.x, note.y))
        self.obstacles.sort(key=lambda obstacle: (obstacle.beat, obstacle.x))
        self.bombs.sort(key=lambda bomb: (bomb.beat, bomb.x, bomb.y))
        self.lights.sort(key=lambda light: (light.beat, light.event_type))
