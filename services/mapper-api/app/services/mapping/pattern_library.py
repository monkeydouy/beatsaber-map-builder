"""Pattern definitions, expressed as data.

The generator never invents a note position from scratch — it instantiates a
pattern. A pattern is a short sequence of *steps*; each step says which hand
swings, roughly where, and (optionally) in which direction. Leaving direction
as `None` hands the decision to the parity engine, which is how the same
pattern stays playable in different parity contexts.

Columns are written as an *offset from the playing hand's own outer lane*, not
as an absolute column: 0 is home (lane 0 for red, lane 3 for blue), 1 the inner
lane of its own half, 2 a lean over the centre line, 3 a full cross-body reach.

That is the difference between a pattern describing a shape and a pattern
describing a picture. Written absolutely, every step's column belonged to
whichever hand happened to land on it, so a step at column 2 arrived paired
with a step at column 1 and *both* hands crossed at once — correctly scored as
bad, and leaving no way to say the thing human mappers do constantly: slide one
hand inward while the other stays home. It also meant half of every pattern's
lead/mirror variants put both hands on the wrong sides of the body, burning
candidate budget on shapes that could never win.

**Read the steps in pairs, not in sequence.** Hands alternate, so one hand gets
steps 0, 2, 4... and the other gets 1, 3, 5... A pattern written `DOWN, UP,
DOWN, UP` reads beautifully as a list and is unplayable: it asks one hand for
`DOWN, DOWN, DOWN`. Explicit directions must oppose *within each hand's own
subsequence*, including where the pattern wraps around to repeat.
`tests/test_patterns.py` enforces this for every definition here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.models.enums import CutDirection, Difficulty, MappingStyle

#: How a step chooses its hand.
LEAD = "lead"  # the hand currently leading the run
OFF = "off"  # the other hand
BOTH = "both"  # a double


@dataclass(frozen=True, slots=True)
class PatternStep:
    """One slot in a pattern."""

    role: str = LEAD
    #: Columns inward from the playing hand's home lane (0-3). `None` lets the
    #: generator keep the hand where it is.
    offset: int | None = None
    #: Row template (0-2).
    row: int | None = 1
    #: Explicit cut direction, or `None` to let parity decide.
    direction: CutDirection | None = None
    #: Offset for the off hand when `role` is BOTH.
    partner_offset: int | None = None
    partner_row: int | None = None
    partner_direction: CutDirection | None = None
    #: When True the leading hand swaps after this step.
    swap_lead: bool = True


@dataclass(frozen=True, slots=True)
class PatternDefinition:
    """A reusable rhythmic/positional shape with difficulty metadata."""

    name: str
    steps: tuple[PatternStep, ...]
    min_difficulty: Difficulty
    max_difficulty: Difficulty | None = None
    #: 0-1; compared against `DifficultyProfile.pattern_complexity`.
    complexity: float = 0.3
    #: 0-1; how much grid travel the pattern asks for.
    movement_score: float = 0.4
    #: Beats of clear space the pattern wants after it.
    required_recovery: float = 0.0
    #: Multiplier per style; missing entries default to 1.0.
    style_affinity: dict[MappingStyle, float] | None = None
    allow_mirror: bool = True
    #: Free-form tags used by the generator (``double``, ``technical``, ...).
    tags: frozenset[str] = frozenset()

    @property
    def length(self) -> int:
        return len(self.steps)

    def affinity(self, style: MappingStyle) -> float:
        if not self.style_affinity:
            return 1.0
        return self.style_affinity.get(style, 1.0)

    def allows(self, difficulty: Difficulty, complexity_cap: float) -> bool:
        if difficulty.rank < self.min_difficulty.rank:
            return False
        if self.max_difficulty is not None and difficulty.rank > self.max_difficulty.rank:
            return False
        return self.complexity <= complexity_cap + 1e-6


#: Readable names for the four offsets a step can name.
HOME, INNER, LEAN, CROSS = 0, 1, 2, 3


def _alt(
    offset: int | None, row: int | None = 1, direction: CutDirection | None = None
) -> PatternStep:
    return PatternStep(role=LEAD, offset=offset, row=row, direction=direction)


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

PATTERNS: tuple[PatternDefinition, ...] = (
    # -- foundational ------------------------------------------------------
    PatternDefinition(
        name="SimpleAlternation",
        steps=(_alt(INNER, 0), _alt(INNER, 0)),
        min_difficulty=Difficulty.EASY,
        complexity=0.10,
        movement_score=0.20,
        tags=frozenset({"basic"}),
    ),
    PatternDefinition(
        name="VerticalFlow",
        steps=(
            _alt(HOME, 0, CutDirection.DOWN),
            _alt(HOME, 0, CutDirection.DOWN),
            _alt(INNER, 1, CutDirection.UP),
            _alt(INNER, 1, CutDirection.UP),
        ),
        min_difficulty=Difficulty.EASY,
        complexity=0.18,
        movement_score=0.30,
        tags=frozenset({"basic", "vertical"}),
    ),
    PatternDefinition(
        name="OuterAlternation",
        steps=(_alt(HOME, 0), _alt(HOME, 0), _alt(HOME, 1), _alt(HOME, 1)),
        min_difficulty=Difficulty.EASY,
        complexity=0.14,
        movement_score=0.65,
        style_affinity={MappingStyle.DANCE: 1.5},
        tags=frozenset({"basic", "wide"}),
    ),
    PatternDefinition(
        name="MidLift",
        steps=(_alt(INNER, 1), _alt(INNER, 1), _alt(HOME, 0), _alt(HOME, 0)),
        min_difficulty=Difficulty.EASY,
        complexity=0.12,
        movement_score=0.28,
        tags=frozenset({"basic", "vertical"}),
    ),
    PatternDefinition(
        name="StepOut",
        steps=(_alt(INNER, 0), _alt(HOME, 1), _alt(LEAN, 0), _alt(CROSS, 1)),
        min_difficulty=Difficulty.EASY,
        complexity=0.20,
        movement_score=0.55,
        tags=frozenset({"basic"}),
    ),
    PatternDefinition(
        name="WideAlternation",
        steps=(_alt(HOME, 0), _alt(HOME, 0), _alt(INNER, 1), _alt(INNER, 1)),
        min_difficulty=Difficulty.EASY,
        complexity=0.22,
        movement_score=0.70,
        style_affinity={MappingStyle.DANCE: 1.6, MappingStyle.TECHNICAL: 0.8},
        tags=frozenset({"wide"}),
    ),
    # -- diagonal / flow ----------------------------------------------------
    PatternDefinition(
        name="DiagonalAlternation",
        steps=(
            _alt(HOME, 0, CutDirection.DOWN_LEFT),
            _alt(HOME, 0, CutDirection.DOWN_RIGHT),
            _alt(INNER, 1, CutDirection.UP_RIGHT),
            _alt(INNER, 1, CutDirection.UP_LEFT),
        ),
        min_difficulty=Difficulty.NORMAL,
        complexity=0.35,
        movement_score=0.45,
        tags=frozenset({"diagonal"}),
    ),
    PatternDefinition(
        name="HorizontalFlow",
        steps=(
            _alt(HOME, 1, CutDirection.RIGHT),
            _alt(HOME, 1, CutDirection.LEFT),
            _alt(INNER, 1, CutDirection.LEFT),
            _alt(INNER, 1, CutDirection.RIGHT),
        ),
        min_difficulty=Difficulty.NORMAL,
        complexity=0.40,
        movement_score=0.75,
        style_affinity={MappingStyle.DANCE: 1.5},
        tags=frozenset({"horizontal", "wide"}),
    ),
    # Every other alternating pattern here is symmetric about the centre
    # line, so a lane-2 step always arrives paired with a lane-1 step and both
    # hands cross at once — which is correctly scored as bad, and left the
    # generator with no way at all to express what human maps do constantly:
    # slide the pair sideways so one hand leans over while the other stays
    # home. Red played lane 2 in 3% of our notes against 22% in the corpus.
    PatternDefinition(
        name="SlideAcross",
        steps=(_alt(INNER, 1), _alt(INNER, 1), _alt(LEAN, 0), _alt(HOME, 0)),
        min_difficulty=Difficulty.NORMAL,
        complexity=0.38,
        movement_score=0.50,
        style_affinity={MappingStyle.DANCE: 1.3},
        tags=frozenset({"slide"}),
    ),
    PatternDefinition(
        name="WalkIn",
        steps=(_alt(HOME, 1), _alt(HOME, 1), _alt(INNER, 0), _alt(HOME, 0), _alt(LEAN, 1), _alt(HOME, 1)),
        min_difficulty=Difficulty.HARD,
        complexity=0.58,
        movement_score=0.62,
        required_recovery=0.5,
        tags=frozenset({"slide", "stream"}),
    ),
    # Only expressible now that columns are hand-relative: one hand leans over
    # the centre line while the other holds its home lane. Written absolutely
    # this was lanes (2, 3) — sayable, but every mirror and lead variant of it
    # turned into both hands crossing at once, so it never survived scoring.
    PatternDefinition(
        name="LeanAndHold",
        steps=(_alt(LEAN, 1), _alt(HOME, 1), _alt(LEAN, 0), _alt(HOME, 0)),
        min_difficulty=Difficulty.NORMAL,
        complexity=0.34,
        movement_score=0.55,
        tags=frozenset({"slide"}),
    ),
    PatternDefinition(
        name="LeanTrade",
        steps=(
            _alt(HOME, 0, CutDirection.DOWN),
            _alt(LEAN, 1, CutDirection.UP),
            _alt(LEAN, 1, CutDirection.UP),
            _alt(HOME, 0, CutDirection.DOWN),
        ),
        min_difficulty=Difficulty.HARD,
        complexity=0.48,
        movement_score=0.68,
        style_affinity={MappingStyle.TECHNICAL: 1.3},
        tags=frozenset({"slide", "diagonal"}),
    ),
    PatternDefinition(
        name="StaircaseUp",
        steps=(_alt(HOME, 0), _alt(LEAN, 1), _alt(LEAN, 1), _alt(HOME, 2)),
        min_difficulty=Difficulty.NORMAL,
        complexity=0.45,
        movement_score=0.60,
        tags=frozenset({"staircase"}),
    ),
    # -- streams ------------------------------------------------------------
    PatternDefinition(
        name="ShortStream",
        steps=(_alt(HOME, 1), _alt(HOME, 1), _alt(INNER, 0), _alt(INNER, 0), _alt(LEAN, 1), _alt(LEAN, 1)),
        min_difficulty=Difficulty.HARD,
        complexity=0.55,
        movement_score=0.40,
        required_recovery=0.5,
        tags=frozenset({"stream"}),
    ),
    PatternDefinition(
        name="RollingStream",
        steps=(
            _alt(INNER, 0, CutDirection.DOWN),
            _alt(INNER, 0, CutDirection.DOWN),
            _alt(LEAN, 1, CutDirection.UP),
            _alt(LEAN, 1, CutDirection.UP),
        ),
        min_difficulty=Difficulty.HARD,
        complexity=0.62,
        movement_score=0.50,
        required_recovery=0.5,
        tags=frozenset({"stream"}),
    ),
    PatternDefinition(
        name="WideStream",
        steps=(_alt(HOME, 1), _alt(HOME, 1), _alt(INNER, 0), _alt(INNER, 0), _alt(HOME, 0), _alt(HOME, 0)),
        min_difficulty=Difficulty.EXPERT,
        complexity=0.72,
        movement_score=0.85,
        required_recovery=0.75,
        style_affinity={MappingStyle.DANCE: 1.45},
        tags=frozenset({"stream", "wide"}),
    ),
    # -- technical ----------------------------------------------------------
    PatternDefinition(
        name="TechnicalRotation",
        steps=(
            _alt(INNER, 1, CutDirection.DOWN_RIGHT),
            _alt(LEAN, 1, CutDirection.DOWN_LEFT),
            _alt(LEAN, 2, CutDirection.UP_LEFT),
            _alt(INNER, 0, CutDirection.UP_RIGHT),
        ),
        min_difficulty=Difficulty.EXPERT,
        complexity=0.80,
        movement_score=0.55,
        required_recovery=0.5,
        style_affinity={MappingStyle.TECHNICAL: 1.7, MappingStyle.DANCE: 0.55},
        tags=frozenset({"technical"}),
    ),
    PatternDefinition(
        name="InwardSweep",
        steps=(
            _alt(HOME, 0, CutDirection.DOWN_RIGHT),
            _alt(HOME, 0, CutDirection.DOWN_LEFT),
            _alt(LEAN, 2, CutDirection.UP_LEFT),
            _alt(LEAN, 2, CutDirection.UP_RIGHT),
        ),
        min_difficulty=Difficulty.HARD,
        complexity=0.66,
        movement_score=0.80,
        style_affinity={MappingStyle.DANCE: 1.4},
        tags=frozenset({"wide", "sweep"}),
    ),
    # Setup, cross, escape -- read straight off the reference maps. Across 110
    # visits to the far lane, every single one lasts exactly one note: the hand
    # comes from home, reaches across, and goes straight back. The other hand
    # holds its own home lane throughout so the arms never tangle, and the
    # whole gesture wants a beat either side, which is why this is written long
    # and tagged for the scorer to keep out of fast passages.
    PatternDefinition(
        name="CrossAndReturn",
        steps=(
            _alt(HOME, 1),
            _alt(HOME, 1),
            _alt(CROSS, 0),
            _alt(HOME, 1),
            _alt(HOME, 0),
            _alt(HOME, 0),
        ),
        min_difficulty=Difficulty.HARD,
        complexity=0.60,
        movement_score=0.90,
        required_recovery=1.0,
        style_affinity={MappingStyle.DANCE: 1.3, MappingStyle.TECHNICAL: 1.2},
        tags=frozenset({"crossover", "wide"}),
    ),
    PatternDefinition(
        name="ComplexCrossoverStream",
        steps=(
            _alt(LEAN, 1),
            _alt(LEAN, 1),
            _alt(CROSS, 0),
            _alt(CROSS, 0),
            _alt(LEAN, 0),
            _alt(LEAN, 0),
        ),
        min_difficulty=Difficulty.EXPERT_PLUS,
        complexity=0.95,
        movement_score=0.95,
        required_recovery=1.0,
        style_affinity={MappingStyle.TECHNICAL: 1.6, MappingStyle.DANCE: 0.7},
        tags=frozenset({"stream", "crossover", "technical"}),
    ),
    PatternDefinition(
        name="TowerTap",
        steps=(
            _alt(INNER, 0, CutDirection.DOWN),
            _alt(INNER, 0, CutDirection.DOWN),
            _alt(INNER, 2, CutDirection.UP),
            _alt(INNER, 2, CutDirection.UP),
        ),
        min_difficulty=Difficulty.EXPERT,
        complexity=0.78,
        movement_score=0.65,
        style_affinity={MappingStyle.TECHNICAL: 1.4},
        tags=frozenset({"technical", "vertical"}),
    ),
    # -- accents ------------------------------------------------------------
    PatternDefinition(
        name="Double",
        steps=(
            PatternStep(
                role=BOTH,
                offset=INNER,
                row=0,
                direction=CutDirection.DOWN,
                partner_offset=INNER,
                partner_row=0,
                partner_direction=CutDirection.DOWN,
                swap_lead=False,
            ),
        ),
        min_difficulty=Difficulty.NORMAL,
        complexity=0.30,
        movement_score=0.25,
        required_recovery=0.5,
        allow_mirror=False,
        tags=frozenset({"double", "accent"}),
    ),
    PatternDefinition(
        name="WideDouble",
        steps=(
            PatternStep(
                role=BOTH,
                offset=HOME,
                row=1,
                direction=CutDirection.DOWN_LEFT,
                partner_offset=HOME,
                partner_row=1,
                partner_direction=CutDirection.DOWN_RIGHT,
                swap_lead=False,
            ),
        ),
        min_difficulty=Difficulty.HARD,
        complexity=0.45,
        movement_score=0.80,
        required_recovery=0.5,
        allow_mirror=False,
        style_affinity={MappingStyle.DANCE: 1.7},
        tags=frozenset({"double", "accent", "wide"}),
    ),
    PatternDefinition(
        name="BurstTriplet",
        steps=(_alt(INNER, 1), _alt(INNER, 1), _alt(INNER, 0)),
        min_difficulty=Difficulty.HARD,
        complexity=0.58,
        movement_score=0.35,
        required_recovery=1.0,
        tags=frozenset({"burst", "accent"}),
    ),
    PatternDefinition(
        name="JumpAccent",
        steps=(
            PatternStep(
                role=BOTH,
                offset=INNER,
                row=2,
                direction=CutDirection.UP,
                partner_offset=INNER,
                partner_row=2,
                partner_direction=CutDirection.UP,
                swap_lead=False,
            ),
        ),
        min_difficulty=Difficulty.EXPERT,
        complexity=0.60,
        movement_score=0.50,
        required_recovery=0.75,
        allow_mirror=False,
        tags=frozenset({"double", "accent"}),
    ),
    # -- recovery -----------------------------------------------------------
    PatternDefinition(
        name="RecoverySwing",
        steps=(_alt(INNER, 0, CutDirection.DOWN), _alt(INNER, 1, CutDirection.UP)),
        min_difficulty=Difficulty.EASY,
        complexity=0.08,
        movement_score=0.18,
        tags=frozenset({"recovery", "basic"}),
    ),
)

PATTERNS_BY_NAME = {pattern.name: pattern for pattern in PATTERNS}


def eligible_patterns(
    difficulty: Difficulty,
    complexity_cap: float,
    *,
    exclude_tags: Sequence[str] = (),
    require_tags: Sequence[str] = (),
) -> list[PatternDefinition]:
    """Filter the library down to patterns valid for a difficulty."""
    excluded = frozenset(exclude_tags)
    required = frozenset(require_tags)
    result = []
    for pattern in PATTERNS:
        if not pattern.allows(difficulty, complexity_cap):
            continue
        if excluded & pattern.tags:
            continue
        if required and not required <= pattern.tags:
            continue
        result.append(pattern)
    return result
