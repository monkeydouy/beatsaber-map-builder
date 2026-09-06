"""Beat/time conversion, tempo sanity and quantisation."""

from __future__ import annotations

import numpy as np
import pytest

from app.models.musical import BeatGrid
from app.services.analysis.event_detector import ANALYSIS_DIVISIONS, _snap
from app.services.analysis.tempo import (
    estimate_tempo,
    fold_score,
    prepare_envelope,
    refine_tempo,
    snap_round_tempo,
)


class TestBeatGrid:
    def test_time_to_beat_is_inverse_of_beat_to_time(self):
        grid = BeatGrid(bpm=128.0, offset=0.25)
        for beat in (0.0, 1.0, 7.5, 128.25):
            assert grid.time_to_beat(grid.beat_to_time(beat)) == pytest.approx(beat)

    def test_beat_spacing_matches_bpm(self):
        grid = BeatGrid(bpm=150.0, offset=0.0)
        assert grid.beat_to_time(1) - grid.beat_to_time(0) == pytest.approx(60.0 / 150.0)

    def test_song_beat_counts_from_time_zero(self):
        """Beat Saber counts beat 0 at t=0, regardless of our grid offset."""
        grid = BeatGrid(bpm=120.0, offset=0.5)
        # Grid beat 0 sits at t=0.5s, which is half a beat into the song.
        assert grid.song_beat(grid.beat_to_time(0)) == pytest.approx(1.0)

    def test_seconds_per_beat(self):
        assert BeatGrid(bpm=120.0, offset=0.0).seconds_per_beat == pytest.approx(0.5)


class TestQuantization:
    def test_on_beat_events_snap_to_whole_beats(self):
        snapped, division, error = _snap(4.02, ANALYSIS_DIVISIONS)
        assert snapped == pytest.approx(4.0)
        assert division == 1
        assert error < 0.03

    def test_eighth_note_snaps_to_half_beat(self):
        snapped, division, _ = _snap(4.51, ANALYSIS_DIVISIONS)
        assert snapped == pytest.approx(4.5)
        assert division == 2

    def test_sixteenth_note_snaps_to_quarter_beat(self):
        snapped, division, _ = _snap(4.26, ANALYSIS_DIVISIONS)
        assert snapped == pytest.approx(4.25)
        assert division == 4

    def test_coarse_divisions_are_preferred(self):
        """An on-beat event must not be recorded as a fine subdivision."""
        _snapped, division, _ = _snap(8.0, ANALYSIS_DIVISIONS)
        assert division == 1

    def test_duple_is_preferred_over_triplet_when_both_fit(self):
        # 0.25 is exactly a sixteenth and only roughly a triplet.
        _snapped, division, _ = _snap(0.25, ANALYSIS_DIVISIONS)
        assert division == 4


class TestTempoSanity:
    """A synthetic click train has an unambiguous tempo; detection must find it."""

    @staticmethod
    def _click_envelope(bpm: float, duration: float = 60.0, sr_frames: float = 86.13):
        frames = int(duration * sr_frames)
        times = np.arange(frames) / sr_frames
        envelope = np.zeros(frames)
        period = 60.0 / bpm
        for index in range(int(duration / period)):
            frame = int(index * period * sr_frames)
            if frame < frames:
                envelope[frame] = 1.0
                # Accent every fourth beat, like a real bar.
                if index % 4 == 0:
                    envelope[frame] = 1.6
        return envelope, times

    def test_fold_score_peaks_at_the_true_tempo(self):
        envelope, times = self._click_envelope(128.0)
        envelope = prepare_envelope(envelope)
        true_contrast, _ = fold_score(128.0, envelope, times)
        for wrong in (117.0, 135.0, 96.0):
            other, _ = fold_score(wrong, envelope, times)
            assert true_contrast > other

    def test_refine_recovers_exact_tempo_from_a_binned_estimate(self):
        """The core half/double-time-adjacent problem: 129.2 must become 128."""
        envelope, times = self._click_envelope(128.0)
        envelope = prepare_envelope(envelope)
        refined, _offset, _contrast = refine_tempo(129.199, envelope, times)
        assert refined == pytest.approx(128.0, abs=0.2)

    def test_half_time_estimate_is_corrected_upward(self):
        envelope, times = self._click_envelope(128.0)
        best, _ranked = estimate_tempo(envelope, times, 60.0, [64.0])
        assert best.bpm == pytest.approx(128.0, abs=1.0)

    def test_double_time_estimate_is_corrected_downward(self):
        envelope, times = self._click_envelope(120.0)
        best, _ranked = estimate_tempo(envelope, times, 60.0, [240.0])
        assert best.bpm == pytest.approx(120.0, abs=1.5)

    def test_candidates_stay_in_a_musical_range(self):
        envelope, times = self._click_envelope(128.0)
        best, ranked = estimate_tempo(envelope, times, 60.0, [128.0])
        assert 60.0 <= best.bpm <= 220.0
        assert all(60.0 <= candidate.bpm <= 220.0 for candidate in ranked)

    def test_empty_envelope_falls_back_safely(self):
        best, _ranked = estimate_tempo(np.array([]), np.array([]), 0.0, [])
        assert best.bpm > 0


