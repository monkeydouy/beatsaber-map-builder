"""Difficulty profiles, density control and the intensity/difficulty split."""

from __future__ import annotations

import pytest

from app.models.beatmap import BeatNote
from app.models.enums import CutDirection, Difficulty, Hand
from app.models.musical import BeatGrid
from app.services.mapping.difficulty_controller import (
    DifficultyController,
    _rolling_peak,
    estimate_difficulty,
    measure_density,
)
from app.services.mapping.difficulty_profiles import (
    DIFFICULTY_DESCRIPTIONS,
    PROFILES,
    get_profile,
)


class TestProfileOrdering:
    """Each difficulty must be strictly more demanding than the one below it."""

    ORDER = list(Difficulty)

    @pytest.mark.parametrize("index", range(len(ORDER) - 1))
    def test_targets_increase(self, index):
        lower, upper = PROFILES[self.ORDER[index]], PROFILES[self.ORDER[index + 1]]
        assert upper.target_nps_min > lower.target_nps_min
        assert upper.target_nps_max > lower.target_nps_max
        assert upper.peak_nps > lower.peak_nps

    @pytest.mark.parametrize("index", range(len(ORDER) - 1))
    def test_spacing_tightens(self, index):
        lower, upper = PROFILES[self.ORDER[index]], PROFILES[self.ORDER[index + 1]]
        assert upper.min_note_interval < lower.min_note_interval

    @pytest.mark.parametrize("index", range(len(ORDER) - 1))
    def test_complexity_and_movement_increase(self, index):
        lower, upper = PROFILES[self.ORDER[index]], PROFILES[self.ORDER[index + 1]]
        assert upper.pattern_complexity > lower.pattern_complexity
        assert upper.movement_scale > lower.movement_scale
        assert upper.crossover_probability >= lower.crossover_probability

    @pytest.mark.parametrize("index", range(len(ORDER) - 1))
    def test_subdivisions_never_narrow(self, index):
        lower, upper = PROFILES[self.ORDER[index]], PROFILES[self.ORDER[index + 1]]
        assert max(upper.allowed_subdivisions) >= max(lower.allowed_subdivisions)

    def test_every_difficulty_has_a_description(self):
        for difficulty in Difficulty:
            assert DIFFICULTY_DESCRIPTIONS[difficulty].strip()

    def test_easy_stays_off_the_top_row(self):
        assert 2 not in PROFILES[Difficulty.EASY].allowed_rows

    def test_easy_may_use_half_beats_but_not_consecutively(self):
        """Real ranked Easy maps reach 2.74 NPS at p75, which whole beats alone
        cannot produce at a typical tempo. Easy therefore gets half-beats — its
        readability comes from the spacing floor, which still forbids two of
        them in a row at ordinary tempos."""
        profile = PROFILES[Difficulty.EASY]
        assert 2 in profile.allowed_subdivisions
        eighth_at_128_bpm = 60.0 / 128.0 / 2
        assert profile.min_note_interval > eighth_at_128_bpm

    def test_spacing_floor_matches_the_one_second_ceiling(self):
        """A profile whose spacing floor is slower than its own peak can never
        reach that peak, which would make the ceiling a fiction."""
        for difficulty in Difficulty:
            profile = PROFILES[difficulty]
            instantaneous = 1.0 / profile.min_note_interval
            assert instantaneous >= profile.peak_nps * 0.97, difficulty.label


class TestIntensityInterpolation:
    def test_intensity_moves_the_target_between_the_profile_bounds(self):
        profile = PROFILES[Difficulty.EXPERT]
        assert profile.interpolated_nps(0.0) == pytest.approx(profile.target_nps_min)
        assert profile.interpolated_nps(1.0) == pytest.approx(profile.target_nps_max)
        assert (
            profile.target_nps_min
            < profile.interpolated_nps(0.5)
            < profile.target_nps_max
        )

    def test_intensity_is_clamped(self):
        profile = PROFILES[Difficulty.HARD]
        assert profile.interpolated_nps(-5.0) == pytest.approx(profile.target_nps_min)
        assert profile.interpolated_nps(5.0) == pytest.approx(profile.target_nps_max)

    def test_hard_ceilings_never_move_with_intensity(self):
        """Intensity shapes the map inside a difficulty; it cannot escape it."""
        base = PROFILES[Difficulty.EXPERT]
        for intensity in (0.0, 0.5, 1.0):
            scaled = get_profile(Difficulty.EXPERT, intensity)
            assert scaled.peak_nps == base.peak_nps
            assert scaled.sustained_nps == base.sustained_nps
            assert scaled.allowed_subdivisions == base.allowed_subdivisions
            assert scaled.min_note_interval == base.min_note_interval

    def test_expert_at_full_intensity_stays_below_expert_plus(self):
        expert = PROFILES[Difficulty.EXPERT]
        expert_plus = PROFILES[Difficulty.EXPERT_PLUS]
        assert expert.interpolated_nps(1.0) <= expert_plus.interpolated_nps(1.0)
        assert expert.peak_nps < expert_plus.peak_nps

    def test_soft_knobs_scale_with_intensity(self):
        low = get_profile(Difficulty.EXPERT, 0.0)
        high = get_profile(Difficulty.EXPERT, 1.0)
        assert high.crossover_probability > low.crossover_probability
        assert high.double_probability > low.double_probability
        assert high.movement_scale > low.movement_scale
        # Higher intensity means less recovery, not more.
        assert high.recovery_seconds < low.recovery_seconds


