"""Tempo estimation with octave correction and fine grid refinement.

Two separate problems hide inside "what is the BPM of this song".

**The octave.** librosa routinely lands a factor of two away from what a human
would tap — a 128 BPM dance track reported as 64, a 174 BPM drum-and-bass track
as 87. We fix this by scoring every plausible ratio of every raw estimate.

**The exact value.** Tempo comes out of an autocorrelation, so it arrives
quantised to lag bins: a 128 BPM track is reported as 129.2. That 1% error
sounds harmless and is not. Beat Saber maps have a single constant BPM, so a
1% error drifts the grid by over a second across three minutes and every note
in the back half of the map lands late. We refine against the onset envelope
directly, to about 0.005 BPM.

Both stages use the same measurement: fold the onset envelope onto a candidate
beat period and see how sharply the energy concentrates. A correct period puts
every transient in the same phase bin; a wrong one smears them out. Folding is
O(n) regardless of phase resolution, which is what makes the fine search
affordable.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from app.models.musical import BeatGrid, TempoSegment

logger = logging.getLogger(__name__)

#: Ratios applied to every raw estimate. Covers half/double time plus the
#: triplet confusions that trip up shuffle and 6/8 material.
TEMPO_RATIOS = (0.25, 1 / 3, 0.5, 2 / 3, 0.75, 1.0, 4 / 3, 1.5, 2.0, 3.0, 4.0)

MIN_BPM = 60.0
MAX_BPM = 220.0

#: Centre and width (in octaves) of the plausibility prior.
PRIOR_CENTRE_BPM = 124.0
PRIOR_SIGMA_OCTAVES = 0.62

#: Phase bins used when folding. 96 bins at 128 BPM is ~5 ms of resolution.
PHASE_BINS = 96

#: Circular smoothing kernel: real transients jitter across adjacent bins.
_SMOOTHING_KERNEL = np.array([0.25, 0.6, 1.0, 0.6, 0.25])

#: Fine search window around the chosen octave, and its two step sizes.
REFINE_WINDOW = 0.04
REFINE_COARSE_STEP = 0.1
REFINE_FINE_STEP = 0.005
REFINE_FINE_SPAN = 0.2


@dataclass(slots=True)
class TempoCandidate:
    bpm: float
    offset: float
    alignment: float
    contrast: float
    prior: float
    score: float

    def to_dict(self) -> dict[str, float]:
        return {
            "bpm": round(self.bpm, 3),
            "offset": round(self.offset, 4),
            "alignment": round(self.alignment, 4),
            "contrast": round(self.contrast, 4),
            "prior": round(self.prior, 4),
            "score": round(self.score, 4),
        }


def _tempo_prior(bpm: float) -> float:
    """Log-normal preference for tempi humans actually tap at."""
    octaves = math.log2(bpm / PRIOR_CENTRE_BPM)
    return float(math.exp(-0.5 * (octaves / PRIOR_SIGMA_OCTAVES) ** 2))


def fold_score(
    bpm: float,
    envelope: np.ndarray,
    frame_times: np.ndarray,
    bins: int = PHASE_BINS,
) -> tuple[float, float]:
    """Fold the onset envelope onto one beat period.

    Returns ``(contrast, offset)`` — how sharply energy concentrates at one
    phase, and the wall-clock offset of that phase from t=0.
    """
    period = 60.0 / bpm
    if period <= 0 or envelope.size == 0:
        return 0.0, 0.0

    phase = np.mod(frame_times, period) / period
    indices = np.minimum((phase * bins).astype(np.int64), bins - 1)
    histogram = np.bincount(indices, weights=envelope, minlength=bins)

    # Smooth circularly so a transient landing on a bin edge is not penalised.
    pad = len(_SMOOTHING_KERNEL) // 2
    wrapped = np.concatenate([histogram[-pad:], histogram, histogram[:pad]])
    smoothed = np.convolve(wrapped, _SMOOTHING_KERNEL, mode="same")[pad:-pad]

    mean = float(smoothed.mean())
    if mean <= 1e-12:
        return 0.0, 0.0

    peak_bin = int(smoothed.argmax())
    contrast = float(smoothed.max()) / mean
    offset = (peak_bin + 0.5) / bins * period
    return contrast, offset


def _score_candidate(
    bpm: float, envelope: np.ndarray, frame_times: np.ndarray
) -> TempoCandidate:
    contrast, offset = fold_score(bpm, envelope, frame_times)
    prior = _tempo_prior(bpm)
    # Contrast is scale-free and robust to song length; the prior breaks the
    # octave ambiguity that folding alone cannot resolve.
    score = (contrast**1.4) * prior
    return TempoCandidate(bpm, offset, contrast, contrast, prior, score)


def _collect_candidates(raw_estimates: list[float]) -> list[float]:
    """Expand raw estimates into all plausible tempo octaves/ratios."""
    seen: list[float] = []
    for estimate in raw_estimates:
        if not (estimate and math.isfinite(estimate)) or estimate <= 0:
            continue
        for ratio in TEMPO_RATIOS:
            bpm = estimate * ratio
            if not MIN_BPM <= bpm <= MAX_BPM:
                continue
            if any(abs(bpm - existing) < 0.5 for existing in seen):
                continue
            seen.append(bpm)
    return sorted(seen)


def prepare_envelope(onset_envelope: np.ndarray) -> np.ndarray:
    """Sharpen the envelope so folding measures transients, not loudness."""
    envelope = np.asarray(onset_envelope, dtype=float)
    if envelope.size == 0:
        return envelope
    envelope = np.maximum(envelope - np.median(envelope), 0.0)
    peak = envelope.max()
    return envelope / peak if peak > 0 else envelope


def refine_tempo(
    bpm: float,
    envelope: np.ndarray,
    frame_times: np.ndarray,
) -> tuple[float, float, float]:
    """Search a fine BPM grid around `bpm` for the sharpest fold.

    Coarse-to-fine: 0.1 BPM across a +/-4% window, then 0.005 BPM around the
    winner. Returns ``(bpm, offset, contrast)``.
    """
    if envelope.size == 0 or frame_times.size == 0:
        return bpm, 0.0, 0.0

    def best_over(values: np.ndarray) -> tuple[float, float, float]:
        best = (bpm, 0.0, -1.0)
        for candidate in values:
            if not MIN_BPM <= candidate <= MAX_BPM:
                continue
            contrast, offset = fold_score(candidate, envelope, frame_times)
            if contrast > best[2]:
                best = (float(candidate), offset, contrast)
        return best

    span = bpm * REFINE_WINDOW
    coarse_bpm, _offset, _contrast = best_over(
        np.arange(bpm - span, bpm + span + REFINE_COARSE_STEP, REFINE_COARSE_STEP)
    )
    fine_bpm, fine_offset, fine_contrast = best_over(
        np.arange(
            coarse_bpm - REFINE_FINE_SPAN,
            coarse_bpm + REFINE_FINE_SPAN + REFINE_FINE_STEP,
            REFINE_FINE_STEP,
        )
    )

    snapped = snap_round_tempo(fine_bpm)
    if snapped != fine_bpm:
        # Only accept the rounder value if it folds essentially as well; a real
        # 127.6 BPM live recording should not be forced onto 128.
        contrast, offset = fold_score(snapped, envelope, frame_times)
        if contrast >= fine_contrast * 0.985:
            return snapped, offset, contrast

    return fine_bpm, fine_offset, fine_contrast


def snap_round_tempo(bpm: float, tolerance: float = 0.25) -> float:
    """Nudge onto a whole BPM when the fit is already that close.

    Produced music is overwhelmingly written at integer tempi, so a fit of
    127.97 is far more likely to be 128 than to be genuinely 127.97.

    Only whole numbers are candidates. Including half-BPM values as well would
    put a candidate within 0.25 of *every* possible tempo, so nothing could
    ever stay unsnapped and a genuinely odd tempo — a live take, a tape
    transfer — would be silently rounded off. The caller applies a second
    guard: the snapped value is only accepted if it folds essentially as well
    as the measured one.
    """
    candidate = round(bpm)
    if abs(bpm - candidate) <= tolerance:
        return float(candidate)
    return round(bpm, 3)


def estimate_tempo(
    onset_envelope: np.ndarray,
    frame_times: np.ndarray,
    duration: float,
    raw_estimates: list[float],
) -> tuple[TempoCandidate, list[TempoCandidate]]:
    """Choose the tempo octave, then refine it. Returns best plus alternatives."""
    envelope = prepare_envelope(onset_envelope)
    if envelope.size == 0 or duration <= 0:
        fallback = TempoCandidate(120.0, 0.0, 0.0, 0.0, _tempo_prior(120.0), 0.0)
        return fallback, [fallback]

    candidates = _collect_candidates(raw_estimates) or [120.0]
    scored = [_score_candidate(bpm, envelope, frame_times) for bpm in candidates]
    scored.sort(key=lambda candidate: candidate.score, reverse=True)
    best = scored[0]

    # A close runner-up at exactly double the winner's tempo usually means the
    # track is denser than the winner suggests; prefer the faster reading when
    # it is nearly as good, because sparse grids produce lifeless maps.
    for candidate in scored[1:4]:
        if abs(candidate.bpm / best.bpm - 2.0) < 0.03 and candidate.score > best.score * 0.92:
            logger.info(
                "tempo_double_time_preferred",
                extra={"from_bpm": round(best.bpm, 2), "to_bpm": round(candidate.bpm, 2)},
            )
            best = candidate
            break

    refined_bpm, refined_offset, refined_contrast = refine_tempo(
        best.bpm, envelope, frame_times
    )
    if abs(refined_bpm - best.bpm) > 0.001:
        logger.info(
            "tempo_refined",
            extra={"from_bpm": round(best.bpm, 3), "to_bpm": round(refined_bpm, 3)},
        )

    refined = TempoCandidate(
        bpm=refined_bpm,
        offset=refined_offset,
        alignment=refined_contrast,
        contrast=refined_contrast,
        prior=_tempo_prior(refined_bpm),
        score=(refined_contrast**1.4) * _tempo_prior(refined_bpm),
    )
    return refined, [refined] + scored[:7]


def beat_consistency(beats: np.ndarray, bpm: float) -> float:
    """0-1 score describing how evenly spaced the detected beats are."""
    if beats.size < 3:
        return 0.0
    intervals = np.diff(beats)
    expected = 60.0 / bpm
    error = np.abs(intervals - expected) / expected
    return float(np.clip(1.0 - np.mean(error) * 2.0, 0.0, 1.0))


def grid_fit_quality(bpm: float, offset: float, beats: np.ndarray) -> float:
    """How well the detected beats sit on the final constant grid, 0-1."""
    if beats.size < 3:
        return 0.0
    period = 60.0 / bpm
    residuals = np.abs(((beats - offset) / period) - np.round((beats - offset) / period))
    return float(np.clip(1.0 - np.mean(residuals) * 4.0, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Tempo drift
# ---------------------------------------------------------------------------
#
# Everything above assumes the song holds one tempo, which is true of anything
# written to a click and false of everything played by humans. A live take
# breathes: it pushes into the chorus and settles in the verse, typically by
# one to three percent. That is small enough to sound natural and large enough
# to ruin a map — a 2% error accumulates a full beat of drift in fifty bars.
#
# The fix is a piecewise-constant tempo map. Beat Saber supports this natively
# through v3 `bpmEvents`, so the map can follow the performance instead of
# fighting it.
#
# The detector is deliberately reluctant. A spurious tempo change is worse than
# a missed one — a constant grid on drifting music degrades gracefully, while a
# wrong tempo map does not — so a piecewise result is only accepted when it
# measurably beats the constant fit on the detected beats.

#: Local tempo is measured from inter-beat intervals, then median-filtered over
#: this many beats to suppress single-beat jitter.
IBI_SMOOTHING_BEATS = 9

#: Relative spread (p90-p10 over median) below which a song is called constant.
DRIFT_DETECTION_THRESHOLD = 0.012

#: A new segment starts when local tempo departs the current one by this much.
SEGMENT_TOLERANCE = 0.010

#: ...and stays departed for this many consecutive observations.
SEGMENT_CONFIRM_BEATS = 6

#: Segments shorter than this are merged into a neighbour.
MIN_SEGMENT_SECONDS = 10.0

#: Hard cap, so pathological input cannot produce hundreds of tempo events.
MAX_SEGMENTS = 24

#: A piecewise map must beat the constant one by this factor to be accepted...
DRIFT_IMPROVEMENT_FACTOR = 0.75

#: ...and the constant fit must be at least this bad to be worth replacing.
#: Expressed as mean absolute distance from the beat grid, in beats.
MIN_CONSTANT_RESIDUAL = 0.055


def grid_residual(grid: BeatGrid, beats: np.ndarray) -> float:
    """Mean distance from the detected beats to their nearest grid line.

    In beats, so it is comparable across tempos. This is the yardstick that
    decides whether a variable-tempo map is worth having.
    """
    if beats.size == 0:
        return 0.0
    positions = np.array([grid.time_to_beat(float(time)) for time in beats])
    return float(np.mean(np.abs(positions - np.round(positions))))


def _repair_intervals(intervals: np.ndarray) -> np.ndarray:
    """Fix dropped and doubled beats before measuring local tempo.

    A beat tracker that misses a beat reports one interval of roughly twice the
    true length; one that inserts a beat reports two of roughly half. Taken at
    face value these read as violent tempo changes, so they are folded back to
    the prevailing interval rather than being allowed to create segments.
    """
    if intervals.size == 0:
        return intervals
    reference = float(np.median(intervals))
    if reference <= 0:
        return intervals

    repaired = intervals.astype(float).copy()
    for index, value in enumerate(repaired):
        if value <= 0:
            repaired[index] = reference
            continue
        ratio = value / reference
        for divisor in (2.0, 3.0, 4.0):
            if abs(ratio - divisor) < 0.28:
                repaired[index] = value / divisor
                break
        else:
            for multiplier in (2.0, 3.0):
                if abs(ratio - 1.0 / multiplier) < 0.28 / multiplier:
                    repaired[index] = value * multiplier
                    break
    return repaired


def _median_filter(values: np.ndarray, window: int) -> np.ndarray:
    if values.size == 0 or window <= 1:
        return values
    half = window // 2
    padded = np.pad(values, (half, half), mode="edge")
    return np.array(
        [float(np.median(padded[index : index + window])) for index in range(values.size)]
    )


def local_tempo_curve(beats: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Smoothed local tempo, as ``(times, bpms)``, from the detected beats."""
    if beats.size < IBI_SMOOTHING_BEATS + 2:
        return np.array([]), np.array([])
    intervals = _repair_intervals(np.diff(beats))
    valid = intervals > 1e-6
    if valid.sum() < IBI_SMOOTHING_BEATS:
        return np.array([]), np.array([])
    times = ((beats[:-1] + beats[1:]) / 2.0)[valid]
    bpms = _median_filter(60.0 / intervals[valid], IBI_SMOOTHING_BEATS)
    return times, bpms


