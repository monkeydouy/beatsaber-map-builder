"""Meter and subdivision detection."""

from __future__ import annotations

import numpy as np
import pytest

from app.models.enums import Difficulty
from app.services.analysis.event_detector import (
    ANALYSIS_DIVISIONS,
    TRIPLE_DIVISIONS,
    divisions_for,
)
from app.services.analysis.meter import (
    CANDIDATE_METERS,
    MeterEstimate,
    detect_meter,
    detect_subdivision,
)
from app.services.mapping.difficulty_profiles import PROFILES
from app.services.mapping.rhythm_selector import effective_subdivisions


def accent_series(pattern: list[float], bars: int, noise: float = 0.02) -> np.ndarray:
    """Repeat one bar's accent pattern, with a little jitter."""
    rng = np.random.default_rng(7)
    series = np.tile(np.array(pattern, dtype=float), bars)
    return series + rng.normal(0.0, noise, series.size)


class TestMeterDetection:
    def test_a_four_four_groove_reads_as_four(self):
        # Kick on 1 and 3, backbeat on 2 and 4.
        estimate = detect_meter(accent_series([1.0, 0.15, 0.6, 0.2], 40))
        assert estimate.beats_per_bar == 4
        assert estimate.phase == 0
        assert estimate.label == "4/4"

    def test_a_waltz_reads_as_three(self):
        """The case the old hardcoded 4 got confidently wrong."""
        estimate = detect_meter(accent_series([1.0, 0.2, 0.2], 50))
        assert estimate.beats_per_bar == 3
        assert estimate.is_triple
        assert estimate.label == "3/4"

    def test_the_downbeat_phase_is_found(self):
        # The bar starts on index 2 of the series.
        estimate = detect_meter(accent_series([0.2, 0.2, 1.0, 0.3], 40))
        assert estimate.beats_per_bar == 4
        assert estimate.phase == 2

    def test_an_unaccented_pulse_falls_back_to_four(self):
        """No structure means no evidence, and 4 is the safe default."""
        estimate = detect_meter(accent_series([0.5, 0.5, 0.5, 0.5], 40, noise=0.05))
        assert estimate.beats_per_bar == 4

    def test_too_few_beats_falls_back_to_four(self):
        estimate = detect_meter(np.array([1.0, 0.2, 0.5, 0.2]))
        assert estimate.beats_per_bar == 4
        assert estimate.confidence == 0.0

    def test_six_that_is_really_two_bars_of_three_reports_three(self):
        """A repeated 3 pattern must not be reported as a 6."""
        estimate = detect_meter(accent_series([1.0, 0.2, 0.2], 60))
        assert estimate.beats_per_bar == 3

    def test_leaving_four_requires_evidence(self):
        """A weak triple hint against a solid four keeps four."""
        estimate = detect_meter(accent_series([1.0, 0.2, 0.55, 0.25], 40))
        assert estimate.beats_per_bar == 4

    def test_confidence_is_higher_for_a_clearer_bar(self):
        strong = detect_meter(accent_series([1.0, 0.05, 0.05, 0.05], 40))
        weak = detect_meter(accent_series([1.0, 0.8, 0.85, 0.8], 40))
        assert strong.confidence > weak.confidence

    def test_every_candidate_is_scored(self):
        estimate = detect_meter(accent_series([1.0, 0.15, 0.6, 0.2], 40))
        assert set(estimate.scores) == set(CANDIDATE_METERS)

    def test_silence_does_not_crash(self):
        estimate = detect_meter(np.zeros(200))
        assert estimate.beats_per_bar == 4

    def test_triple_property_covers_compound_meters(self):
        assert MeterEstimate(3, 0, 1.0, {}).is_triple
        assert MeterEstimate(6, 0, 1.0, {}).is_triple
        assert not MeterEstimate(4, 0, 1.0, {}).is_triple
        assert not MeterEstimate(2, 0, 1.0, {}).is_triple


