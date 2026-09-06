"""Build the musical event timeline.

This is the intermediate representation the whole design hinges on: onsets are
never turned straight into notes. Instead each onset becomes a `MusicalEvent`
carrying enough context — which drum it probably was, how strong it is, where
it sits in the bar, which section it belongs to — that five different
difficulty profiles can each make their own decision about it later.

We also synthesise events on strong grid positions that carry energy but no
detected transient, so sparse passages still have material to map.
"""

from __future__ import annotations

import logging

import numpy as np

from app.models.enums import MusicalEventType
from app.models.musical import BeatGrid, MusicalEvent, Section
from app.services.analysis.audio_analyzer import RawAnalysis

logger = logging.getLogger(__name__)

#: Subdivisions the analysis grid may snap to (denominators of a beat), in
#: preference order. Duple divisions are tried before triplets: most music is
#: duple, and an ambiguous event snapped to a triplet reads as a timing error.
ANALYSIS_DIVISIONS = (1, 2, 4, 8, 3, 6)

#: The same ladder for music whose beat divides in three. Snapping a shuffle
#: onto the duple ladder drags every offbeat about 40 ms early, which reads as
#: sloppy timing rather than as the groove it is.
TRIPLE_DIVISIONS = (1, 3, 6, 12, 2, 4)


def divisions_for(triple_subdivision: bool) -> tuple[int, ...]:
    """Snapping ladder appropriate to how the beat divides."""
    return TRIPLE_DIVISIONS if triple_subdivision else ANALYSIS_DIVISIONS

#: An event is dropped if it cannot be snapped within this many beats.
#: At 128 BPM this is roughly 55 ms, about the limit of what reads as "on time".
MAX_SNAP_ERROR_BEATS = 0.12

#: Two events closer than this (in beats) collapse into the stronger one.
MIN_EVENT_SEPARATION_BEATS = 0.115

#: Percentile above which an onset counts as an accent.
ACCENT_PERCENTILE = 82.0


def _sample(values: np.ndarray, times: np.ndarray, at: np.ndarray) -> np.ndarray:
    if values.size == 0 or times.size == 0 or at.size == 0:
        return np.zeros_like(at)
    return np.interp(at, times, values, left=0.0, right=0.0)


def _snap(beat: float, divisions: tuple[int, ...]) -> tuple[float, int, float]:
    """Snap a beat position to the coarsest subdivision that fits it well.

    Returns ``(snapped_beat, division, error)``. Preferring coarse divisions
    keeps rhythms readable: a note that is genuinely on the beat should not be
    recorded as a 1/8 event merely because 1/8 also happens to fit.
    """
    best: tuple[float, int, float] | None = None
    for division in divisions:
        snapped = round(beat * division) / division
        error = abs(snapped - beat)
        # Half the slot width, with headroom, so a division only claims events
        # that are genuinely closer to its grid than to a coarser one.
        tolerance = min(MAX_SNAP_ERROR_BEATS, 0.5 / division * 0.45)
        if error <= tolerance:
            return snapped, division, error
        if best is None or error < best[2]:
            best = (snapped, division, error)
    assert best is not None
    return best


def _classify_event(
    low: float,
    mid: float,
    high: float,
    percussive: float,
    harmonic: float,
    strength: float,
    accent_threshold: float,
    is_downbeat: bool,
    near_boundary: bool,
) -> MusicalEventType:
    """Heuristic instrument labelling from band-limited spectral flux.

    Structural labels win over instrument labels: for mapping purposes, "this
    is the first beat of the bar" is more useful than "this is a kick drum".
    Below that we identify the instrument, and only fall back on loudness
    (ACCENT) when the bands are ambiguous — otherwise every loud kick would be
    labelled an accent and the instrument heuristic would never be exercised.

    The tests compare bands against each other rather than against a share of
    the total. Real percussive attacks are broadband: a kick puts about half
    its flux in the low band and most of the rest in the mid, so a "low band
    holds most of the energy" test misses nearly every kick ever recorded.
    """
    if near_boundary and strength >= accent_threshold * 0.8:
        return MusicalEventType.TRANSITION
    if is_downbeat:
        return MusicalEventType.DOWNBEAT

    total = low + mid + high + 1e-9

    # Hi-hats and shakers are the one unambiguous case: high band, nothing else.
    if high / total > 0.45 and high > low * 1.4:
        return MusicalEventType.PERCUSSION
    # Kick: low band leads the mid, and the high band is well behind both.
    if low > mid * 1.15 and low > high * 1.6 and low > 0.08:
        return MusicalEventType.KICK
    # Snare: broadband with the mid band at least matching the low.
    if mid >= low * 0.85 and mid > 0.06 and high > 0.03:
        return MusicalEventType.SNARE
    if harmonic > percussive * 1.35 and (mid + high) / total > 0.55:
        return MusicalEventType.MELODY
    if strength >= accent_threshold:
        return MusicalEventType.ACCENT
    return MusicalEventType.GENERIC_ONSET


def _section_index(sections: list[Section], time: float) -> int:
    for section in sections:
        if section.contains(time):
            return section.index
    return sections[-1].index if sections else 0