def tempo_spread(bpms: np.ndarray) -> float:
    """Relative spread of the local tempo curve; 0 means perfectly steady."""
    if bpms.size < 4:
        return 0.0
    low, high = np.percentile(bpms, [10, 90])
    centre = float(np.median(bpms))
    return float((high - low) / centre) if centre > 0 else 0.0


def _split_curve(times: np.ndarray, bpms: np.ndarray) -> list[tuple[int, int]]:
    """Cut the tempo curve into runs of roughly constant tempo."""
    spans: list[tuple[int, int]] = []
    start = 0
    index = 1
    while index < bpms.size:
        centre = float(np.median(bpms[start:index]))
        if centre <= 0:
            index += 1
            continue
        departed = abs(bpms[index] - centre) / centre > SEGMENT_TOLERANCE
        if departed and len(spans) < MAX_SEGMENTS - 1:
            run = bpms[index : index + SEGMENT_CONFIRM_BEATS]
            confirmed = run.size >= SEGMENT_CONFIRM_BEATS and bool(
                np.all(np.abs(run - centre) / centre > SEGMENT_TOLERANCE * 0.7)
            )
            long_enough = times[index] - times[start] >= MIN_SEGMENT_SECONDS
            if confirmed and long_enough:
                spans.append((start, index))
                start = index
        index += 1
    spans.append((start, bpms.size))
    return spans


