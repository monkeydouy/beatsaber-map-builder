"""Wall planning.

Walls are planned **before** notes, not filtered after them. The difference
matters more than it sounds.

Placing walls last means every wall has to be vetoed the moment any note
occupies its lane, and in a dense map that is nearly always. The result is a
handful of walls that survived by luck, sitting wherever the notes happened to
leave a gap — which is the opposite of intentional. Planning first inverts the
relationship: the wall claims its space because the music asked for it, and the
pattern generator routes the notes around the space that is left.

This module therefore produces a `WallPlan` — the obstacles plus the cell
reservations the generator must respect — rather than a bare list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from random import Random

from app.models.beatmap import GRID_COLUMNS, GRID_ROWS, Obstacle
from app.models.enums import ObstacleKind, SectionType
from app.models.musical import BeatGrid, Section
from app.services.mapping.difficulty_profiles import DifficultyProfile
from app.services.mapping.rhythm_selector import RhythmSlot

logger = logging.getLogger(__name__)

#: Wall length in beats. Now that notes route around walls rather than vetoing
#: them, a wall can be long enough to actually mean something: half a bar of
#: dodging reads as a movement cue, where a token quarter-beat wall does not.
DODGE_DURATION = 2.0
CROUCH_DURATION = 1.5

#: Notes this close to a wall's span are treated as inside it. A note cut on
#: the very frame a wall arrives is unreadable even though it is legal.
NOTE_CLEARANCE_BEATS = 0.25

#: Minimum gap between the end of one wall and the start of the next.
MIN_WALL_GAP_BEATS = 6.0

#: Never let walls occupy more than this share of the mapped song.
MAX_WALL_COVERAGE = 0.10

#: A wall needs notes around it to be worth anything; require at least this
#: many rhythm slots inside its span.
MIN_SLOTS_UNDER_WALL = 2

#: Section changes worth marking with a wall.
EMPHATIC_SECTIONS = frozenset(
    {SectionType.DROP, SectionType.CHORUS, SectionType.BUILDUP, SectionType.BRIDGE}
)


@dataclass(frozen=True, slots=True)
class WallPlan:
    """Planned walls, plus the grid reservations they imply.

    The pattern generator consults this for every note it places, so the
    queries are kept simple and allocation-free. Wall counts are small (well
    under twenty for a full song), so a linear scan beats any index.
    """

    obstacles: tuple[Obstacle, ...] = ()
    clearance: float = NOTE_CLEARANCE_BEATS

    @property
    def is_empty(self) -> bool:
        return not self.obstacles

    def _active(self, beat: float) -> list[Obstacle]:
        return [
            obstacle
            for obstacle in self.obstacles
            if obstacle.beat - self.clearance <= beat <= obstacle.end_beat + self.clearance
        ]

    def blocks(self, beat: float, x: int, y: int) -> bool:
        """True when a note at this beat and cell would sit inside a wall."""
        for obstacle in self._active(beat):
            if obstacle.covers_column(x) and obstacle.y <= y < obstacle.y + obstacle.height:
                return True
        return False

    def free_lanes(self, beat: float, y: int) -> tuple[int, ...]:
        """Columns usable on a given row at a given beat."""
        return tuple(x for x in range(GRID_COLUMNS) if not self.blocks(beat, x, y))

    def free_rows(self, beat: float, x: int, allowed: tuple[int, ...]) -> tuple[int, ...]:
        """Rows usable in a given column, restricted to the profile's rows."""
        return tuple(y for y in allowed if not self.blocks(beat, x, y))

    def dodge_direction(self, beat: float) -> int:
        """Which way the player is being pushed: -1 left, +1 right, 0 neither.

        Used to reward notes on the side the wall is herding the player toward,
        so a dodge wall reads as choreography rather than an obstacle that
        happens to be there.
        """
        for obstacle in self._active(beat):
            if obstacle.kind is ObstacleKind.DODGE_LEFT:
                return 1
            if obstacle.kind is ObstacleKind.DODGE_RIGHT:
                return -1
        return 0

    def as_list(self) -> list[Obstacle]:
        return list(self.obstacles)


@dataclass(slots=True)
class _Candidate:
    """A position a wall could occupy, before shape selection."""

    beat: float
    section: Section
    is_boundary: bool
    local_density: float = 0.0
    slot_count: int = 0
    top_row_slots: int = field(default=0)


def _candidate_positions(
    sections: list[Section], grid: BeatGrid
) -> list[_Candidate]:
    """Where a wall could plausibly go: section openings, then phrase starts."""
    candidates: list[_Candidate] = []
    for section in sections[1:]:
        candidates.append(_Candidate(section.start_beat, section, is_boundary=True))

    phrase = grid.beats_per_bar * 4  # four bars
    for section in sections:
        if section.intensity < 0.55:
            continue
        beat = section.start_beat + phrase
        while beat < section.end_beat - phrase / 2:
            candidates.append(_Candidate(beat, section, is_boundary=False))
            beat += phrase

    candidates.sort(key=lambda candidate: candidate.beat)
    return candidates


