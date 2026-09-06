"""Variable-tempo grids, and the `bpmEvents` that carry them into the game."""

from __future__ import annotations

import numpy as np
import pytest

from app.models.beatmap import BeatNote, GeneratedBeatmap
from app.models.enums import CutDirection, Difficulty, Hand, MappingStyle
from app.models.musical import BeatGrid, TempoSegment
from app.services.analysis.tempo import (
    DRIFT_IMPROVEMENT_FACTOR,
    build_tempo_segments,
    fit_beat_grid,
    grid_residual,
    local_tempo_curve,
    prepare_envelope,
    tempo_spread,
)
from app.services.export.beatsaber_exporter import (
    MapMetadata,
    serialize_bpm_events,
    serialize_difficulty,
    serialize_info,
)

# ---------------------------------------------------------------------------
# An independent model of what Beat Saber does with `bpmEvents`.
# ---------------------------------------------------------------------------


def beat_saber_time(beat: float, info_bpm: float, bpm_events: list[dict]) -> float:
    """Re-derive wall-clock time from an exported map, the way the game does.

    This is deliberately written from the format's semantics rather than from
    our own `BeatGrid`, so that a mistake in the exporter cannot hide behind
    the same mistake in the checker. `bpmEvents` redefine the beat axis from
    each event onward: a beat value is measured in the space produced by every
    preceding event.
    """
    events = sorted(bpm_events, key=lambda event: event["b"])
    if not events or events[0]["b"] > 0:
        events = [{"b": 0.0, "m": info_bpm}, *events]

    # (start_beat, start_time, bpm)
    segments: list[tuple[float, float, float]] = [(0.0, 0.0, events[0]["m"])]
    for event in events[1:]:
        start_beat, start_time, bpm = segments[-1]
        segments.append(
            (event["b"], start_time + (event["b"] - start_beat) * 60.0 / bpm, event["m"])
        )

    chosen = segments[0]
    for segment in segments:
        if segment[0] <= beat:
            chosen = segment
        else:
            break
    return chosen[1] + (beat - chosen[0]) * 60.0 / chosen[2]


VARIABLE = BeatGrid(
    segments=[
        TempoSegment(start_time=0.5, start_beat=0.0, bpm=120.0),
        TempoSegment(start_time=30.5, start_beat=60.0, bpm=133.0),
        TempoSegment(start_time=60.5, start_beat=126.5, bpm=126.0),
    ],
    duration=120.0,
)


class TestBeatGridPiecewise:
    def test_a_single_tempo_behaves_exactly_as_before(self):
        grid = BeatGrid(bpm=128.0, offset=0.25)
        assert not grid.is_variable
        assert grid.bpm == 128.0
        assert grid.representative_bpm == 128.0
        assert grid.seconds_per_beat == pytest.approx(60.0 / 128.0)
        assert grid.beat_to_time(4) == pytest.approx(0.25 + 4 * 60 / 128)

    def test_conversions_round_trip_across_tempo_changes(self):
        for beat in (0.0, 30.0, 60.0, 61.0, 126.5, 200.0):
            assert VARIABLE.time_to_beat(VARIABLE.beat_to_time(beat)) == pytest.approx(beat)

    def test_each_segment_uses_its_own_tempo(self):
        # One beat inside the first segment lasts 60/120 s.
        assert VARIABLE.beat_to_time(1) - VARIABLE.beat_to_time(0) == pytest.approx(0.5)
        # One beat inside the second lasts 60/133 s.
        assert VARIABLE.beat_to_time(61) - VARIABLE.beat_to_time(60) == pytest.approx(
            60.0 / 133.0
        )

    def test_base_tempo_is_the_first_segment(self):
        assert VARIABLE.bpm == 120.0

    def test_representative_tempo_is_duration_weighted(self):
        # 30 s at 120, 30 s at 133, 59.5 s at 126.
        assert 120.0 < VARIABLE.representative_bpm < 133.0
        assert VARIABLE.representative_bpm == pytest.approx(126.6, abs=1.0)

    def test_bpm_at_reports_the_local_tempo(self):
        assert VARIABLE.bpm_at(10.0) == 120.0
        assert VARIABLE.bpm_at(40.0) == 133.0
        assert VARIABLE.bpm_at(100.0) == 126.0

    def test_times_before_the_first_beat_extrapolate_backwards(self):
        assert VARIABLE.time_to_beat(0.0) == pytest.approx(-1.0)

    def test_serialisation_round_trips(self):
        restored = BeatGrid.from_dict(VARIABLE.to_dict())
        assert restored.segments == VARIABLE.segments
        assert restored.is_variable

    def test_legacy_payloads_without_segments_still_load(self):
        """Analyses cached before variable tempo existed must keep working."""
        restored = BeatGrid.from_dict({"bpm": 128.0, "offset": 0.25, "beats_per_bar": 4})
        assert not restored.is_variable
        assert restored.bpm == 128.0


