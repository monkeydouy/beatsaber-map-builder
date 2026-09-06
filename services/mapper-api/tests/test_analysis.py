"""Analysis over the real audio fixture: tempo, structure and events."""

from __future__ import annotations

import pytest

from app.models.enums import MusicalEventType, SectionType
from tests.conftest import FIXTURE_BPM, FIXTURE_DURATION

pytestmark = pytest.mark.slow


class TestTempoDetection:
    def test_finds_the_fixture_tempo_exactly(self, analysis):
        """The fixture is synthesised at 128 BPM; detection must land on it."""
        assert analysis.bpm == pytest.approx(FIXTURE_BPM, abs=0.5)

    def test_reports_high_confidence_on_unambiguous_material(self, analysis):
        assert analysis.grid.confidence > 0.6

    def test_the_grid_does_not_drift_across_the_song(self, analysis):
        """A 1% tempo error would put the end of the map a second out."""
        grid = analysis.grid
        last_beat = grid.time_to_beat(FIXTURE_DURATION - 1.0)
        true_time = last_beat * 60.0 / FIXTURE_BPM + grid.offset
        assert abs(grid.beat_to_time(last_beat) - true_time) < 0.05

    def test_offset_is_within_one_beat(self, analysis):
        assert 0 <= analysis.grid.offset < 60.0 / analysis.bpm

    def test_beats_are_evenly_spaced(self, analysis):
        intervals = [
            later - earlier
            for earlier, later in zip(analysis.beats, analysis.beats[1:])
        ]
        expected = 60.0 / FIXTURE_BPM
        assert sum(intervals) / len(intervals) == pytest.approx(expected, rel=0.05)

    def test_reports_alternative_tempo_candidates(self, analysis):
        assert len(analysis.tempo_candidates) >= 2


class TestStructure:
    def test_finds_several_sections(self, analysis):
        assert 3 <= len(analysis.sections) <= 14

    def test_sections_tile_the_whole_song_without_gaps(self, analysis):
        sections = analysis.sections
        assert sections[0].start == pytest.approx(0.0)
        assert sections[-1].end == pytest.approx(analysis.duration, abs=1.0)
        for earlier, later in zip(sections, sections[1:]):
            assert earlier.end == pytest.approx(later.start)

    def test_every_section_has_a_known_type_and_bounded_intensity(self, analysis):
        for section in analysis.sections:
            assert isinstance(section.type, SectionType)
            assert 0.0 <= section.intensity <= 1.0

    def test_intensity_tracks_the_fixture_arrangement(self, analysis):
        """The fixture's quiet intro must not outrank its loud drop."""
        first = analysis.sections[0]
        loudest = max(analysis.sections, key=lambda s: s.intensity)
        assert loudest.intensity > first.intensity

    def test_section_beats_agree_with_the_grid(self, analysis):
        for section in analysis.sections:
            assert section.start_beat == pytest.approx(
                analysis.grid.time_to_beat(section.start), abs=0.01
            )


class TestEventTimeline:
    def test_produces_a_dense_event_timeline(self, analysis):
        assert len(analysis.events) > 200

    def test_events_are_ordered(self, analysis):
        beats = [event.quantized_beat for event in analysis.events]
        assert beats == sorted(beats)

    def test_events_stay_inside_the_song(self, analysis):
        for event in analysis.events:
            assert -0.5 <= event.timestamp <= analysis.duration + 0.5

    def test_events_carry_bounded_measurements(self, analysis):
        for event in analysis.events:
            assert 0.0 <= event.strength <= 1.0
            assert 0.0 <= event.confidence <= 1.0
            assert event.division >= 1

    def test_several_instrument_classes_are_identified(self, analysis):
        """The classifier must actually discriminate, not label everything once."""
        kinds = {event.event_type for event in analysis.events}
        assert len(kinds) >= 4
        assert MusicalEventType.DOWNBEAT in kinds

    def test_downbeats_land_on_bar_lines(self, analysis):
        downbeats = [event for event in analysis.events if event.is_downbeat]
        assert len(downbeats) > 10
        on_bar = sum(1 for event in downbeats if event.quantized_beat % 4 < 1e-6)
        assert on_bar / len(downbeats) > 0.8

    def test_events_are_snapped_to_musical_subdivisions(self, analysis):
        """A duple fixture should snap overwhelmingly to duple divisions."""
        duple = sum(1 for event in analysis.events if event.division in (1, 2, 4, 8))
        assert duple / len(analysis.events) > 0.9

    def test_no_two_events_collide(self, analysis):
        beats = [event.quantized_beat for event in analysis.events]
        for earlier, later in zip(beats, beats[1:]):
            assert later - earlier > 0.1

    def test_every_event_belongs_to_a_section(self, analysis):
        valid = {section.index for section in analysis.sections}
        assert all(event.section_index in valid for event in analysis.events)


class TestSerializationRoundTrip:
    def test_analysis_survives_a_json_round_trip(self, analysis):
        """Generation reloads the cached analysis, so this must be lossless."""
        import json

        from app.models.musical import AnalysisResult

        restored = AnalysisResult.from_dict(json.loads(json.dumps(analysis.to_dict())))
        assert restored.bpm == pytest.approx(analysis.bpm)
        assert restored.grid.offset == pytest.approx(analysis.grid.offset, abs=1e-5)
        assert len(restored.events) == len(analysis.events)
        assert len(restored.sections) == len(analysis.sections)
        assert restored.events[0].event_type is analysis.events[0].event_type
