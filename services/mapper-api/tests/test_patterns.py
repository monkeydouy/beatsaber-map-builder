"""Structural checks on the pattern library.

Patterns are laid over runs with the hands alternating, so one hand takes
steps 0, 2, 4... and the other takes 1, 3, 5... A definition written
`DOWN, UP, DOWN, UP` reads perfectly as a list and asks one hand for
`DOWN, DOWN, DOWN` — a motion an arm cannot make without an uncounted return
stroke between every note.

Three patterns shipped with exactly that defect. The vertical ones were mostly
rejected at scoring time because the forehand/backhand rule caught them, but
`HorizontalFlow` asked the left hand to cut RIGHT over and over; horizontals
were treated as parity-neutral, so nothing objected and it became one of the
most-used patterns in the library.

These tests read every definition the same way the generator does.
"""

from __future__ import annotations

import pytest

from app.models.enums import (
    DIRECTION_ANGLES,
    HORIZONTAL_CUTS,
    CutDirection,
    Difficulty,
    Hand,
    MappingStyle,
    Parity,
)
from app.services.mapping.parity_engine import (
    OPPOSING_SWING_ANGLE,
    HandState,
    angle_delta,
    chase_distance,
    opposes,
)
from app.services.mapping.pattern_library import (
    BOTH,
    PATTERNS,
    PATTERNS_BY_NAME,
    eligible_patterns,
)


def hand_subsequences(pattern) -> list[list[CutDirection]]:
    """The direction each hand actually sees, in order, for one pass."""
    result = []
    for offset in (0, 1):
        directions = [step.direction for step in pattern.steps[offset::2]]
        if len(directions) >= 2 and all(d is not None for d in directions):
            result.append(directions)
    return result


ALTERNATING = [
    pattern
    for pattern in PATTERNS
    if pattern.length >= 3 and not any(step.role == BOTH for step in pattern.steps)
]


class TestEachHandCanActuallyPlayIt:
    @pytest.mark.parametrize("pattern", ALTERNATING, ids=lambda p: p.name)
    def test_consecutive_swings_for_one_hand_oppose(self, pattern):
        for sequence in hand_subsequences(pattern):
            for earlier, later in zip(sequence, sequence[1:]):
                assert opposes(earlier, later), (
                    f"{pattern.name}: one hand is asked for {earlier.name} then "
                    f"{later.name} ({angle_delta(earlier, later):.0f} degrees apart)"
                )

    @pytest.mark.parametrize("pattern", ALTERNATING, ids=lambda p: p.name)
    def test_it_still_works_when_the_pattern_repeats(self, pattern):
        """Patterns tile over long runs, so the wrap-around is a real join."""
        for sequence in hand_subsequences(pattern):
            assert opposes(sequence[-1], sequence[0]), (
                f"{pattern.name}: repeating it asks one hand for "
                f"{sequence[-1].name} then {sequence[0].name}"
            )

    @pytest.mark.parametrize("pattern", ALTERNATING, ids=lambda p: p.name)
    def test_no_hand_repeats_a_direction_outright(self, pattern):
        for sequence in hand_subsequences(pattern):
            ring = [*sequence, sequence[0]]
            for earlier, later in zip(ring, ring[1:]):
                assert earlier is not later, f"{pattern.name} repeats {earlier.name}"


class TestKnownRegressions:
    """The three that shipped broken, named so they cannot come back."""

    @pytest.mark.parametrize("name", ["HorizontalFlow", "VerticalFlow", "TowerTap"])
    def test_the_patterns_that_were_unplayable(self, name):
        pattern = PATTERNS_BY_NAME[name]
        for sequence in hand_subsequences(pattern):
            ring = [*sequence, sequence[0]]
            worst = min(angle_delta(a, b) for a, b in zip(ring, ring[1:]))
            assert worst >= OPPOSING_SWING_ANGLE, f"{name} is back to {worst:.0f} degrees"

    def test_horizontal_flow_alternates_each_hand_across_the_body(self):
        sequences = hand_subsequences(PATTERNS_BY_NAME["HorizontalFlow"])
        for sequence in sequences:
            assert set(sequence) == {CutDirection.LEFT, CutDirection.RIGHT}