class TestDensityMeasurement:
    GRID = BeatGrid(bpm=120.0, offset=0.0)

    @staticmethod
    def notes_at(beats):
        return [
            BeatNote(beat=beat, hand=Hand.RIGHT if index % 2 else Hand.LEFT,
                     x=1, y=1, direction=CutDirection.DOWN)
            for index, beat in enumerate(beats)
        ]

    def test_rolling_peak_measures_intervals_not_endpoints(self):
        """Three notes spanning one second is a rate of 2/s, not 3/s."""
        assert _rolling_peak([0.0, 0.5, 1.0], 1.0) == pytest.approx(2.0)

    def test_rolling_peak_of_a_single_note_is_zero(self):
        assert _rolling_peak([1.0], 1.0) == 0.0

    def test_quarter_notes_at_120bpm_measure_two_nps(self):
        # 120 BPM quarter notes are exactly 2 per second.
        beats = [float(index) for index in range(20)]
        report = measure_density(self.notes_at(beats), self.GRID, (1.0,))
        assert report.average_nps == pytest.approx(2.0, rel=0.1)
        assert report.peak_nps == pytest.approx(2.0, rel=0.05)

    def test_empty_map_reports_zero(self):
        report = measure_density([], self.GRID, (1.0, 2.0))
        assert report.average_nps == 0.0 and report.peak_nps == 0.0


class TestDifficultyController:
    GRID = BeatGrid(bpm=120.0, offset=0.0)

    def test_a_dense_burst_is_thinned_to_the_profile_ceiling(self):
        # 32 notes inside 2 seconds is far beyond any Easy map.
        notes = [
            BeatNote(beat=index * 0.125, hand=Hand.RIGHT if index % 2 else Hand.LEFT,
                     x=1, y=1, direction=CutDirection.DOWN)
            for index in range(32)
        ]
        profile = PROFILES[Difficulty.EASY]
        controller = DifficultyController(profile, self.GRID)
        kept, report = controller.apply(notes)
        assert len(kept) < len(notes)
        assert report.peak_nps <= profile.peak_nps * 1.05

    def test_a_map_already_within_profile_is_left_alone(self):
        notes = [
            BeatNote(beat=index * 2.0, hand=Hand.RIGHT if index % 2 else Hand.LEFT,
                     x=1, y=1, direction=CutDirection.DOWN)
            for index in range(20)
        ]
        controller = DifficultyController(PROFILES[Difficulty.EXPERT], self.GRID)
        kept, _report = controller.apply(notes)
        assert len(kept) == len(notes)

    def test_controller_never_adds_notes(self):
        notes = [
            BeatNote(beat=index * 0.25, hand=Hand.RIGHT if index % 2 else Hand.LEFT,
                     x=1, y=1, direction=CutDirection.DOWN)
            for index in range(60)
        ]
        for difficulty in Difficulty:
            kept, _ = DifficultyController(PROFILES[difficulty], self.GRID).apply(notes)
            assert len(kept) <= len(notes)

    def test_empty_input_is_handled(self):
        kept, report = DifficultyController(PROFILES[Difficulty.EXPERT], self.GRID).apply([])
        assert kept == [] and report.average_nps == 0.0


class TestDifficultyEstimate:
    def test_estimate_boundaries_follow_the_profiles(self):
        """The label must agree with the profile ranges, not a stale constant."""
        for difficulty in Difficulty:
            profile = PROFILES[difficulty]
            midpoint = (profile.target_nps_min + profile.target_nps_max) / 2
            report = measure_density([], BeatGrid(bpm=120.0, offset=0.0), (1.0,))
            report.average_nps = midpoint
            assert estimate_difficulty(report, []) == difficulty.label


class TestReachLadder:
    """How far from home a hand may be sent, per difficulty."""

    def test_the_easier_difficulties_stay_on_their_own_half(self):
        assert PROFILES[Difficulty.EASY].max_offset == 1
        assert PROFILES[Difficulty.NORMAL].max_offset == 1

    def test_the_limit_never_tightens_as_difficulty_rises(self):
        limits = [PROFILES[d].max_offset for d in Difficulty]
        assert limits == sorted(limits)

    def test_the_lean_gets_cheaper_as_difficulty_rises(self):
        """Hard leans occasionally, Expert+ freely — the reference ladder."""
        costs = [PROFILES[d].lean_cost for d in Difficulty if PROFILES[d].max_offset > 1]
        assert costs == sorted(costs, reverse=True)
        assert PROFILES[Difficulty.EXPERT_PLUS].lean_cost == 0.0
        assert PROFILES[Difficulty.HARD].lean_cost > PROFILES[Difficulty.EXPERT].lean_cost

    def test_intensity_cannot_move_the_limit(self):
        """It is a property of the difficulty, not of the slider.

        This used to be inferred by comparing the intensity-scaled crossover
        probability against a fixed threshold, and Normal's scaled value sits
        right on it — so whether Normal crossed the centre line depended on
        where the intensity slider happened to be.
        """
        for difficulty in Difficulty:
            base = PROFILES[difficulty]
            for intensity in (0.0, 0.5, 1.0):
                assert base.scaled(intensity).max_offset == base.max_offset
                assert base.scaled(intensity).lean_cost == base.lean_cost