class TestRoundTempoSnapping:
    def test_snaps_to_a_whole_bpm(self):
        assert snap_round_tempo(127.97) == 128.0

    def test_leaves_genuinely_odd_tempos_alone(self):
        """A live or tape-transferred track must not be rounded off."""
        assert snap_round_tempo(127.4) == pytest.approx(127.4)
        assert snap_round_tempo(140.5) == pytest.approx(140.5)

    def test_every_tempo_has_an_unsnapped_neighbourhood(self):
        """With a 0.25 tolerance, mid-way tempos must survive untouched."""
        for bpm in (100.5, 128.45, 174.5):
            assert snap_round_tempo(bpm) == pytest.approx(bpm)


class TestNoNegativeBeats:
    """Grid beat 0 is the first detected beat, so earlier onsets are negative.

    A single transient before the downbeat used to snap to a negative beat,
    which the validator rejects — failing the whole map over two notes in the
    first half-second.
    """

    @staticmethod
    def _events(bpm: float, offset: float, onset_times: list[float]):
        import numpy as np

        from app.models.enums import SectionType
        from app.models.musical import BeatGrid, Section
        from app.services.analysis.audio_analyzer import RawAnalysis
        from app.services.analysis.event_detector import build_events
        from app.services.analysis.tempo import TempoCandidate

        grid = BeatGrid(bpm=bpm, offset=offset, duration=30.0)
        frames = np.arange(0.0, 30.0, 512 / 44100)
        ones = np.ones_like(frames)
        onsets = np.array(onset_times)
        raw = RawAnalysis(
            duration=30.0, sample_rate=44100, hop_length=512, grid=grid,
            beats=np.arange(offset, 30.0, 60.0 / bpm),
            beat_confidence=np.ones(10), downbeats=np.array([offset]),
            onsets=onsets, onset_strengths=np.ones_like(onsets),
            frame_times=frames, onset_envelope=ones * 0.5,
            band_envelopes=np.vstack([ones * 0.4, ones * 0.3, ones * 0.2]),
            rms=ones * 0.5, spectral_centroid=ones * 2000,
            spectral_bandwidth=ones * 1000, percussive_energy=ones * 0.5,
            harmonic_energy=ones * 0.2, chroma=np.zeros((12, frames.size)),
            mfcc=np.zeros((13, frames.size)),
            tempo_candidates=[TempoCandidate(bpm, offset, 1.0, 1.0, 1.0, 1.0)],
        )
        sections = [
            Section(index=0, start=0.0, end=30.0, start_beat=0.0,
                    end_beat=grid.time_to_beat(30.0),
                    type=SectionType.VERSE, intensity=0.5)
        ]
        return build_events(raw, grid, sections)

    @pytest.mark.parametrize(
        "bpm,offset", [(110.0, 0.50), (128.0, 0.40), (90.0, 0.60), (174.0, 0.30)]
    )
    def test_onsets_before_the_first_beat_are_dropped(self, bpm, offset):
        events = self._events(bpm, offset, [0.0, 0.05, 0.15, 0.30, 0.45, 1.0, 2.0, 3.0])
        assert events, "everything was discarded"
        assert all(event.quantized_beat >= 0.0 for event in events)

    def test_ordinary_onsets_are_still_kept(self):
        events = self._events(128.0, 0.05, [0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
        assert len(events) >= 5

    def test_a_generated_map_never_holds_a_negative_beat(self):
        """The property the validator checks, asserted at the source."""
        from app.models.beatmap import BeatNote
        from app.models.enums import CutDirection, Hand

        events = self._events(110.0, 0.5, [0.1, 0.3, 0.45, 1.0, 1.6, 2.2, 2.7])
        notes = [
            BeatNote(beat=event.quantized_beat, hand=Hand.LEFT, x=1, y=1,
                     direction=CutDirection.DOWN)
            for event in events
        ]
        assert all(note.beat >= 0 for note in notes)