def build_events(
    raw: RawAnalysis,
    grid: BeatGrid,
    sections: list[Section],
) -> list[MusicalEvent]:
    """Turn onsets, beats and energy curves into a musical event timeline."""
    divisions = divisions_for(grid.triple_subdivision)
    times = raw.frame_times
    low_env, mid_env, high_env = raw.band_envelopes

    boundaries = np.array([section.start for section in sections[1:]], dtype=float)

    accent_threshold = (
        float(np.percentile(raw.onset_strengths, ACCENT_PERCENTILE))
        if raw.onset_strengths.size
        else 0.6
    )

    candidates: list[MusicalEvent] = []

    def _make_event(
        timestamp: float,
        strength: float,
        onset_strength: float,
        event_type_hint: MusicalEventType | None = None,
    ) -> MusicalEvent | None:
        beat = grid.time_to_beat(timestamp)
        if beat < -0.5:
            return None
        snapped, division, error = _snap(beat, divisions)
        if error > MAX_SNAP_ERROR_BEATS:
            return None
        # Grid beat 0 is the song's first detected beat, not t=0, so anything
        # earlier is a negative beat position. Beat Saber has no way to
        # schedule one and the validator rejects the whole map over it, which
        # is how a stray transient in the first half-second could sink an
        # otherwise fine song.
        if snapped < 0.0:
            return None

        at = np.array([timestamp])
        low = float(_sample(low_env, times, at)[0])
        mid = float(_sample(mid_env, times, at)[0])
        high = float(_sample(high_env, times, at)[0])
        percussive = float(_sample(raw.percussive_energy, times, at)[0])
        harmonic = float(_sample(raw.harmonic_energy, times, at)[0])

        # A downbeat is a position in the bar, full stop. This used to also
        # accept "close in time to a detected downbeat", which predates meter
        # detection and now marks events that snap elsewhere in the bar —
        # scattering the accent across beat 1 and the eighth after it.
        bar_phase = snapped % grid.beats_per_bar
        is_downbeat = abs(bar_phase) < 1e-6
        near_boundary = bool(
            boundaries.size and np.min(np.abs(boundaries - timestamp)) < 0.35
        )

        event_type = event_type_hint or _classify_event(
            low,
            mid,
            high,
            percussive,
            harmonic,
            strength,
            accent_threshold,
            is_downbeat,
            near_boundary,
        )

        return MusicalEvent(
            timestamp=float(timestamp),
            beat=float(beat),
            strength=float(np.clip(strength, 0.0, 1.0)),
            confidence=float(np.clip(1.0 - error / MAX_SNAP_ERROR_BEATS, 0.0, 1.0)),
            section_index=_section_index(sections, timestamp),
            event_type=event_type,
            low_energy=low,
            mid_energy=mid,
            high_energy=high,
            onset_strength=float(onset_strength),
            percussive=percussive,
            harmonic=harmonic,
            quantized_beat=float(snapped),
            division=division,
            snap_error=float(error),
            is_downbeat=is_downbeat,
            bar_phase=float(bar_phase),
        )

    for timestamp, strength in zip(raw.onsets.tolist(), raw.onset_strengths.tolist()):
        event = _make_event(timestamp, strength, strength)
        if event is not None:
            candidates.append(event)

    # Fill in grid positions that carry energy but produced no transient. Quiet
    # intros and sustained pads would otherwise yield no mappable material.
    beat_strength = _sample(raw.onset_envelope, times, raw.beats)
    beat_rms = _sample(raw.rms, times, raw.beats)
    existing = np.array([event.timestamp for event in candidates], dtype=float)
    for timestamp, strength, loudness in zip(
        raw.beats.tolist(), beat_strength.tolist(), beat_rms.tolist()
    ):
        if loudness < 0.06:
            continue
        if existing.size and np.min(np.abs(existing - timestamp)) < 0.055:
            continue
        event = _make_event(
            timestamp,
            max(strength, loudness * 0.55),
            strength,
            event_type_hint=MusicalEventType.BEAT,
        )
        if event is not None:
            candidates.append(event)

    candidates.sort(key=lambda event: (event.quantized_beat, -event.strength))
    events = _deduplicate(candidates)

    logger.info(
        "event_timeline_built",
        extra={
            "events": len(events),
            "by_type": _type_histogram(events),
        },
    )
    return events


def _deduplicate(events: list[MusicalEvent]) -> list[MusicalEvent]:
    """Collapse events that land on (nearly) the same beat, keeping the best."""
    kept: list[MusicalEvent] = []
    for event in events:
        if kept and event.quantized_beat - kept[-1].quantized_beat < MIN_EVENT_SEPARATION_BEATS:
            previous = kept[-1]
            if _priority(event) > _priority(previous):
                kept[-1] = event
            continue
        kept.append(event)
    return kept


def _priority(event: MusicalEvent) -> float:
    """Rank events competing for the same slot; structure outranks loudness."""
    bonus = {
        MusicalEventType.DOWNBEAT: 0.45,
        MusicalEventType.ACCENT: 0.30,
        MusicalEventType.TRANSITION: 0.28,
        MusicalEventType.KICK: 0.20,
        MusicalEventType.SNARE: 0.18,
        MusicalEventType.MELODY: 0.08,
        MusicalEventType.PERCUSSION: 0.04,
        MusicalEventType.GENERIC_ONSET: 0.0,
        MusicalEventType.BEAT: -0.05,
    }[event.event_type]
    coarse = 0.12 / event.division
    return event.strength + bonus + coarse


def _type_histogram(events: list[MusicalEvent]) -> dict[str, int]:
    histogram: dict[str, int] = {}
    for event in events:
        histogram[event.event_type.value] = histogram.get(event.event_type.value, 0) + 1
    return histogram
