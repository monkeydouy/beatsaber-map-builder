"""Difficulty profiles measured against real community-ranked maps.

The profiles used to be reasoned rather than fitted, and the numbers below are
the evidence that they no longer are. `tests/fixtures/ranked_corpus.json` holds
aggregate statistics for 716 ranked, human-made, vanilla Standard maps pulled
from BeatSaver's public metadata (see `tools/fetch_ranked_corpus.py`); these
tests assert the profiles still agree with it.

If a profile is retuned and one of these fails, that is the point: the change
has moved a difficulty away from what the label means to players, and either
the profile or the corpus needs revisiting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.enums import DIFFICULTY_ORDER, Difficulty
from app.services.mapping.difficulty_profiles import PROFILES

CORPUS_PATH = Path(__file__).parent / "fixtures" / "ranked_corpus.json"


@pytest.fixture(scope="module")
def corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def reference(corpus) -> dict:
    return corpus["difficulties"]


class TestCorpusIntegrity:
    def test_the_corpus_is_committed_and_substantial(self, corpus):
        assert corpus["map_count"] >= 300
        assert corpus["row_count"] >= 1000

    def test_it_covers_every_difficulty(self, reference):
        for difficulty in DIFFICULTY_ORDER:
            assert difficulty.value in reference
            assert reference[difficulty.value]["count"] >= 50

    def test_it_records_what_it_filtered_for(self, corpus):
        """Provenance matters: these numbers justify the profiles."""
        assert "beatsaver" in corpus["source"].lower()
        for term in ("ranked", "Standard"):
            assert term.lower() in corpus["filter"].lower()

    def test_real_maps_get_denser_with_difficulty(self, reference):
        """A sanity check on the corpus itself before trusting it."""
        previous = 0.0
        for difficulty in DIFFICULTY_ORDER:
            median = reference[difficulty.value]["nps"]["p50"]
            assert median > previous, difficulty.value
            previous = median


class TestProfilesMatchTheCorpus:
    @pytest.mark.parametrize("difficulty", list(Difficulty))
    def test_the_target_band_is_the_real_interquartile_range(
        self, difficulty, reference
    ):
        """Default settings should produce a map of ordinary density for its
        label, not one at the bottom edge of the distribution."""
        profile = PROFILES[difficulty]
        nps = reference[difficulty.value]["nps"]
        assert profile.target_nps_min == pytest.approx(nps["p25"], abs=0.25)
        assert profile.target_nps_max == pytest.approx(nps["p75"], abs=0.25)

    @pytest.mark.parametrize("difficulty", list(Difficulty))
    def test_note_jump_speed_matches_what_mappers_choose(self, difficulty, reference):
        profile = PROFILES[difficulty]
        njs = reference[difficulty.value]["njs"]
        assert njs["p25"] <= profile.base_njs <= njs["p75"]

    @pytest.mark.parametrize("difficulty", list(Difficulty))
    def test_the_default_intensity_lands_mid_distribution(self, difficulty, reference):
        """Intensity 0.6 is the default, so it should not sit in a tail."""
        nps = reference[difficulty.value]["nps"]
        effective = PROFILES[difficulty].interpolated_nps(0.6)
        assert nps["p25"] <= effective <= nps["p95"]

    @pytest.mark.parametrize("difficulty", list(Difficulty))
    def test_the_ceiling_leaves_room_for_real_peaks(self, difficulty, reference):
        """`peak_nps` is a one-second ceiling, so it must clear the whole-map
        average of even the densest maps carrying that label."""
        nps = reference[difficulty.value]["nps"]
        assert PROFILES[difficulty].peak_nps > nps["p75"]


class TestProfileCoherence:
    """Internal consistency, which the calibration is what surfaced."""

    @pytest.mark.parametrize("difficulty", list(Difficulty))
    def test_the_spacing_floor_permits_the_peak(self, difficulty):
        profile = PROFILES[difficulty]
        assert 1.0 / profile.min_note_interval >= profile.peak_nps * 0.97

    @pytest.mark.parametrize("difficulty", list(Difficulty))
    def test_the_subdivisions_permit_the_target(self, difficulty):
        """A difficulty whose finest subdivision cannot reach its own target
        density at an ordinary tempo has a target it can never meet."""
        profile = PROFILES[difficulty]
        beats_per_second = 128.0 / 60.0
        reachable = beats_per_second * max(profile.allowed_subdivisions)
        assert reachable >= profile.target_nps_max

    @pytest.mark.parametrize("difficulty", list(Difficulty))
    def test_ceilings_are_ordered_within_a_profile(self, difficulty):
        profile = PROFILES[difficulty]
        assert profile.target_nps_min < profile.target_nps_max
        assert profile.target_nps_max < profile.sustained_nps
        assert profile.sustained_nps < profile.peak_nps


@pytest.mark.slow
class TestGeneratedMapsMatchTheCorpus:
    """The end of the chain: does what we actually emit look like a real map?"""

    @pytest.fixture(scope="class")
    def dense_analysis(self, dense_wav):
        """A source with enough going on that the profile, not the music, is
        what limits density. On sparse material the top difficulties are
        content-bound and cannot reach their targets however they are tuned."""
        from app.models.musical import AnalysisResult
        from app.services.analysis.audio_analyzer import analyze_audio
        from app.services.analysis.event_detector import build_events
        from app.services.analysis.section_analyzer import analyze_sections

        raw = analyze_audio(dense_wav)
        sections = analyze_sections(raw, raw.grid)
        events = build_events(raw, raw.grid, sections)
        return AnalysisResult(
            duration=raw.duration, sample_rate=raw.sample_rate, grid=raw.grid,
            beats=raw.beats.tolist(), beat_confidence=raw.beat_confidence.tolist(),
            downbeats=raw.downbeats.tolist(), onsets=raw.onsets.tolist(),
            onset_strengths=raw.onset_strengths.tolist(), sections=sections,
            events=events, features=raw.to_features(),
        )

    @staticmethod
    def _generate(analysis, difficulty):
        from app.services.mapping.pipeline import GenerationOptions, generate_beatmap

        return generate_beatmap(
            analysis,
            GenerationOptions(difficulty=difficulty, intensity=0.6, seed=1234),
        )

    def test_the_dense_source_is_not_the_limiting_factor(self, dense_analysis):
        assert len(dense_analysis.events) / dense_analysis.duration > 7.0

    @pytest.mark.parametrize("difficulty", list(Difficulty))
    def test_generated_density_lands_in_the_real_band(
        self, difficulty, dense_analysis, reference
    ):
        result = self._generate(dense_analysis, difficulty)
        nps = reference[difficulty.value]["nps"]
        produced = result.beatmap.statistics.song_nps
        assert nps["p25"] <= produced <= nps["p75"], (
            f"{difficulty.label}: {produced:.2f} NPS is outside the real "
            f"{nps['p25']:.2f}-{nps['p75']:.2f} band"
        )

    @pytest.mark.parametrize("difficulty", list(Difficulty))
    def test_generated_note_jump_speed_lands_in_the_real_band(
        self, difficulty, dense_analysis, reference
    ):
        result = self._generate(dense_analysis, difficulty)
        njs = reference[difficulty.value]["njs"]
        assert njs["p5"] <= result.beatmap.note_jump_speed <= njs["p95"]

    def test_song_nps_is_measured_the_way_the_corpus_measures_it(
        self, dense_analysis
    ):
        """Comparing against published maps only works if the metric matches:
        notes over the *whole song*, not over the mapped span."""
        result = self._generate(dense_analysis, Difficulty.EXPERT)
        stats = result.beatmap.statistics
        expected = stats.total_notes / dense_analysis.duration
        assert stats.song_nps == pytest.approx(expected, rel=1e-6)
        # The mapped span is shorter than the song, so it reads higher.
        assert stats.average_nps >= stats.song_nps