def _refine_within(
    bpm: float,
    envelope: np.ndarray,
    frame_times: np.ndarray,
    start: float,
    end: float,
) -> tuple[float, float]:
    """Fine-refine one segment's tempo and phase against only its own audio.

    Returns ``(bpm, offset)``. The phase matters as much as the tempo: a global
    fold over a drifting song smears its phase along with everything else, so a
    segment that inherits it starts out half a beat wrong no matter how exact
    its tempo is.
    """
    mask = (frame_times >= start) & (frame_times < end)
    if mask.sum() < 32:
        return bpm, 0.0
    refined, offset, _contrast = refine_tempo(bpm, envelope[mask], frame_times[mask])
    # A segment refinement that wanders far has lost the plot; keep the input.
    if abs(refined - bpm) / bpm >= 0.05:
        return bpm, 0.0
    return refined, offset


def build_tempo_segments(
    beats: np.ndarray,
    envelope: np.ndarray,
    frame_times: np.ndarray,
    base_bpm: float,
    base_offset: float,
    duration: float,
    beats_per_bar: int = 4,
) -> list[TempoSegment]:
    """Fit a piecewise-constant tempo map to the detected beats.

    Segment boundaries are snapped to bar lines of the preceding segment, so
    the beat index at every tempo change is a whole number of bars. That keeps
    quantisation honest across the change and produces the tidy `bpmEvents` a
    human mapper would have written.
    """
    times, bpms = local_tempo_curve(beats)
    if times.size == 0:
        return [TempoSegment(start_time=base_offset, start_beat=0.0, bpm=base_bpm)]

    spans = _split_curve(times, bpms)
    segments: list[TempoSegment] = []

    for order, (start_index, end_index) in enumerate(spans):
        span_bpm = float(np.median(bpms[start_index:end_index]))
        span_start = 0.0 if order == 0 else float(times[start_index])
        span_end = duration if end_index >= times.size else float(times[end_index])
        span_bpm, span_offset = _refine_within(
            span_bpm, envelope, frame_times, span_start, span_end
        )

        if order == 0:
            # Take the phase measured inside this segment rather than the
            # global one, which a drifting song has already smeared.
            first_offset = span_offset if span_offset > 0 else base_offset
            segments.append(
                TempoSegment(start_time=first_offset, start_beat=0.0, bpm=span_bpm)
            )
            continue

        previous = segments[-1]
        raw_beat = (
            previous.start_beat + (span_start - previous.start_time) * previous.bpm / 60.0
        )
        # Snap the change onto a bar line of the previous tempo.
        snapped_beat = round(raw_beat / beats_per_bar) * beats_per_bar
        if snapped_beat <= previous.start_beat:
            continue
        snapped_time = (
            previous.start_time
            + (snapped_beat - previous.start_beat) * 60.0 / previous.bpm
        )
        if snapped_time - previous.start_time < MIN_SEGMENT_SECONDS:
            continue
        segments.append(
            TempoSegment(
                start_time=snapped_time, start_beat=float(snapped_beat), bpm=span_bpm
            )
        )

    return segments


