"""Re-fit an existing musical timeline onto a corrected beat grid.

Tempo detection is right most of the time and occasionally confidently wrong —
usually by an octave, sometimes because the song has no steady pulse to find.
When it is wrong, nothing downstream can save the map: every note is placed
against a grid that does not match the music, which is what "the notes are not
on the beat" actually means.

So the user gets to correct it. Re-running the DSP would take another twenty
seconds and is unnecessary: musical events carry wall-clock timestamps and
band energies, which do not depend on the grid at all. Only the beat-relative
fields have to be recomputed, and the phase can be recovered from the onset
envelope that analysis already stored.
"""

from __future__ import annotations

import logging

import numpy as np

from app.models.musical import AnalysisResult, BeatGrid, MusicalEvent, Section
from app.services.analysis.event_detector import (
    MAX_SNAP_ERROR_BEATS,
    _snap,
    divisions_for,
)
from app.services.analysis.tempo import fold_score, prepare_envelope

logger = logging.getLogger(__name__)

#: A correction outside this range is almost certainly a typo, not a tempo.
MIN_OVERRIDE_BPM = 40.0
MAX_OVERRIDE_BPM = 300.0


def best_offset_for(analysis: AnalysisResult, bpm: float) -> float:
    """Find the phase that best fits a user-supplied tempo.

    Asking someone for a BPM is reasonable; asking them for a millisecond
    offset is not. The onset envelope kept from analysis is enough to recover
    the phase on its own.
    """
    features = analysis.features
    if not features.onset_strength or not features.frame_times:
        return analysis.grid.offset
    envelope = prepare_envelope(np.asarray(features.onset_strength, dtype=float))
    frame_times = np.asarray(features.frame_times, dtype=float)
    if envelope.size < 8 or envelope.size != frame_times.size:
        return analysis.grid.offset
    _contrast, offset = fold_score(bpm, envelope, frame_times)
    return float(offset)


def requantize(analysis: AnalysisResult, bpm: float) -> AnalysisResult:
    """Return the same analysis re-expressed on a corrected constant tempo.

    Wall-clock timing, band energies and event classifications are untouched —
    they were never grid-dependent. Beat positions, subdivisions and bar phase
    are recomputed, and events that no longer land near the new grid are
    dropped rather than dragged onto it.
    """
    if not MIN_OVERRIDE_BPM <= bpm <= MAX_OVERRIDE_BPM:
        raise ValueError(
            f"Tempo must be between {MIN_OVERRIDE_BPM:.0f} and {MAX_OVERRIDE_BPM:.0f} BPM."
        )

    offset = best_offset_for(analysis, bpm)
    grid = BeatGrid(
        bpm=round(bpm, 4),
        offset=offset,
        beats_per_bar=analysis.grid.beats_per_bar,
        confidence=analysis.grid.confidence,
        duration=analysis.duration,
        triple_subdivision=analysis.grid.triple_subdivision,
    )
    divisions = divisions_for(grid.triple_subdivision)

    events: list[MusicalEvent] = []
    for event in analysis.events:
        beat = grid.time_to_beat(event.timestamp)
        if beat < -0.5:
            continue
        snapped, division, error = _snap(beat, divisions)
        if error > MAX_SNAP_ERROR_BEATS or snapped < 0.0:
            continue
        bar_phase = snapped % grid.beats_per_bar
        events.append(
            MusicalEvent(
                timestamp=event.timestamp,
                beat=float(beat),
                strength=event.strength,
                confidence=float(
                    np.clip(1.0 - error / MAX_SNAP_ERROR_BEATS, 0.0, 1.0)
                ),
                section_index=event.section_index,
                event_type=event.event_type,
                low_energy=event.low_energy,
                mid_energy=event.mid_energy,
                high_energy=event.high_energy,
                onset_strength=event.onset_strength,
                percussive=event.percussive,
                harmonic=event.harmonic,
                quantized_beat=float(snapped),
                division=division,
                snap_error=float(error),
                is_downbeat=abs(bar_phase) < 1e-6,
                bar_phase=float(bar_phase),
            )
        )

    sections = [
        Section(
            index=section.index,
            start=section.start,
            end=section.end,
            start_beat=grid.time_to_beat(section.start),
            end_beat=grid.time_to_beat(section.end),
            type=section.type,
            intensity=section.intensity,
            loudness=section.loudness,
            energy=section.energy,
            onset_density=section.onset_density,
        )
        for section in analysis.sections
    ]

    logger.info(
        "analysis_requantized",
        extra={
            "from_bpm": round(analysis.grid.bpm, 3),
            "to_bpm": round(grid.bpm, 3),
            "offset": round(grid.offset, 4),
            "events_before": len(analysis.events),
            "events_after": len(events),
        },
    )

    return AnalysisResult(
        duration=analysis.duration,
        sample_rate=analysis.sample_rate,
        grid=grid,
        beats=analysis.beats,
        beat_confidence=analysis.beat_confidence,
        downbeats=analysis.downbeats,
        onsets=analysis.onsets,
        onset_strengths=analysis.onset_strengths,
        sections=sections,
        events=events,
        features=analysis.features,
        tempo_candidates=analysis.tempo_candidates,
    )
