"""Structural segmentation.

We do not attempt neural section classification. Beat-synchronous timbre and
harmony features are clustered agglomeratively to find boundaries, then each
segment is labelled from its relative energy, its position in the song and its
relationship to its neighbours. Getting the *shape* right (where the quiet
parts and the loud parts are) matters far more to a generated map than getting
the word "chorus" right.
"""

from __future__ import annotations

import logging

import librosa
import numpy as np

from app.models.enums import SectionType
from app.models.musical import BeatGrid, Section
from app.services.analysis.audio_analyzer import RawAnalysis

logger = logging.getLogger(__name__)

#: Aim for roughly one section every this many seconds.
TARGET_SECTION_SECONDS = 18.0
MIN_SECTIONS = 3
MAX_SECTIONS = 14
#: Segments shorter than this are merged into a neighbour.
MIN_SECTION_SECONDS = 6.0


def _beat_sync(values: np.ndarray, frame_times: np.ndarray, beats: np.ndarray) -> np.ndarray:
    """Average a frame-level feature between consecutive beats."""
    if beats.size < 2:
        return np.zeros((values.shape[0] if values.ndim > 1 else 1, 0))
    matrix = values if values.ndim > 1 else values[np.newaxis, :]
    edges = np.searchsorted(frame_times, beats)
    edges = np.clip(edges, 0, matrix.shape[1])
    result = np.zeros((matrix.shape[0], beats.size - 1))
    for index in range(beats.size - 1):
        start, end = edges[index], max(edges[index + 1], edges[index] + 1)
        result[:, index] = matrix[:, start:end].mean(axis=1)
    return result


def _standardize(matrix: np.ndarray) -> np.ndarray:
    mean = matrix.mean(axis=1, keepdims=True)
    std = matrix.std(axis=1, keepdims=True)
    std[std < 1e-8] = 1.0
    return (matrix - mean) / std


def _merge_short(boundaries: list[int], beat_times: np.ndarray) -> list[int]:
    """Drop boundaries that would create a segment below the minimum length."""
    if len(boundaries) < 3:
        return boundaries
    merged = [boundaries[0]]
    for index in boundaries[1:-1]:
        if beat_times[index] - beat_times[merged[-1]] >= MIN_SECTION_SECONDS:
            merged.append(index)
    merged.append(boundaries[-1])
    if len(merged) > 2 and beat_times[merged[-1]] - beat_times[merged[-2]] < MIN_SECTION_SECONDS:
        merged.pop(-2)
    return merged


def _classify(
    index: int,
    count: int,
    intensity: float,
    energies: np.ndarray,
    onset_density: float,
    duration: float,
) -> SectionType:
    """Label a segment from its energy relative to the rest of the song."""
    is_first = index == 0
    is_last = index == count - 1
    previous = energies[index - 1] if index > 0 else None
    following = energies[index + 1] if index < count - 1 else None

    high = intensity >= 0.72
    low = intensity <= 0.32

    if is_first and intensity < 0.55:
        return SectionType.INTRO
    if is_last and intensity < 0.6:
        return SectionType.OUTRO

    if low:
        # A quiet stretch surrounded by loud ones is a break, not an intro.
        if previous is not None and following is not None and (previous > 0.6 or following > 0.6):
            return SectionType.BREAK
        return SectionType.LOW_ENERGY

    if high:
        # A loud section that follows a rapid rise reads as a drop.
        if previous is not None and intensity - previous > 0.28:
            return SectionType.DROP
        return SectionType.CHORUS

    if following is not None and following - intensity > 0.25 and duration <= 24.0:
        return SectionType.BUILDUP
    if previous is not None and abs(intensity - previous) < 0.08 and onset_density > 0.5:
        return SectionType.VERSE
    if duration < 10.0:
        return SectionType.TRANSITION
    if intensity >= 0.55:
        return SectionType.PRE_CHORUS
    return SectionType.VERSE