class TestLibraryIntegrity:
    def test_every_pattern_has_steps_and_a_name(self):
        names = [pattern.name for pattern in PATTERNS]
        assert len(names) == len(set(names))
        for pattern in PATTERNS:
            assert pattern.steps and pattern.name

    def test_explicit_directions_are_real_directions(self):
        for pattern in PATTERNS:
            for step in pattern.steps:
                for direction in (step.direction, step.partner_direction):
                    if direction is not None:
                        assert direction in DIRECTION_ANGLES or direction is CutDirection.ANY

    def test_offsets_and_rows_stay_on_the_grid(self):
        for pattern in PATTERNS:
            for step in pattern.steps:
                for offset in (step.offset, step.partner_offset):
                    assert offset is None or 0 <= offset <= 3, pattern.name
                for row in (step.row, step.partner_row):
                    assert row is None or 0 <= row <= 2, pattern.name

    def test_a_full_cross_body_reach_stays_exceptional(self):
        """CROSS sends a hand all the way to the far lane.

        Human maps put red in the rightmost column for well under 2% of notes,
        so the vocabulary should barely contain it — an offset the library
        reaches for freely is one the scorer spends its budget rejecting.
        """
        offsets = [
            step.offset
            for pattern in PATTERNS
            for step in pattern.steps
            if step.offset is not None
        ]
        assert offsets.count(3) / len(offsets) < 0.08

    def test_the_vocabulary_reaches_across_the_centre_line(self):
        """LEAN is the offset human mappers use about a quarter of the time.

        Before columns were hand-relative the library could barely express it:
        a lean for one hand implied a lean for the other, both hands crossed at
        once, and the scorer threw the candidate away. Red played lane 2 in 5%
        of our notes against 24% in the reference maps.
        """
        offsets = [
            step.offset
            for pattern in PATTERNS
            for step in pattern.steps
            if step.offset is not None
        ]
        assert offsets.count(2) / len(offsets) > 0.15

    def test_every_difficulty_has_something_to_play(self):
        for difficulty in Difficulty:
            from app.services.mapping.difficulty_profiles import PROFILES

            pool = eligible_patterns(difficulty, PROFILES[difficulty].pattern_complexity)
            assert len(pool) >= 3, difficulty.label

    def test_every_style_has_something_it_prefers(self):
        for style in MappingStyle:
            preferred = [p for p in PATTERNS if p.affinity(style) > 1.0]
            assert preferred or style is MappingStyle.BALANCED


