"""Tunable weights for the mapping engine.

Everything the generator "prefers" lives here as data, so the map's character
can be retuned without touching an algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.models.enums import MappingStyle


@dataclass(frozen=True, slots=True)
class ScoringWeights:
    """Weights applied to candidate pattern scoring.

    Positive fields reward; `*_penalty` fields are subtracted.
    """

    #: Raised alongside `movement`: rewarding travel made the generator shy
    #: of two-hand accents, which are stationary by nature. This is what
    #: buys them back, and it buys them back by tying notes to the music
    #: rather than by simply moving less.
    musical_match: float = 1.35
    flow: float = 1.25
    parity: float = 2.10
    position: float = 0.75
    #: Both of these push a hand off the cell it is on. Real maps leave it
    #: there for a fifth of all swings — the hand holds still and only the
    #: cut direction changes. That is where a player gets to breathe.
    variety: float = 0.55
    style: float = 0.80
    #: Reward for covering ground between notes. Deliberately small: pushed
    #: up, it bought sideways travel at the cost of every rest in the map —
    #: half the reference maps' share of stay-put swings and 1.6x their share
    #: of long reaches, which plays as arms spread from start to finish.
    movement: float = 0.60
    #: Pull toward each hand's natural columns. See
    #: `parity_engine.lane_affinity`.
    lane_spread: float = 2.00
    #: Pull each column toward the row real maps put it on — centre
    #: columns low, outer columns at mid height. See
    #: `parity_engine.height_affinity`.
    height_shape: float = 1.00

    repetition_penalty: float = 0.95
    vision_block_penalty: float = 0.85
    awkward_transition_penalty: float = 1.15
    hand_cross_penalty: float = 0.90
    #: Charged when a swing has to work around where the *other* saber
    #: just went. See `parity_engine.handoff_cost`.
    handoff_penalty: float = 2.50
    density_penalty: float = 0.70
    reset_penalty: float = 1.30
    stack_penalty: float = 2.20


@dataclass(frozen=True, slots=True)
class MappingConfig:
    """Global generator behaviour, independent of the selected difficulty."""

    weights: ScoringWeights = field(default_factory=ScoringWeights)

    #: How many pattern candidates to score per rhythmic run.
    min_candidates: int = 6
    max_candidates: int = 20

    #: Sliding windows (seconds) used by the difficulty controller.
    nps_windows: tuple[float, ...] = (1.0, 2.0, 4.0)

    #: Recent-pattern memory used for the repetition penalty.
    repetition_memory: int = 6

    #: A run ends when two selected events are further apart than this.
    run_gap_beats: float = 1.75

    #: Notes closer together than this are treated as simultaneous.
    simultaneous_epsilon: float = 1e-3

    #: Seconds of silence to leave before the first note. Real maps do this:
    #: across a corpus of community levels the median gap before the first
    #: note is 4.3 s. The player needs time to see the track start moving,
    #: settle their grip and find the tempo before anything arrives.
    lead_in_seconds: float = 4.0

    enable_bombs: bool = False
    enable_walls: bool = True
    enable_lighting: bool = True


#: Style modifiers multiply into the base weights.
STYLE_WEIGHT_MODIFIERS: dict[MappingStyle, dict[str, float]] = {
    MappingStyle.BALANCED: {},
    MappingStyle.DANCE: {
        "movement": 1.65,
        "flow": 1.25,
        "position": 1.20,
        "awkward_transition_penalty": 1.45,
        "hand_cross_penalty": 0.75,
        "variety": 0.85,
    },
    MappingStyle.TECHNICAL: {
        "variety": 1.55,
        "musical_match": 1.15,
        "movement": 0.70,
        "awkward_transition_penalty": 0.80,
        "hand_cross_penalty": 0.65,
        "repetition_penalty": 1.35,
    },
}


#: Style bias applied to a pattern's *selection* weight by tag. Steering the
#: candidate pool is what actually changes a map's character; scoring weights
#: alone only re-rank whatever shapes happened to be offered.
STYLE_TAG_BIAS: dict[MappingStyle, dict[str, float]] = {
    MappingStyle.BALANCED: {},
    MappingStyle.DANCE: {
        "wide": 2.10,
        "horizontal": 1.80,
        "sweep": 1.70,
        "technical": 0.35,
        "crossover": 0.45,
        "vertical": 0.70,
    },
    MappingStyle.TECHNICAL: {
        "technical": 2.60,
        "crossover": 1.80,
        "stream": 1.35,
        "diagonal": 1.40,
        "wide": 0.65,
        "basic": 0.55,
    },
}


def tag_bias(style: MappingStyle, tags: frozenset[str]) -> float:
    """Combined selection bias for a pattern's tags under a style."""
    biases = STYLE_TAG_BIAS.get(style, {})
    if not biases:
        return 1.0
    multiplier = 1.0
    for tag in tags:
        multiplier *= biases.get(tag, 1.0)
    return multiplier


def weights_for_style(base: ScoringWeights, style: MappingStyle) -> ScoringWeights:
    """Apply a style's multipliers to the base weight set."""
    modifiers = STYLE_WEIGHT_MODIFIERS.get(style, {})
    if not modifiers:
        return base
    updates = {
        name: getattr(base, name) * multiplier for name, multiplier in modifiers.items()
    }
    return replace(base, **updates)
