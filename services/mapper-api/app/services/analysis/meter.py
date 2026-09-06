"""Meter detection: how many beats make a bar, and which beat starts one.

Assuming 4/4 is right most of the time and catastrophically wrong the rest of
it. In a waltz, "every fourth beat is a downbeat" puts the emphasis on beats
1, 2, 3, 1, 2, 3 in turn — it rotates through the bar, so the map accents the
music in a pattern the music does not have. That is worse than having no
downbeat information at all, because the rest of the pipeline trusts it.

Two independent measurements decide the meter, and they disagree in useful
ways:

* **Phase contrast** — how much louder the strongest position in a bar is than
  the average beat. Sensitive, but biased toward larger bars: taking the best
  of six positions beats the best of four by chance alone.
* **Accent autocorrelation** — whether the accent pattern actually repeats at
  that period. Unbiased with respect to bar length, and it separates duple from
  triple decisively: a 4/4 groove *anti*-correlates at lag 3, and a waltz
  anti-correlates at lag 4.

Multiplying them cancels the bias in the first with the rigour of the second.
A prior then keeps 4 as the default, and a departure has to be earned — the
same reluctance the tempo octave and drift detectors apply.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

#: Bar lengths worth considering. 5 and 7 exist but are rare enough that
#: offering them costs more in false positives than it wins in true ones.
CANDIDATE_METERS = (2, 3, 4, 6)

#: Mild preference for common bar lengths, applied to the combined score.
METER_PRIOR: dict[int, float] = {2: 0.90, 3: 0.97, 4: 1.00, 6: 0.90}

#: 4/4 is the default, and a different meter must beat it by this margin.
DEPARTURE_MARGIN = 1.15

#: When a bar length's pattern is really its own divisor repeated, prefer the
#: divisor: 6 that is just two bars of 3 should be reported as 3.
DIVISOR_PREFERENCE = 0.92

#: Below this many beats there is not enough evidence to judge a meter.
MIN_BEATS_FOR_METER = 24

#: The strongest position in the bar must be at least this much louder than the
#: average beat. Below it there is no accent pattern to read, and any meter the
#: scoring picks is noise wearing a hat — so 4 stands.
MIN_METER_CONTRAST = 1.15


@dataclass(frozen=True, slots=True)
class MeterEstimate:
    """What the detector concluded, and how sure it is."""

    beats_per_bar: int
    #: Index of the first beat of a bar, within the detected beat list.
    phase: int
    confidence: float
    #: Per-candidate scores, kept for logging and debugging.
    scores: dict[int, float]

    @property
    def is_triple(self) -> bool:
        """True for bar lengths built from threes (3/4, 6/8, 9/8)."""
        return self.beats_per_bar % 3 == 0

    @property
    def label(self) -> str:
        """A readable time signature, assuming a quarter-note pulse."""
        return {2: "2/4", 3: "3/4", 4: "4/4", 6: "6/8"}.get(
            self.beats_per_bar, f"{self.beats_per_bar}/4"
        )


def _autocorrelation(accent: np.ndarray, lag: int) -> float:
    """Normalised autocorrelation of the accent series at one lag."""
    if accent.size <= lag + 2:
        return 0.0
    centred = accent - accent.mean()
    denominator = float((centred * centred).sum())
    if denominator <= 1e-12:
        return 0.0
    return float((centred[:-lag] * centred[lag:]).sum() / denominator)


def _phase_profile(accent: np.ndarray, meter: int) -> np.ndarray:
    """Mean accent at each position within the bar."""
    return np.array([float(accent[phase::meter].mean()) for phase in range(meter)])


def _score_meter(accent: np.ndarray, meter: int) -> tuple[float, int, float]:
    """Score one candidate bar length. Returns ``(score, phase, contrast)``."""
    if accent.size < meter * 4:
        return 0.0, 0, 0.0

    profile = _phase_profile(accent, meter)
    overall = float(accent.mean())
    if overall <= 1e-9:
        return 0.0, 0, 0.0

    phase = int(np.argmax(profile))
    contrast = float(profile[phase]) / overall
    # A negative autocorrelation is positive evidence *against* this meter, so
    # it zeroes the score rather than merely lowering it.
    support = max(_autocorrelation(accent, meter), 0.0)
    score = contrast * support * METER_PRIOR.get(meter, 0.85)
    return score, phase, contrast


def _explained_by_divisor(
    accent: np.ndarray, meter: int, scores: dict[int, float]
) -> int | None:
    """Find a proper divisor that accounts for this meter just as well."""
    for divisor in sorted(CANDIDATE_METERS):
        if divisor >= meter or meter % divisor != 0:
            continue
        if scores.get(divisor, 0.0) >= scores[meter] * DIVISOR_PREFERENCE:
            return divisor
    return None


def detect_meter(accent: np.ndarray) -> MeterEstimate:
    """Infer bar length and downbeat phase from a per-beat accent series.

    `accent` is one value per detected beat — how emphatic that beat is.
    """
    default = MeterEstimate(beats_per_bar=4, phase=0, confidence=0.0, scores={})
    if accent.size < MIN_BEATS_FOR_METER:
        return default

    scores: dict[int, float] = {}
    phases: dict[int, int] = {}
    contrasts: dict[int, float] = {}
    for meter in CANDIDATE_METERS:
        score, phase, contrast = _score_meter(accent, meter)
        scores[meter] = score
        phases[meter] = phase
        contrasts[meter] = contrast

    best = max(CANDIDATE_METERS, key=lambda meter: scores[meter])

    # No accent pattern at all: do not dignify the noise with a meter.
    if contrasts[best] < MIN_METER_CONTRAST:
        return default

    # Collapse a bar length that is only its own divisor repeated.
    divisor = _explained_by_divisor(accent, best, scores)
    if divisor is not None:
        best = divisor

    # 4/4 is the overwhelming default; leaving it has to be earned.
    if best != 4 and scores[best] <= scores.get(4, 0.0) * DEPARTURE_MARGIN:
        best = 4

    support = max(_autocorrelation(accent, best), 0.0)
    confidence = float(
        np.clip(0.6 * (contrasts[best] - 1.0) / 1.2 + 0.4 * support, 0.0, 1.0)
    )

    estimate = MeterEstimate(
        beats_per_bar=best,
        phase=phases[best],
        confidence=confidence,
        scores={meter: round(value, 4) for meter, value in scores.items()},
    )
    logger.info(
        "meter_detected",
        extra={
            "beats_per_bar": estimate.beats_per_bar,
            "label": estimate.label,
            "phase": estimate.phase,
            "confidence": round(estimate.confidence, 3),
            "scores": estimate.scores,
        },
    )
    return estimate


# ---------------------------------------------------------------------------
# How the beat itself divides
# ---------------------------------------------------------------------------
#
# Separate question from bar length, and just as easy to get wrong. Straight
# music puts its offbeats halfway between beats; shuffles, swing and compound
# time put them a third of the way. Quantising a shuffle onto a duple grid
# drags every offbeat note about 40 ms early, which reads as sloppy timing
# rather than as the groove it is.
#
# This is measured directly from where onsets actually fall, rather than
# inferred from the meter — a 4/4 blues shuffle and a 6/8 ballad are different
# meters with the same answer.

#: Onsets closer than this to a grid position count as landing on it.
SUBDIVISION_TOLERANCE = 0.08

#: Triple has to beat duple by this much to be believed.
TRIPLE_MARGIN = 1.25

#: ...and this much of the offbeats must land on a third, per target position.
#: Without an absolute floor, scattered offbeats that land on nothing in
#: particular get read as triple. Real shuffled material concentrates hard;
#: noise does not.
MIN_TRIPLE_DENSITY = 0.20

#: Fraction of onsets that must be off-beat at all for the test to mean much.
MIN_OFFBEAT_SHARE = 0.12


@dataclass(frozen=True, slots=True)
class SubdivisionEstimate:
    """Whether the beat divides in two or in three."""

    triple: bool
    confidence: float
    duple_share: float
    triple_share: float

    @property
    def label(self) -> str:
        return "triple" if self.triple else "duple"


def detect_subdivision(beat_positions: np.ndarray) -> SubdivisionEstimate:
    """Decide how the beat subdivides from onset positions within the beat.

    `beat_positions` are onset times expressed in beats, at any scale — only
    the fractional part matters.
    """
    duple_default = SubdivisionEstimate(False, 0.0, 0.0, 0.0)
    if beat_positions.size < 32:
        return duple_default

    fractional = np.mod(beat_positions, 1.0)
    # Distance to the nearest beat, ignoring which side.
    from_beat = np.minimum(fractional, 1.0 - fractional)
    offbeat = from_beat > SUBDIVISION_TOLERANCE
    if offbeat.sum() < beat_positions.size * MIN_OFFBEAT_SHARE:
        # Almost everything is on the beat; there is nothing to judge.
        return duple_default

    candidates = fractional[offbeat]
    near_half = np.abs(candidates - 0.5) <= SUBDIVISION_TOLERANCE
    near_thirds = np.minimum(
        np.abs(candidates - 1.0 / 3.0), np.abs(candidates - 2.0 / 3.0)
    ) <= SUBDIVISION_TOLERANCE

    duple_share = float(near_half.mean())
    triple_share = float(near_thirds.mean())

    # Compare *densities*, not raw shares. The triple test accepts two target
    # positions (1/3 and 2/3) to duple's one, so it sweeps twice the width of
    # the beat — uniformly scattered offbeats score twice as "triple" as
    # "duple" purely from the geometry, before any music is involved.
    duple_density = duple_share
    triple_density = triple_share / 2.0

    triple = (
        triple_density >= MIN_TRIPLE_DENSITY
        and triple_density > duple_density * TRIPLE_MARGIN
    )
    dominant = max(duple_density, triple_density)
    other = min(duple_density, triple_density)
    confidence = float(np.clip((dominant - other) * 2.0, 0.0, 1.0))

    estimate = SubdivisionEstimate(
        triple=triple,
        confidence=confidence,
        duple_share=round(duple_share, 4),
        triple_share=round(triple_share, 4),
    )
    logger.info(
        "subdivision_detected",
        extra={
            "family": estimate.label,
            "duple_share": estimate.duple_share,
            "triple_share": estimate.triple_share,
            "confidence": round(estimate.confidence, 3),
        },
    )
    return estimate