class TestTheScorerCanOverrulePatternDirections:
    """A pattern's declared cut is a preference, not a mandate.

    Patterns write their directions down in advance and cannot know what the
    hand was doing when the phrase before them ended. Held as absolute, that
    seam handed the player right-angle turns at speed for no musical reason.
    """

    @staticmethod
    def generator():
        from random import Random

        from app.models.musical import BeatGrid
        from app.services.mapping.config import MappingConfig, ScoringWeights
        from app.services.mapping.difficulty_profiles import PROFILES
        from app.services.mapping.pattern_generator import PatternGenerator

        return PatternGenerator(
            profile=PROFILES[Difficulty.EXPERT],
            config=MappingConfig(),
            weights=ScoringWeights(),
            style=MappingStyle.BALANCED,
            intensity=0.6,
            grid=BeatGrid(bpm=128.0, offset=0.0),
            sections=[],
            rng=Random(7),
        )

    @staticmethod
    def slot(beat: float):
        from app.models.enums import MusicalEventType
        from app.models.musical import MusicalEvent
        from app.services.mapping.rhythm_selector import RhythmSlot

        event = MusicalEvent(
            timestamp=beat / 2.0,
            beat=beat,
            strength=0.6,
            confidence=0.8,
            section_index=0,
            event_type=MusicalEventType.BEAT,
        )
        return RhythmSlot(
            beat=beat,
            time=beat / 2.0,
            event=event,
            importance=0.6,
            section_index=0,
        )

    def test_a_playable_direction_is_kept(self):
        generator = self.generator()
        state = HandState(Hand.RIGHT, parity=Parity.BACKHAND, last_beat=0.0,
                          last_x=2, last_y=1, last_direction=CutDirection.DOWN)
        kept = generator._honour_or_replace(
            CutDirection.UP, state, self.slot(0.5), 2, 1
        )
        assert kept is CutDirection.UP

    def test_a_right_angle_turn_at_speed_is_overruled(self):
        generator = self.generator()
        state = HandState(Hand.RIGHT, parity=Parity.FOREHAND, last_beat=0.0,
                          last_x=2, last_y=1, last_direction=CutDirection.UP_RIGHT)
        chosen = generator._honour_or_replace(
            CutDirection.DOWN_RIGHT, state, self.slot(0.25), 2, 1
        )
        assert chosen is not CutDirection.DOWN_RIGHT
        assert angle_delta(CutDirection.UP_RIGHT, chosen) >= 135.0

    def test_the_same_turn_is_kept_when_there_is_time_for_it(self):
        generator = self.generator()
        state = HandState(Hand.RIGHT, parity=Parity.FOREHAND, last_beat=0.0,
                          last_x=2, last_y=1, last_direction=CutDirection.UP_RIGHT)
        kept = generator._honour_or_replace(
            CutDirection.DOWN_RIGHT, state, self.slot(2.0), 2, 1
        )
        assert kept is CutDirection.DOWN_RIGHT

    def test_a_diagonal_is_never_replaced_by_a_flick(self):
        # Substituting like for like is what keeps a pattern recognisable.
        generator = self.generator()
        state = HandState(Hand.RIGHT, parity=Parity.FOREHAND, last_beat=0.0,
                          last_x=2, last_y=1, last_direction=CutDirection.UP_RIGHT)
        chosen = generator._honour_or_replace(
            CutDirection.DOWN_RIGHT, state, self.slot(0.25), 2, 1
        )
        assert chosen not in HORIZONTAL_CUTS


class TestTheScorerCanOverrulePatternPositions:
    """A pattern's declared cell is a preference too, on the same terms."""

    generator = staticmethod(TestTheScorerCanOverrulePatternDirections.generator)
    slot = staticmethod(TestTheScorerCanOverrulePatternDirections.slot)

    @staticmethod
    def swung_down_to_the_floor():
        # Hand finishes low, so a block placed lower still is unreachable
        # without stopping and coming back for it.
        return HandState(Hand.RIGHT, parity=Parity.BACKHAND, last_beat=0.0,
                         last_x=2, last_y=2, last_direction=CutDirection.DOWN)

    def test_a_cell_off_the_swing_path_is_left_alone(self):
        generator = self.generator()
        state = self.swung_down_to_the_floor()
        assert generator._cell_off_the_swing_path(
            2, 2, Hand.RIGHT, state, self.slot(0.25), (0, 1, 2)
        ) == (2, 2)

    def test_a_cell_along_the_swing_is_moved(self):
        generator = self.generator()
        state = self.swung_down_to_the_floor()
        moved = generator._cell_off_the_swing_path(
            2, 0, Hand.RIGHT, state, self.slot(0.25), (0, 1, 2)
        )
        assert moved != (2, 0)
        assert chase_distance(state, *moved) == 0.0

    def test_the_pattern_survives_when_there_is_time_to_reposition(self):
        # Given a beat to get there the player just moves; the pattern's own
        # shape is worth more than the correction.
        generator = self.generator()
        state = self.swung_down_to_the_floor()
        assert generator._cell_off_the_swing_path(
            2, 0, Hand.RIGHT, state, self.slot(1.5), (0, 1, 2)
        ) == (2, 0)

    def test_the_note_moves_as_little_as_it_can(self):
        generator = self.generator()
        state = self.swung_down_to_the_floor()
        moved = generator._cell_off_the_swing_path(
            2, 0, Hand.RIGHT, state, self.slot(0.25), (0, 1, 2)
        )
        assert abs(moved[0] - 2) + abs(moved[1] - 0) <= 2