def onsets_at(offsets: list[float], beats: int) -> np.ndarray:
    """Onset positions in beats: `offsets` within each of `beats` beats."""
    return np.array(
        [beat + offset for beat in range(beats) for offset in offsets], dtype=float
    )


class TestSubdivisionDetection:
    def test_straight_eighths_read_as_duple(self):
        estimate = detect_subdivision(onsets_at([0.0, 0.5], 120))
        assert not estimate.triple
        assert estimate.label == "duple"

    def test_swung_eighths_read_as_triple(self):
        estimate = detect_subdivision(onsets_at([0.0, 2.0 / 3.0], 120))
        assert estimate.triple
        assert estimate.triple_share > 0.9

    def test_triplets_read_as_triple(self):
        estimate = detect_subdivision(onsets_at([0.0, 1 / 3, 2 / 3], 120))
        assert estimate.triple

    def test_sixteenths_read_as_duple(self):
        estimate = detect_subdivision(onsets_at([0.0, 0.25, 0.5, 0.75], 120))
        assert not estimate.triple

    def test_material_with_no_offbeats_defaults_to_duple(self):
        estimate = detect_subdivision(onsets_at([0.0], 120))
        assert not estimate.triple
        assert estimate.confidence == 0.0

    def test_scattered_offbeats_are_not_mistaken_for_triple(self):
        """Noise that leans slightly toward a third is still noise.

        This is the failure the absolute floor exists to prevent: without it,
        offbeats landing on nothing in particular get called triple whenever a
        few more of them happen to sit near a third than near a half.
        """
        rng = np.random.default_rng(3)
        positions = np.arange(300) + rng.uniform(0.1, 0.9, 300)
        estimate = detect_subdivision(positions)
        assert not estimate.triple

    def test_too_few_onsets_defaults_to_duple(self):
        assert not detect_subdivision(np.array([0.0, 0.5, 1.0])).triple


class TestSubdivisionLadders:
    def test_duple_music_uses_the_profile_as_written(self):
        for difficulty in Difficulty:
            allowed = PROFILES[difficulty].allowed_subdivisions
            assert effective_subdivisions(allowed, False) == allowed

    def test_triple_music_reads_halves_as_thirds(self):
        assert effective_subdivisions((1, 2), True) == (1, 3)
        assert effective_subdivisions((1, 2, 4), True) == (1, 3, 6)

    def test_easy_reads_its_half_beats_as_thirds_in_a_shuffle(self):
        allowed = PROFILES[Difficulty.EASY].allowed_subdivisions
        assert effective_subdivisions(allowed, True) == (1, 3)

    def test_the_ladder_never_loses_resolution(self):
        """Translating must not leave a difficulty with fewer choices."""
        for difficulty in Difficulty:
            allowed = PROFILES[difficulty].allowed_subdivisions
            assert len(effective_subdivisions(allowed, True)) >= 1

    def test_the_snapping_ladder_follows_the_subdivision(self):
        assert divisions_for(False) == ANALYSIS_DIVISIONS
        assert divisions_for(True) == TRIPLE_DIVISIONS
        # Whichever ladder, the whole beat is always tried first.
        assert divisions_for(False)[0] == 1
        assert divisions_for(True)[0] == 1


