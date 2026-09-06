"""Wall planning and the reservations it hands the pattern generator."""

from __future__ import annotations

from dataclasses import replace
from random import Random

from app.models.beatmap import GRID_COLUMNS, GRID_ROWS, Obstacle
from app.models.enums import (
    Difficulty,
    Hand,
    MappingStyle,
    MusicalEventType,
    ObstacleKind,
    SectionType,
)
from app.models.musical import BeatGrid, MusicalEvent, Section
from app.services.mapping.config import MappingConfig, ScoringWeights
from app.services.mapping.difficulty_profiles import PROFILES, get_profile
from app.services.mapping.obstacle_generator import WallPlan, plan_obstacles
from app.services.mapping.pattern_generator import PatternGenerator
from app.services.mapping.rhythm_selector import RhythmSlot

GRID = BeatGrid(bpm=120.0, offset=0.0)


def dodge(beat: float, x: int, duration: float = 2.0) -> Obstacle:
    kind = ObstacleKind.DODGE_LEFT if x == 0 else ObstacleKind.DODGE_RIGHT
    return Obstacle(
        beat=beat, duration=duration, x=x, y=0, width=1, height=GRID_ROWS, kind=kind
    )


def crouch(beat: float, duration: float = 1.5) -> Obstacle:
    return Obstacle(
        beat=beat, duration=duration, x=0, y=2, width=GRID_COLUMNS, height=1,
        kind=ObstacleKind.CROUCH,
    )


class TestWallPlanQueries:
    def test_empty_plan_blocks_nothing(self):
        plan = WallPlan()
        assert plan.is_empty
        assert not plan.blocks(4.0, 0, 0)
        assert plan.free_lanes(4.0, 0) == (0, 1, 2, 3)

    def test_dodge_wall_blocks_its_whole_column(self):
        plan = WallPlan(obstacles=(dodge(4.0, 0),))
        for row in range(GRID_ROWS):
            assert plan.blocks(5.0, 0, row)
        assert not plan.blocks(5.0, 1, 0)
        assert plan.free_lanes(5.0, 0) == (1, 2, 3)

    def test_crouch_wall_blocks_only_the_top_row(self):
        plan = WallPlan(obstacles=(crouch(4.0),))
        for column in range(GRID_COLUMNS):
            assert plan.blocks(4.5, column, 2)
            assert not plan.blocks(4.5, column, 0)
            assert not plan.blocks(4.5, column, 1)
        assert plan.free_rows(4.5, 1, (0, 1, 2)) == (0, 1)

    def test_walls_only_block_during_their_span(self):
        plan = WallPlan(obstacles=(dodge(4.0, 0, duration=2.0),))
        assert not plan.blocks(1.0, 0, 0)
        assert plan.blocks(4.0, 0, 0)
        assert plan.blocks(6.0, 0, 0)
        assert not plan.blocks(20.0, 0, 0)

    def test_clearance_extends_the_blocked_window(self):
        """A note cut on the frame a wall arrives is legal but unreadable."""
        plan = WallPlan(obstacles=(dodge(4.0, 0, duration=2.0),), clearance=0.25)
        assert plan.blocks(3.9, 0, 0)
        assert plan.blocks(6.2, 0, 0)
        assert not plan.blocks(3.5, 0, 0)

    def test_dodge_direction_points_away_from_the_wall(self):
        assert WallPlan(obstacles=(dodge(4.0, 0),)).dodge_direction(5.0) == 1
        assert WallPlan(obstacles=(dodge(4.0, 3),)).dodge_direction(5.0) == -1
        assert WallPlan(obstacles=(crouch(4.0),)).dodge_direction(4.5) == 0
        assert WallPlan(obstacles=(dodge(4.0, 0),)).dodge_direction(50.0) == 0

    def test_overlapping_walls_combine_their_reservations(self):
        plan = WallPlan(obstacles=(dodge(4.0, 0), crouch(4.0)))
        assert plan.blocks(4.5, 0, 1)  # dodge column
        assert plan.blocks(4.5, 2, 2)  # crouch row
        assert not plan.blocks(4.5, 2, 0)
        # Something must always remain reachable.
        assert plan.free_lanes(4.5, 0) == (1, 2, 3)


def _slots(count: int, spacing: float = 0.5) -> list[RhythmSlot]:
    slots = []
    for index in range(count):
        beat = index * spacing
        event = MusicalEvent(
            timestamp=beat * 0.5, beat=beat, strength=0.7, confidence=1.0,
            section_index=0, event_type=MusicalEventType.KICK, quantized_beat=beat,
        )
        slots.append(
            RhythmSlot(beat=beat, time=beat * 0.5, event=event, importance=0.7, section_index=0)
        )
    return slots


def _sections(count: int = 4, span: float = 40.0) -> list[Section]:
    return [
        Section(
            index=index, start=index * span, end=(index + 1) * span,
            start_beat=index * span * 2, end_beat=(index + 1) * span * 2,
            type=SectionType.DROP, intensity=0.85,
        )
        for index in range(count)
    ]