def analyze_sections(raw: RawAnalysis, grid: BeatGrid) -> list[Section]:
    """Segment the song and label each span."""
    beats = raw.beats
    duration = raw.duration

    if beats.size < 8:
        return [
            Section(
                index=0,
                start=0.0,
                end=duration,
                start_beat=grid.time_to_beat(0.0),
                end_beat=grid.time_to_beat(duration),
                type=SectionType.MEDIUM_ENERGY,
                intensity=0.5,
                energy=float(raw.rms.mean()) if raw.rms.size else 0.0,
            )
        ]

    features = np.vstack(
        [
            _beat_sync(raw.chroma, raw.frame_times, beats),
            _beat_sync(raw.mfcc[1:8], raw.frame_times, beats),
            _beat_sync(raw.rms[np.newaxis, :], raw.frame_times, beats) * 3.0,
            _beat_sync(raw.band_envelopes, raw.frame_times, beats) * 2.0,
        ]
    )
    features = _standardize(features)

    target = int(np.clip(round(duration / TARGET_SECTION_SECONDS), MIN_SECTIONS, MAX_SECTIONS))
    target = min(target, max(2, features.shape[1] // 8))

    try:
        boundary_beats = librosa.segment.agglomerative(features, target)
        boundaries = sorted({0, *[int(value) for value in boundary_beats], beats.size - 1})
    except Exception:  # pragma: no cover - degenerate feature matrices
        logger.warning("segmentation_failed", exc_info=True)
        step = max(1, (beats.size - 1) // target)
        boundaries = list(range(0, beats.size, step))
        if boundaries[-1] != beats.size - 1:
            boundaries.append(beats.size - 1)

    boundaries = _merge_short(boundaries, beats)

    # Per-segment energy, normalised across the song so labels are relative.
    spans: list[tuple[float, float, float, float]] = []
    for index in range(len(boundaries) - 1):
        start = float(beats[boundaries[index]]) if index > 0 else 0.0
        end = float(beats[boundaries[index + 1]])
        if index == len(boundaries) - 2:
            end = duration
        mask = (raw.frame_times >= start) & (raw.frame_times < end)
        energy = float(raw.rms[mask].mean()) if mask.any() else 0.0
        onsets_in_span = int(((raw.onsets >= start) & (raw.onsets < end)).sum())
        density = onsets_in_span / max(end - start, 1e-6)
        spans.append((start, end, energy, density))

    energies = np.array([span[2] for span in spans])
    densities = np.array([span[3] for span in spans])
    intensities = _rank_normalize(energies * 0.7 + _rank_normalize(densities) * 0.3)
    # Ranking is deliberately magnitude-blind, which is right for naming a
    # section and wrong for deciding how busy it should be. Keep the real
    # ratio too: a break that is a quarter as loud as the drop must map a
    # quarter as densely, not merely "one rank quieter".
    peak_energy = float(energies.max()) if energies.size else 0.0
    loudness = energies / peak_energy if peak_energy > 0 else np.ones_like(energies)

    sections: list[Section] = []
    for index, (start, end, energy, density) in enumerate(spans):
        section_type = _classify(
            index,
            len(spans),
            float(intensities[index]),
            intensities,
            float(_rank_normalize(densities)[index]),
            end - start,
        )
        sections.append(
            Section(
                index=index,
                start=start,
                end=end,
                start_beat=grid.time_to_beat(start),
                end_beat=grid.time_to_beat(end),
                type=section_type,
                intensity=round(float(intensities[index]), 4),
                loudness=round(float(loudness[index]), 4),
                energy=round(energy, 6),
                onset_density=round(density, 4),
            )
        )

    logger.info(
        "sections_detected",
        extra={"count": len(sections), "types": [section.type.value for section in sections]},
    )
    return sections


def _rank_normalize(values: np.ndarray) -> np.ndarray:
    """Map values onto 0-1 by rank, so labels adapt to each song's dynamics."""
    if values.size == 0:
        return values
    if values.size == 1:
        return np.array([0.5])
    order = values.argsort().argsort().astype(float)
    return order / (values.size - 1)