@pytest.mark.slow
class TestAgainstRealAudio:
    def test_the_straight_four_four_fixture(self, analysis):
        assert analysis.grid.beats_per_bar == 4
        assert not analysis.grid.triple_subdivision

    def test_the_waltz_is_detected_as_three(self, waltz_raw):
        assert waltz_raw.meter.beats_per_bar == 3
        assert waltz_raw.grid.beats_per_bar == 3
        assert waltz_raw.meter.confidence > 0.5

    def test_the_shuffle_keeps_four_but_divides_in_three(self, swing_raw):
        """Bar length and beat subdivision are independent questions."""
        assert swing_raw.meter.beats_per_bar == 4
        assert swing_raw.subdivision.triple
        assert swing_raw.grid.triple_subdivision

    def test_compound_time_is_recognised_as_triple(self, six_eight_raw):
        """6/8 commonly reads as 3 — both group the eighths correctly.

        What matters is that it is not forced onto 4, which would rotate the
        accent through the bar.
        """
        assert six_eight_raw.meter.is_triple
        assert six_eight_raw.meter.beats_per_bar in (3, 6)

    def test_downbeats_land_on_the_same_bar_position_every_time(
        self, waltz_raw, swing_raw, six_eight_raw, analysis
    ):
        """The property the whole feature exists to provide."""
        from app.services.analysis.event_detector import build_events
        from app.services.analysis.section_analyzer import analyze_sections

        for raw in (waltz_raw, swing_raw, six_eight_raw):
            sections = analyze_sections(raw, raw.grid)
            events = build_events(raw, raw.grid, sections)
            downbeats = [event for event in events if event.is_downbeat]
            assert len(downbeats) > 20
            phases = {
                round(event.quantized_beat % raw.grid.beats_per_bar, 3)
                for event in downbeats
            }
            assert phases == {0.0}, f"downbeats scattered across {phases}"

        assert {round(event.bar_phase, 3) for event in analysis.events if event.is_downbeat} == {
            0.0
        }

    def test_a_waltz_forced_onto_four_scatters_its_downbeats(self, waltz_raw):
        """The regression this fixes, stated as a test.

        With a hardcoded 4, a third of the detected downbeats land on beats 2
        and 3 of the real bar — the accent rotates through the music.
        """
        from app.services.analysis.event_detector import build_events
        from app.services.analysis.section_analyzer import analyze_sections

        original = waltz_raw.grid.beats_per_bar
        try:
            waltz_raw.grid.beats_per_bar = 4
            sections = analyze_sections(waltz_raw, waltz_raw.grid)
            events = build_events(waltz_raw, waltz_raw.grid, sections)
            downbeats = [event for event in events if event.is_downbeat]
            on_beat_one = sum(
                1 for event in downbeats if round(event.quantized_beat) % 3 == 0
            )
            assert on_beat_one / len(downbeats) < 0.8, (
                "forcing 4/4 on a waltz should misplace downbeats"
            )
        finally:
            waltz_raw.grid.beats_per_bar = original

    def test_the_shuffle_snaps_its_offbeats_to_thirds(self, swing_raw):
        from app.services.analysis.event_detector import build_events
        from app.services.analysis.section_analyzer import analyze_sections

        sections = analyze_sections(swing_raw, swing_raw.grid)
        events = build_events(swing_raw, swing_raw.grid, sections)
        divisions = [event.division for event in events]
        # Thirds must dominate the offbeat material, not halves.
        assert divisions.count(3) > divisions.count(2) * 4

    def test_every_meter_still_produces_a_valid_map(
        self, waltz_raw, swing_raw, six_eight_raw
    ):
        from app.models.musical import AnalysisResult
        from app.services.analysis.event_detector import build_events
        from app.services.analysis.section_analyzer import analyze_sections
        from app.services.mapping.pipeline import GenerationOptions, generate_beatmap

        for raw in (waltz_raw, swing_raw, six_eight_raw):
            sections = analyze_sections(raw, raw.grid)
            events = build_events(raw, raw.grid, sections)
            analysis = AnalysisResult(
                duration=raw.duration, sample_rate=raw.sample_rate, grid=raw.grid,
                beats=raw.beats.tolist(),
                beat_confidence=raw.beat_confidence.tolist(),
                downbeats=raw.downbeats.tolist(), onsets=raw.onsets.tolist(),
                onset_strengths=raw.onset_strengths.tolist(), sections=sections,
                events=events, features=raw.to_features(),
            )
            result = generate_beatmap(
                analysis, GenerationOptions(difficulty=Difficulty.EXPERT, seed=11)
            )
            assert result.validation.ok, result.validation.errors
            assert result.beatmap.statistics.total_notes > 100