def fit_beat_grid(
    beats: np.ndarray,
    envelope: np.ndarray,
    frame_times: np.ndarray,
    base_bpm: float,
    base_offset: float,
    duration: float,
    *,
    beats_per_bar: int = 4,
    allow_variable: bool = True,
) -> tuple[BeatGrid, dict[str, float]]:
    """Choose between a constant and a variable-tempo grid.

    Returns the grid and a small report describing why. The constant grid wins
    ties and near-ties on purpose: it is what almost every song wants, and it
    is the reading that fails gracefully when the detector is wrong.
    """
    constant = BeatGrid(
        bpm=base_bpm,
        offset=base_offset,
        beats_per_bar=beats_per_bar,
        duration=duration,
    )
    constant_residual = grid_residual(constant, beats)
    report = {
        "spread": 0.0,
        "constant_residual": round(constant_residual, 5),
        "variable_residual": round(constant_residual, 5),
        "segments": 1,
    }

    if not allow_variable or beats.size < 24:
        return constant, report

    _times, bpms = local_tempo_curve(beats)
    spread = tempo_spread(bpms)
    report["spread"] = round(spread, 5)

    if spread < DRIFT_DETECTION_THRESHOLD or constant_residual < MIN_CONSTANT_RESIDUAL:
        return constant, report

    segments = build_tempo_segments(
        beats, envelope, frame_times, base_bpm, base_offset, duration, beats_per_bar
    )
    if len(segments) < 2:
        return constant, report

    variable = BeatGrid(
        segments=segments,
        beats_per_bar=beats_per_bar,
        duration=duration,
    )
    variable_residual = grid_residual(variable, beats)
    report["variable_residual"] = round(variable_residual, 5)
    report["segments"] = len(segments)

    if variable_residual < constant_residual * DRIFT_IMPROVEMENT_FACTOR:
        logger.info(
            "tempo_drift_detected",
            extra={
                "segments": len(segments),
                "tempos": [round(segment.bpm, 2) for segment in segments],
                "constant_residual": round(constant_residual, 4),
                "variable_residual": round(variable_residual, 4),
            },
        )
        return variable, report

    report["segments"] = 1
    return constant, report
