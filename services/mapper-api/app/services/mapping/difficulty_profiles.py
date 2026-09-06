"""Difficulty profiles: the hard boundaries each difficulty may not escape.

Intensity interpolates *within* a profile. That separation is what stops
"Expert at intensity 1.0" from quietly becoming an Expert+ map — the ceilings
belong to the profile, and intensity only chooses a position between the
profile's own floor and ceiling.

**The NPS bands and note jump speeds here are calibrated, not invented.** They
come from `tests/fixtures/ranked_corpus.json`: 716 community-ranked, human-made
Standard maps with no Noodle Extensions, Mapping Extensions or Chroma, pulled
from BeatSaver's public metadata. Each difficulty targets the interquartile
range of what real mappers ship under that label — `target_nps_min` is the
corpus p25 and `target_nps_max` the p75 — so default settings land near the
middle of the real distribution rather than at its bottom edge.

Two things the corpus could *not* calibrate, and were left to judgement:

* **Local peaks.** The corpus reports whole-map average NPS, which says nothing
  about density inside any one second. `peak_nps` stays at roughly 1.75x the
  target ceiling.
* **Wall frequency.** The corpus median is 34-47 obstacles per minute, which
  is not a dodging course — it is decoration. Many maps use large numbers of
  tiny or off-grid walls for visual effect, and the count cannot distinguish
  those from a wall the player must actually avoid. Matching that number would
  have been calibration in name only, so wall frequency remains deliberately
  conservative. See `tools/fetch_ranked_corpus.py` to reproduce the corpus.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.models.enums import Difficulty


@dataclass(frozen=True, slots=True)
class DifficultyProfile:
    """Every knob the generator is allowed to turn for one difficulty."""

    difficulty: Difficulty

    #: Average notes-per-second the map should land between.
    target_nps_min: float
    target_nps_max: float
    #: Hard ceiling on any rolling 1-second window.
    peak_nps: float
    #: Hard ceiling on the 4-second rolling average.
    sustained_nps: float

    #: Beat subdivisions (denominators) the rhythm selector may use.
    allowed_subdivisions: tuple[int, ...]
    #: Minimum spacing between consecutive rhythm slots, in seconds — the
    #: instantaneous reaction floor. It is the reciprocal of `peak_nps`: a
    #: profile whose spacing floor is slower than its own one-second ceiling
    #: can never reach that ceiling, which made the ceiling a fiction.
    min_note_interval: float

    pattern_complexity: float  # 0-1 cap on pattern complexity metadata
    movement_scale: float  # multiplier on tolerated grid travel
    crossover_probability: float
    double_probability: float
    wall_probability: float
    #: Seconds of eased density inserted after a sustained dense passage.
    recovery_seconds: float
    #: Dense passages longer than this force a recovery window.
    max_sustained_seconds: float
    technical_pattern_probability: float
    vision_block_tolerance: float
    #: Minimum gap (beats) before a parity reset is tolerated at all.
    reset_min_gap_beats: float
    #: Fraction of notes allowed to use the ANY cut direction.
    any_direction_share: float
    #: How much a pure sideways cut is worth against a diagonal covering the
    #: same travel. Sideways movement aligns perfectly with LEFT/RIGHT, so
    #: unweighted they win every lateral transition. Real maps use them as a
    #: readable gesture at the easier difficulties and almost never once the
    #: density leaves no time for a parity-neutral flick: across the reference
    #: maps horizontals are 7.8% of Easy notes, 14.1% of Normal and 11.2% of
    #: Hard -- but only 1.6% of Expert and 2.4% of Expert+.
    horizontal_cut_bias: float
    #: Rows the generator may place notes on (Easy stays out of the top row).
    allowed_rows: tuple[int, ...]
    #: Furthest a hand may be sent from its own outer lane: 1 keeps it strictly
    #: on its own half, 2 allows a lean over the centre line, 3 the full
    #: cross-body reach. The reference maps draw this line sharply -- red plays
    #: lane 2 for 0.0% of Easy notes and 0.2% of Normal, then 6% of Hard and
    #: 22% of Expert+.
    #:
    #: Declared rather than inferred. This used to fall out of comparing the
    #: intensity-scaled `crossover_probability` against a fixed 0.02, and
    #: Normal's scaled value straddles it: crossing turned on or off depending
    #: on where the intensity slider sat, which is not something a difficulty
    #: should decide by accident.
    max_offset: int
    #: What a lean over the centre line costs this difficulty, 0.0-1.0. Home
    #: and the hand's own inner lane are always free, the far lane always
    #: costs everything; only the lean is a matter of taste, and the reference
    #: maps have very different taste at each level -- red is in lane 2 for 6%
    #: of their Hard notes, 16% of Expert and 22% of Expert+. A single global
    #: value put Hard at 18%, which reads as an Expert map played slowly.
    lean_cost: float
    #: Base note jump speed before BPM/density adjustment.
    base_njs: float
    #: Seconds a note should be visible before it must be cut. Drives the
    #: note-jump offset so readability holds across tempos.
    reaction_time: float

    def interpolated_nps(self, intensity: float) -> float:
        """Effective average NPS target for a given intensity (0-1)."""
        intensity = min(max(intensity, 0.0), 1.0)
        return self.target_nps_min + (self.target_nps_max - self.target_nps_min) * intensity

    def scaled(self, intensity: float) -> DifficultyProfile:
        """Return a copy with intensity-interpolated soft knobs.

        Hard ceilings (`peak_nps`, `allowed_subdivisions`, `min_note_interval`,
        `pattern_complexity`) are deliberately left untouched — they define the
        difficulty class. `pattern_complexity` in particular is a *pool filter*:
        scaling it down would delete whole pattern shapes from a difficulty's
        vocabulary, which is how Easy ended up placing every note in two
        centre columns. Intensity biases which of the available shapes get
        chosen, via the scorer, rather than removing any of them.
        """
        intensity = min(max(intensity, 0.0), 1.0)
        # Map 0-1 onto 0.55-1.15 so mid intensity is the profile's natural feel.
        factor = 0.55 + 0.60 * intensity
        return replace(
            self,
            crossover_probability=self.crossover_probability * factor,
            double_probability=self.double_probability * factor,
            wall_probability=self.wall_probability * factor,
            technical_pattern_probability=self.technical_pattern_probability * factor,
            movement_scale=self.movement_scale * (0.82 + 0.30 * intensity),
            recovery_seconds=self.recovery_seconds * (1.25 - 0.35 * intensity),
        )


PROFILES: dict[Difficulty, DifficultyProfile] = {
    Difficulty.EASY: DifficultyProfile(
        difficulty=Difficulty.EASY,
        target_nps_min=1.4,
        target_nps_max=2.7,
        peak_nps=4.0,
        sustained_nps=3.3,
        allowed_subdivisions=(1, 2),
        min_note_interval=0.25,
        pattern_complexity=0.25,
        movement_scale=0.55,
        crossover_probability=0.0,
        double_probability=0.04,
        wall_probability=0.05,
        recovery_seconds=2.5,
        max_sustained_seconds=8.0,
        technical_pattern_probability=0.0,
        vision_block_tolerance=0.10,
        reset_min_gap_beats=2.0,
        any_direction_share=0.14,
        horizontal_cut_bias=0.55,
        allowed_rows=(0, 1),
        max_offset=1,
        lean_cost=1.0,  # Cannot lean at all (max_offset), so this only records the intent.
        base_njs=11.5,
        reaction_time=1.15,
    ),
    Difficulty.NORMAL: DifficultyProfile(
        difficulty=Difficulty.NORMAL,
        target_nps_min=2.1,
        target_nps_max=3.9,
        peak_nps=5.6,
        sustained_nps=4.6,
        allowed_subdivisions=(1, 2),
        min_note_interval=0.179,
        pattern_complexity=0.42,
        movement_scale=0.72,
        crossover_probability=0.02,
        double_probability=0.08,
        wall_probability=0.10,
        recovery_seconds=2.0,
        max_sustained_seconds=10.0,
        technical_pattern_probability=0.05,
        vision_block_tolerance=0.20,
        reset_min_gap_beats=1.5,
        any_direction_share=0.10,
        horizontal_cut_bias=0.70,
        allowed_rows=(0, 1, 2),
        max_offset=1,
        lean_cost=1.0,  # Cannot lean at all (max_offset), so this only records the intent.
        base_njs=13.5,
        reaction_time=1.05,
    ),
    Difficulty.HARD: DifficultyProfile(
        difficulty=Difficulty.HARD,
        target_nps_min=3.0,
        target_nps_max=5.1,
        peak_nps=7.2,
        sustained_nps=6.0,
        allowed_subdivisions=(1, 2, 4),
        min_note_interval=0.139,
        pattern_complexity=0.62,
        movement_scale=0.88,
        crossover_probability=0.06,
        double_probability=0.12,
        wall_probability=0.16,
        recovery_seconds=1.6,
        max_sustained_seconds=13.0,
        technical_pattern_probability=0.18,
        vision_block_tolerance=0.32,
        reset_min_gap_beats=1.0,
        any_direction_share=0.06,
        horizontal_cut_bias=0.20,
        allowed_rows=(0, 1, 2),
        max_offset=3,
        lean_cost=0.68,  # Reference maps: red is in lane 2 for 6% of Hard notes.
        base_njs=14.5,
        reaction_time=0.95,
    ),
    Difficulty.EXPERT: DifficultyProfile(
        difficulty=Difficulty.EXPERT,
        target_nps_min=3.9,
        target_nps_max=6.6,
        peak_nps=9.2,
        sustained_nps=7.7,
        allowed_subdivisions=(1, 2, 4),
        min_note_interval=0.109,
        pattern_complexity=0.80,
        movement_scale=1.0,
        crossover_probability=0.12,
        double_probability=0.16,
        wall_probability=0.20,
        recovery_seconds=1.2,
        max_sustained_seconds=16.0,
        technical_pattern_probability=0.35,
        vision_block_tolerance=0.45,
        reset_min_gap_beats=0.75,
        any_direction_share=0.03,
        horizontal_cut_bias=0.08,
        allowed_rows=(0, 1, 2),
        max_offset=3,
        lean_cost=0.25,  # ...16% of Expert.
        base_njs=16.5,
        reaction_time=0.85,
    ),
    Difficulty.EXPERT_PLUS: DifficultyProfile(
        difficulty=Difficulty.EXPERT_PLUS,
        target_nps_min=5.4,
        target_nps_max=8.5,
        peak_nps=11.9,
        sustained_nps=9.9,
        allowed_subdivisions=(1, 2, 3, 4, 6),
        min_note_interval=0.084,
        pattern_complexity=1.0,
        movement_scale=1.18,
        crossover_probability=0.20,
        double_probability=0.20,
        wall_probability=0.22,
        recovery_seconds=0.9,
        max_sustained_seconds=22.0,
        technical_pattern_probability=0.55,
        vision_block_tolerance=0.55,
        reset_min_gap_beats=0.5,
        any_direction_share=0.02,
        horizontal_cut_bias=0.07,
        allowed_rows=(0, 1, 2),
        max_offset=3,
        lean_cost=0.0,  # ...and 22% of Expert+, where the lean is simply free.
        base_njs=18.5,
        reaction_time=0.72,
    ),
}

DIFFICULTY_DESCRIPTIONS: dict[Difficulty, str] = {
    Difficulty.EASY: "Relaxed patterns focused on timing and readability.",
    Difficulty.NORMAL: "Comfortable flowing patterns with moderate rhythm variation.",
    Difficulty.HARD: "Faster patterns with increased movement and complexity.",
    Difficulty.EXPERT: "Fast, flowing patterns with moderate technical complexity.",
    Difficulty.EXPERT_PLUS: "Dense and technically demanding patterns for advanced players.",
}


def get_profile(difficulty: Difficulty, intensity: float = 0.5) -> DifficultyProfile:
    """Look up a profile and interpolate its soft knobs for `intensity`."""
    return PROFILES[difficulty].scaled(intensity)