class TestPlanning:
    def test_returns_an_empty_plan_when_walls_are_disabled(self):
        # slots=True dataclasses have no __dict__, so replace() is the way in.
        disabled = replace(PROFILES[Difficulty.EASY], wall_probability=0.0)
        plan = plan_obstacles(_slots(200), _sections(), GRID, disabled, Random(1))
        assert plan.is_empty

    def test_returns_an_empty_plan_without_rhythm(self):
        plan = plan_obstacles([], _sections(), GRID, PROFILES[Difficulty.EXPERT], Random(1))
        assert plan.is_empty

    def test_places_walls_for_a_dense_song(self):
        plan = plan_obstacles(
            _slots(600, 0.5), _sections(6), GRID, PROFILES[Difficulty.EXPERT], Random(3)
        )
        assert len(plan.obstacles) > 0

    def test_planned_walls_never_overlap(self):
        for seed in range(12):
            plan = plan_obstacles(
                _slots(600, 0.5), _sections(6), GRID,
                PROFILES[Difficulty.EXPERT_PLUS], Random(seed),
            )
            obstacles = sorted(plan.obstacles, key=lambda item: item.beat)
            for earlier, later in zip(obstacles, obstacles[1:]):
                assert later.beat >= earlier.end_beat

    def test_geometry_is_always_on_the_grid(self):
        for seed in range(12):
            plan = plan_obstacles(
                _slots(600, 0.5), _sections(6), GRID,
                PROFILES[Difficulty.EXPERT], Random(seed),
            )
            for obstacle in plan.obstacles:
                assert 0 <= obstacle.x < GRID_COLUMNS
                assert obstacle.x + obstacle.width <= GRID_COLUMNS
                assert obstacle.duration > 0 and obstacle.width > 0 and obstacle.height > 0

    def test_a_reachable_cell_always_remains(self):
        """However walls stack, the generator must never be boxed in."""
        for seed in range(12):
            plan = plan_obstacles(
                _slots(600, 0.5), _sections(6), GRID,
                PROFILES[Difficulty.EXPERT_PLUS], Random(seed),
            )
            for obstacle in plan.obstacles:
                for beat in (obstacle.beat, (obstacle.beat + obstacle.end_beat) / 2):
                    free = [
                        (x, y)
                        for x in range(GRID_COLUMNS)
                        for y in range(GRID_ROWS)
                        if not plan.blocks(beat, x, y)
                    ]
                    assert len(free) >= 4

    def test_easy_never_gets_crouch_walls(self):
        """Crouch walls cost the top row, which Easy does not have to give."""
        for seed in range(20):
            plan = plan_obstacles(
                _slots(600, 0.5), _sections(6), GRID,
                get_profile(Difficulty.EASY, 1.0), Random(seed),
            )
            assert all(o.kind is not ObstacleKind.CROUCH for o in plan.obstacles)

    def test_planning_is_deterministic(self):
        args = (_slots(600, 0.5), _sections(6), GRID, PROFILES[Difficulty.EXPERT])
        first = plan_obstacles(*args, Random(99))
        second = plan_obstacles(*args, Random(99))
        assert [o.beat for o in first.obstacles] == [o.beat for o in second.obstacles]
        assert [o.kind for o in first.obstacles] == [o.kind for o in second.obstacles]

    def test_coverage_stays_bounded(self):
        sections = _sections(8)
        plan = plan_obstacles(
            _slots(900, 0.5), sections, GRID, PROFILES[Difficulty.EXPERT_PLUS], Random(5),
            song_duration_beats=sections[-1].end_beat,
        )
        covered = sum(o.duration for o in plan.obstacles)
        assert covered / sections[-1].end_beat <= 0.11

    def test_walls_are_not_placed_over_silence(self):
        """Rhythm stops at beat 20; nothing after it should carry a wall."""
        plan = plan_obstacles(
            _slots(40, 0.5), _sections(6), GRID, PROFILES[Difficulty.EXPERT], Random(2)
        )
        assert all(o.beat <= 22.0 for o in plan.obstacles)


class TestGeneratorRouting:
    """The generator must relocate out of reserved cells, not give up."""

    @staticmethod
    def _generator(walls: WallPlan):
        return PatternGenerator(
            profile=PROFILES[Difficulty.EXPERT],
            config=MappingConfig(),
            weights=ScoringWeights(),
            style=MappingStyle.BALANCED,
            intensity=0.6,
            grid=GRID,
            sections=_sections(1),
            rng=Random(1),
            walls=walls,
        )

    def test_a_free_cell_is_left_alone(self):
        generator = self._generator(WallPlan())
        assert generator._clear_cell(4.0, 2, 1, Hand.RIGHT, (0, 1, 2)) == (2, 1)

    def test_a_walled_cell_moves_sideways_first(self):
        """Vertical shape carries more of a pattern's character than column."""
        generator = self._generator(WallPlan(obstacles=(dodge(4.0, 0),)))
        placed = generator._clear_cell(4.5, 0, 1, Hand.LEFT, (0, 1, 2))
        assert placed is not None
        x, y = placed
        assert x != 0
        assert y == 1

    def test_a_crouch_wall_moves_the_note_down(self):
        generator = self._generator(WallPlan(obstacles=(crouch(4.0),)))
        placed = generator._clear_cell(4.5, 2, 2, Hand.RIGHT, (0, 1, 2))
        assert placed is not None
        assert placed[1] < 2

    def test_relocation_prefers_the_hands_own_side(self):
        generator = self._generator(WallPlan(obstacles=(dodge(4.0, 0),)))
        placed = generator._clear_cell(4.5, 0, 1, Hand.LEFT, (0, 1, 2))
        assert placed == (1, 1)

    def test_gives_up_only_when_nothing_is_reachable(self):
        walls = WallPlan(
            obstacles=(
                dodge(4.0, 0), dodge(4.0, 1), dodge(4.0, 2), dodge(4.0, 3),
            )
        )
        generator = self._generator(walls)
        assert generator._clear_cell(4.5, 1, 1, Hand.LEFT, (0, 1, 2)) is None