class TestOffsetsAreHandRelative:
    """A step's column belongs to the hand that plays it, not to the grid."""

    @staticmethod
    def generator():
        return TestTheScorerCanOverrulePatternDirections.generator()

    def test_the_same_offset_lands_on_opposite_sides(self):
        generator = self.generator()
        left = generator._resolve_lane(0, Hand.LEFT, HandState(Hand.LEFT))
        right = generator._resolve_lane(0, Hand.RIGHT, HandState(Hand.RIGHT))
        assert (left, right) == (0, 3)

    def test_leaning_crosses_the_centre_line_for_either_hand(self):
        generator = self.generator()
        assert generator._resolve_lane(2, Hand.LEFT, HandState(Hand.LEFT)) == 2
        assert generator._resolve_lane(2, Hand.RIGHT, HandState(Hand.RIGHT)) == 1

    def test_one_hand_can_lean_while_the_other_stays_home(self):
        """The shape the absolute-column library could not hold.

        Written as columns, a lean for one hand arrived paired with a lean for
        the other and both crossed at once. As offsets the two sides are simply
        independent.
        """
        generator = self.generator()
        leaning = generator._resolve_lane(2, Hand.LEFT, HandState(Hand.LEFT))
        holding = generator._resolve_lane(0, Hand.RIGHT, HandState(Hand.RIGHT))
        assert leaning == 2 and holding == 3
        assert leaning < holding, "hands must not end up crossed over each other"

    def test_the_easier_difficulties_keep_hands_on_their_own_half(self):
        from random import Random

        from app.models.enums import MappingStyle
        from app.models.musical import BeatGrid
        from app.services.mapping.config import MappingConfig, ScoringWeights
        from app.services.mapping.difficulty_profiles import PROFILES
        from app.services.mapping.pattern_generator import PatternGenerator

        generator = PatternGenerator(
            profile=PROFILES[Difficulty.EASY],
            config=MappingConfig(),
            weights=ScoringWeights(),
            style=MappingStyle.BALANCED,
            intensity=0.6,
            grid=BeatGrid(bpm=128.0, offset=0.0),
            sections=[],
            rng=Random(3),
        )
        for offset in range(4):
            assert generator._resolve_lane(offset, Hand.LEFT, HandState(Hand.LEFT)) <= 1
            assert generator._resolve_lane(offset, Hand.RIGHT, HandState(Hand.RIGHT)) >= 2


class TestTheCrossoverPatternIsShapedLikeAGesture:
    """`CrossAndReturn` is the vocabulary the far lane needs.

    Without it nothing ever proposed a full cross-body reach, so making the
    charge affordable changed nothing: the scorer can only pick from what the
    library offers.
    """

    @staticmethod
    def pattern():
        return PATTERNS_BY_NAME["CrossAndReturn"]

    def test_one_hand_crosses_and_the_other_holds_home(self):
        steps = self.pattern().steps
        reaching = [step.offset for step in steps[0::2]]
        holding = [step.offset for step in steps[1::2]]
        assert 3 in reaching, "no hand actually reaches across"
        assert holding == [0] * len(holding), "the other hand must stay home"

    def test_the_reach_is_a_single_note(self):
        # Every visit in the reference maps lasts exactly one note.
        reaching = [step.offset for step in self.pattern().steps[0::2]]
        assert reaching.count(3) == 1

    def test_the_hand_comes_from_home_and_goes_back(self):
        reaching = [step.offset for step in self.pattern().steps[0::2]]
        crossed = reaching.index(3)
        assert crossed > 0 and reaching[crossed - 1] == 0, "no setup"
        assert crossed < len(reaching) - 1 and reaching[crossed + 1] == 0, "no escape"

    def test_it_is_kept_away_from_the_easier_difficulties(self):
        assert self.pattern().min_difficulty.rank >= Difficulty.HARD.rank

    def test_it_asks_for_recovery_room(self):
        assert self.pattern().required_recovery >= 0.5