def _measure(candidate: _Candidate, slots: list[RhythmSlot], duration: float) -> None:
    """Record how much rhythm sits under a candidate wall."""
    end = candidate.beat + duration
    inside = [slot for slot in slots if candidate.beat <= slot.beat <= end]
    candidate.slot_count = len(inside)
    candidate.local_density = len(inside) / max(duration, 1e-6)


def _choose_shape(
    candidate: _Candidate,
    profile: DifficultyProfile,
    rng: Random,
) -> Obstacle | None:
    """Pick a wall shape appropriate to the difficulty and local density.

    A dodge wall costs the player one of four columns; a crouch wall costs the
    whole top row. The second is much more constraining, so it is reserved for
    difficulties that have room to give and passages loose enough to absorb it.
    """
    shapes: list[tuple[ObstacleKind, int, int, int, int, float]] = [
        # (kind, x, y, width, height, duration)
        (ObstacleKind.DODGE_LEFT, 0, 0, 1, GRID_ROWS, DODGE_DURATION),
        (ObstacleKind.DODGE_RIGHT, GRID_COLUMNS - 1, 0, 1, GRID_ROWS, DODGE_DURATION),
    ]

    # Crouch walls only where the top row is not carrying the pattern, and only
    # from Hard upward where the profile can spare the vertical space.
    crouch_allowed = (
        profile.difficulty.rank >= 2
        and 2 in profile.allowed_rows
        and candidate.section.intensity > 0.6
        and candidate.local_density < profile.target_nps_max * 0.8
    )
    if crouch_allowed:
        shapes.append(
            (ObstacleKind.CROUCH, 0, 2, GRID_COLUMNS, GRID_ROWS - 2, CROUCH_DURATION)
        )

    kind, x, y, width, height, duration = shapes[rng.randrange(len(shapes))]
    return Obstacle(
        beat=candidate.beat,
        duration=duration,
        x=x,
        y=y,
        width=width,
        height=height,
        kind=kind,
    )


def plan_obstacles(
    slots: list[RhythmSlot],
    sections: list[Section],
    grid: BeatGrid,
    profile: DifficultyProfile,
    rng: Random,
    *,
    song_duration_beats: float | None = None,
) -> WallPlan:
    """Reserve wall space from the song's structure, before notes exist.

    Takes the rhythm slots rather than notes: at this point we know *when* the
    map will ask the player to swing, which is enough to avoid putting a wall
    over silence or over a passage too dense to route around.
    """
    if profile.wall_probability <= 0 or not sections or not slots:
        return WallPlan()

    ordered_slots = sorted(slots, key=lambda slot: slot.beat)
    total_beats = song_duration_beats or (ordered_slots[-1].beat - ordered_slots[0].beat)
    if total_beats <= 0:
        return WallPlan()

    obstacles: list[Obstacle] = []
    covered_beats = 0.0
    last_end = -999.0

    for candidate in _candidate_positions(sections, grid):
        if candidate.beat < last_end + MIN_WALL_GAP_BEATS:
            continue
        if covered_beats / total_beats >= MAX_WALL_COVERAGE:
            break

        if candidate.is_boundary:
            interesting = (
                candidate.section.type in EMPHATIC_SECTIONS
                or candidate.section.intensity >= 0.55
            )
        else:
            interesting = candidate.section.intensity >= 0.6
        if not interesting:
            continue

        # Boundaries are the strongest structural cue, so they get better odds.
        # These multipliers are higher than they were when walls were filtered
        # after the fact: back then most placements were vetoed, so the gate had
        # to be generous to yield anything. Now every accepted candidate lands,
        # and the rate is what it says it is.
        chance = profile.wall_probability * (3.0 if candidate.is_boundary else 1.6)
        if rng.random() > chance:
            continue

        _measure(candidate, ordered_slots, DODGE_DURATION)
        if candidate.slot_count < MIN_SLOTS_UNDER_WALL:
            # A wall over silence is decoration nobody experiences.
            continue

        obstacle = _choose_shape(candidate, profile, rng)
        if obstacle is None:
            continue

        obstacles.append(obstacle)
        covered_beats += obstacle.duration
        last_end = obstacle.end_beat

    plan = WallPlan(obstacles=tuple(obstacles))
    logger.info(
        "obstacles_planned",
        extra={
            "count": len(obstacles),
            "difficulty": profile.difficulty.value,
            "kinds": sorted({obstacle.kind.value for obstacle in obstacles}),
            "coverage": round(covered_beats / total_beats, 4),
        },
    )
    return plan