class TestSongCoordinates:
    def test_song_beat_counts_from_time_zero(self):
        grid = BeatGrid(bpm=120.0, offset=0.5)
        assert grid.song_beat(0.0) == pytest.approx(0.0)
        assert grid.song_beat(grid.beat_to_time(0)) == pytest.approx(1.0)

    def test_song_segments_extend_the_first_tempo_back_to_the_origin(self):
        segments = VARIABLE.song_segments()
        assert segments[0].start_time == 0.0
        assert segments[0].start_beat == 0.0
        assert segments[0].bpm == 120.0

    def test_song_segments_preserve_the_tempo_change_times(self):
        segments = VARIABLE.song_segments()
        assert [round(s.start_time, 4) for s in segments] == [0.0, 30.5, 60.5]
        assert [s.bpm for s in segments] == [120.0, 133.0, 126.0]


class TestBpmEventExport:
    def test_constant_songs_emit_no_events(self):
        """Unchanged output for the overwhelmingly common case."""
        assert serialize_bpm_events(BeatGrid(bpm=128.0, offset=0.25)) == []

    def test_variable_songs_emit_one_event_per_segment(self):
        events = serialize_bpm_events(VARIABLE)
        assert len(events) == 3
        assert events[0]["b"] == 0.0
        assert [event["m"] for event in events] == [120.0, 133.0, 126.0]

    def test_events_are_ordered_and_non_negative(self):
        events = serialize_bpm_events(VARIABLE)
        beats = [event["b"] for event in events]
        assert beats == sorted(beats)
        assert all(beat >= 0 for beat in beats)

    def test_the_difficulty_document_carries_the_events(self):
        beatmap = _beatmap([BeatNote(beat=0.0, hand=Hand.LEFT, x=1, y=1,
                                     direction=CutDirection.DOWN)])
        document = serialize_difficulty(beatmap, VARIABLE)
        assert len(document["bpmEvents"]) == 3


class TestExportMatchesTheGame:
    """The load-bearing test: does our export mean what we think it means?"""

    @pytest.mark.parametrize("beat", [0.0, 1.0, 30.0, 60.0, 90.0, 126.5, 180.0])
    def test_exported_notes_land_at_the_intended_wall_clock_time(self, beat):
        beatmap = _beatmap(
            [BeatNote(beat=beat, hand=Hand.LEFT, x=1, y=1, direction=CutDirection.DOWN)]
        )
        document = serialize_difficulty(beatmap, VARIABLE)
        info = serialize_info([beatmap], MapMetadata(title="T", artist="A"), VARIABLE)

        exported_beat = document["colorNotes"][0]["b"]
        replayed = beat_saber_time(
            exported_beat, info["_beatsPerMinute"], document["bpmEvents"]
        )
        assert replayed == pytest.approx(VARIABLE.beat_to_time(beat), abs=0.002)

    def test_the_same_holds_for_a_constant_tempo_map(self):
        grid = BeatGrid(bpm=128.0, offset=0.25)
        for beat in (0.0, 8.0, 64.0, 250.0):
            beatmap = _beatmap(
                [BeatNote(beat=beat, hand=Hand.LEFT, x=1, y=1,
                          direction=CutDirection.DOWN)]
            )
            document = serialize_difficulty(beatmap, grid)
            info = serialize_info([beatmap], MapMetadata(title="T", artist="A"), grid)
            replayed = beat_saber_time(
                document["colorNotes"][0]["b"], info["_beatsPerMinute"],
                document["bpmEvents"],
            )
            assert replayed == pytest.approx(grid.beat_to_time(beat), abs=0.002)

    def test_obstacles_and_lights_use_the_same_axis(self):
        from app.models.beatmap import LightEvent, Obstacle
        from app.models.enums import ObstacleKind

        beatmap = _beatmap(
            [BeatNote(beat=90.0, hand=Hand.LEFT, x=1, y=1,
                      direction=CutDirection.DOWN)]
        )
        beatmap.obstacles = [
            Obstacle(beat=90.0, duration=2.0, x=0, y=0, width=1, height=3,
                     kind=ObstacleKind.DODGE_LEFT)
        ]
        beatmap.lights = [LightEvent(beat=90.0, event_type=4, value=5)]
        document = serialize_difficulty(beatmap, VARIABLE)
        assert document["colorNotes"][0]["b"] == document["obstacles"][0]["b"]
        assert document["colorNotes"][0]["b"] == document["basicBeatmapEvents"][0]["b"]


def _beatmap(notes: list[BeatNote]) -> GeneratedBeatmap:
    return GeneratedBeatmap(
        difficulty=Difficulty.EXPERT,
        style=MappingStyle.BALANCED,
        intensity=0.6,
        seed=1,
        bpm=VARIABLE.bpm,
        duration=120.0,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _beats_from_plan(plan: list[tuple[float, float]], count: int) -> np.ndarray:
    """Synthesise beat times from ``(start_beat, bpm)`` segments."""
    times = []
    now = 0.0
    for index in range(count):
        bpm = plan[0][1]
        for start_beat, segment_bpm in plan:
            if index >= start_beat:
                bpm = segment_bpm
        times.append(now)
        now += 60.0 / bpm
    return np.array(times)


class TestLocalTempoCurve:
    def test_a_steady_beat_train_reads_as_steady(self):
        beats = _beats_from_plan([(0, 128.0)], 200)
        _times, bpms = local_tempo_curve(beats)
        assert bpms.size > 0
        assert tempo_spread(bpms) < 0.005
        assert np.median(bpms) == pytest.approx(128.0, abs=0.2)

    def test_a_drifting_beat_train_reads_as_drifting(self):
        beats = _beats_from_plan([(0, 120.0), (100, 132.0)], 220)
        _times, bpms = local_tempo_curve(beats)
        assert tempo_spread(bpms) > 0.05

    def test_a_dropped_beat_does_not_look_like_a_tempo_change(self):
        """A missed beat reports one double-length interval, not a halving."""
        beats = list(_beats_from_plan([(0, 128.0)], 200))
        del beats[80]  # tracker misses one
        _times, bpms = local_tempo_curve(np.array(beats))
        assert tempo_spread(bpms) < 0.02
        assert np.median(bpms) == pytest.approx(128.0, abs=0.5)

    def test_too_few_beats_yields_nothing(self):
        assert local_tempo_curve(np.array([0.0, 0.5]))[0].size == 0


class TestGridFitting:
    @staticmethod
    def _envelope(beats: np.ndarray, duration: float):
        """A synthetic onset envelope with energy on each beat."""
        frame_times = np.arange(0.0, duration, 512 / 44100)
        envelope = np.zeros_like(frame_times)
        for beat in beats:
            index = int(np.searchsorted(frame_times, beat))
            if 0 <= index < envelope.size:
                envelope[index] = 1.0
        return prepare_envelope(envelope), frame_times

    def test_a_steady_song_stays_constant(self):
        beats = _beats_from_plan([(0, 128.0)], 300)
        envelope, frame_times = self._envelope(beats, float(beats[-1]) + 2)
        grid, report = fit_beat_grid(
            beats, envelope, frame_times, 128.0, 0.0, float(beats[-1]) + 2
        )
        assert not grid.is_variable
        assert report["segments"] == 1

    def test_variable_tempo_can_be_disabled(self):
        beats = _beats_from_plan([(0, 120.0), (96, 133.0)], 300)
        envelope, frame_times = self._envelope(beats, float(beats[-1]) + 2)
        grid, _report = fit_beat_grid(
            beats, envelope, frame_times, 120.0, 0.0, float(beats[-1]) + 2,
            allow_variable=False,
        )
        assert not grid.is_variable

    def test_a_drifting_song_is_fitted_piecewise(self):
        beats = _beats_from_plan([(0, 120.0), (96, 133.0)], 320)
        envelope, frame_times = self._envelope(beats, float(beats[-1]) + 2)
        grid, report = fit_beat_grid(
            beats, envelope, frame_times, 120.0, 0.0, float(beats[-1]) + 2
        )
        assert grid.is_variable, report
        assert len(grid.segments) >= 2
        assert grid.segments[0].bpm == pytest.approx(120.0, abs=1.0)
        assert grid.segments[-1].bpm == pytest.approx(133.0, abs=1.5)

    def test_the_piecewise_fit_must_earn_its_place(self):
        """A variable grid is only accepted when it measurably fits better."""
        beats = _beats_from_plan([(0, 128.0)], 300)
        envelope, frame_times = self._envelope(beats, float(beats[-1]) + 2)
        constant = BeatGrid(bpm=128.0, offset=float(beats[0]))
        assert grid_residual(constant, beats) < 0.02
        grid, _report = fit_beat_grid(
            beats, envelope, frame_times, 128.0, float(beats[0]), float(beats[-1]) + 2
        )
        assert not grid.is_variable

    def test_tempo_changes_land_on_bar_lines(self):
        beats = _beats_from_plan([(0, 120.0), (96, 133.0)], 320)
        envelope, frame_times = self._envelope(beats, float(beats[-1]) + 2)
        segments = build_tempo_segments(
            beats, envelope, frame_times, 120.0, 0.0, float(beats[-1]) + 2, 4
        )
        for segment in segments[1:]:
            assert segment.start_beat % 4 == 0, "tempo change is not bar-aligned"

    def test_segments_never_go_backwards(self):
        beats = _beats_from_plan([(0, 118.0), (80, 130.0), (200, 122.0)], 340)
        envelope, frame_times = self._envelope(beats, float(beats[-1]) + 2)
        segments = build_tempo_segments(
            beats, envelope, frame_times, 118.0, 0.0, float(beats[-1]) + 2, 4
        )
        for earlier, later in zip(segments, segments[1:]):
            assert later.start_time > earlier.start_time
            assert later.start_beat > earlier.start_beat

    def test_improvement_factor_is_a_real_gate(self):
        assert 0.0 < DRIFT_IMPROVEMENT_FACTOR < 1.0


# ---------------------------------------------------------------------------
# Against real audio
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestAgainstRealAudio:
    """The synthetic beat trains above prove the maths; these prove the DSP."""

    def test_a_steady_recording_is_not_mistaken_for_a_drifting_one(self, analysis):
        grid = analysis.grid
        assert not grid.is_variable
        assert grid.bpm == pytest.approx(128.0, abs=0.5)

    def test_a_drifting_recording_is_fitted_piecewise(self, drifting_raw):
        grid = drifting_raw.grid
        assert grid.is_variable, "tempo drift went undetected"
        assert len(grid.segments) == 3

    def test_the_detected_tempos_match_the_performance(self, drifting_raw):
        from tests.conftest import DRIFT_TRUTH

        for segment, (_beat, expected_bpm) in zip(drifting_raw.grid.segments, DRIFT_TRUTH):
            assert segment.bpm == pytest.approx(expected_bpm, abs=1.0)

    def test_the_tempo_changes_land_on_the_right_bars(self, drifting_raw):
        from tests.conftest import DRIFT_TRUTH

        for segment, (expected_beat, _bpm) in zip(drifting_raw.grid.segments, DRIFT_TRUTH):
            assert segment.start_beat == pytest.approx(expected_beat, abs=4.0)

    def test_the_variable_grid_tracks_the_beats_far_better(self, drifting_raw):
        """The whole justification, measured: a constant grid drifts off."""
        raw = drifting_raw
        constant = BeatGrid(
            bpm=raw.grid.representative_bpm, offset=raw.grid.offset, duration=raw.duration
        )
        assert grid_residual(raw.grid, raw.beats) < grid_residual(constant, raw.beats) * 0.5

    def test_disabling_the_feature_forces_a_single_tempo(self, drifting_raw_constant):
        grid = drifting_raw_constant.grid
        assert not grid.is_variable
        assert serialize_bpm_events(grid) == []

    def test_a_full_map_generated_from_a_drifting_song_is_valid(self, drifting_raw):
        """End to end: drifting audio in, playable variable-tempo map out."""
        from app.models.musical import AnalysisResult
        from app.services.analysis.event_detector import build_events
        from app.services.analysis.section_analyzer import analyze_sections
        from app.services.mapping.pipeline import GenerationOptions, generate_beatmap

        raw = drifting_raw
        sections = analyze_sections(raw, raw.grid)
        events = build_events(raw, raw.grid, sections)
        analysis = AnalysisResult(
            duration=raw.duration, sample_rate=raw.sample_rate, grid=raw.grid,
            beats=raw.beats.tolist(), beat_confidence=raw.beat_confidence.tolist(),
            downbeats=raw.downbeats.tolist(), onsets=raw.onsets.tolist(),
            onset_strengths=raw.onset_strengths.tolist(), sections=sections,
            events=events, features=raw.to_features(),
        )
        result = generate_beatmap(
            analysis, GenerationOptions(difficulty=Difficulty.EXPERT, seed=7)
        )
        assert result.validation.ok, result.validation.errors
        assert result.beatmap.statistics.total_notes > 100

        document = serialize_difficulty(result.beatmap, raw.grid)
        assert len(document["bpmEvents"]) == len(raw.grid.segments)

        # Every exported note must replay at the time the mapper intended.
        info = serialize_info(
            [result.beatmap], MapMetadata(title="T", artist="A"), raw.grid
        )
        for note, source in zip(document["colorNotes"][:80], result.beatmap.notes[:80]):
            replayed = beat_saber_time(
                note["b"], info["_beatsPerMinute"], document["bpmEvents"]
            )
            assert replayed == pytest.approx(raw.grid.beat_to_time(source.beat), abs=0.01)
