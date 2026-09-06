"""Swing parity modelling.

Beat Saber is playable because swings alternate: a downward cut leaves the
saber below the hand (backhand), and the natural next swing is upward. Two
downward cuts in a row with the same hand force a "reset" — the player must
re-cock the wrist in the gap between notes. Occasionally that is a deliberate
mapping device; frequently it is unplayable.

This module tracks each hand's approximate state and scores transitions. It is
a heuristic, not a biomechanical simulation: the goal is to reliably reject
sequences that feel wrong, not to model the human arm.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.models.beatmap import GRID_COLUMNS, GRID_ROWS
from app.models.enums import (
    DIRECTION_ANGLES,
    DIRECTION_VECTORS,
    DOWNWARD_CUTS,
    HORIZONTAL_CUTS,
    UPWARD_CUTS,
    CutDirection,
    Hand,
    Parity,
)

#: A hand may not swing again sooner than this (beats), whatever the profile.
ABSOLUTE_MIN_SAME_HAND_GAP = 0.115

#: A swing must oppose the one before it by at least this angle. Below it the
#: hand is being asked to travel the same way twice without a return stroke,
#: which is a reset whatever the wrist is doing.
#:
#: The forehand/backhand model alone does not catch this. It treats horizontal
#: cuts as parity-neutral, so "left hand cuts RIGHT, then RIGHT again" passed
#: every check while being impossible to play smoothly — you have to bring the
#: saber back across before you can swing across again.
OPPOSING_SWING_ANGLE = 90.0

#: Swings that oppose by this much or more flow with no wrist work at all.
IDEAL_OPPOSING_ANGLE = 180.0

#: What a swing must clear when the hand has no time. A right-angle turn is
#: comfortable with a beat to make it and awkward at speed, so the requirement
#: slides between the two: 90 deg is conditional, not simply allowed.
#:
#: Measured against the reference maps, 99.5% of their fast same-hand
#: transitions turn by 135 deg or more. Ours sat at 86%, and the shortfall was
#: almost entirely same-side diagonal pairs — UP_RIGHT into DOWN_RIGHT, which
#: reverses the wrist but carries the arm on across the body the same way.
STRICT_OPPOSING_ANGLE = 135.0

#: Gap (beats) at which the requirement has relaxed all the way back to 90.
RELAXED_OPPOSITION_GAP = 0.75

#: Charge for falling short of the angle the gap asks for. This is a scoring
#: preference, not a rejection: `opposes` still draws the hard line at 90.
SHALLOW_TURN_PENALTY = 3.6

#: Charge for placing a note further along the direction just swung.
#:
#: A block sits on the *return* path, not the follow-through. After a DOWN cut
#: the hand finishes low and the next swing is UP, so the next block belongs
#: above where the hand ended — the upward swing passes through it on the way.
#: Putting it further down instead asks the player to chase a block with an arm
#: already travelling away from it, and there is no swing that reaches it
#: without a stop and a reposition.
#:
#: This is the one rule that reads backwards from the obvious: "place the next
#: note where the arm is heading" is wrong. Across the reference maps only 1.9%
#: of same-hand follow-ups sit along the previous swing; ours sat at 13.4%.
#:
#: Kept moderate on purpose. It once carried this on its own and needed to be
#: enormous to do it; the generator now steps a block off the swing path when
#: it places one, so this only has to break ties between candidates.
CHASE_PENALTY = 5.0


@dataclass(frozen=True, slots=True)
class HandState:
    """Approximate state of one saber between swings."""

    hand: Hand
    parity: Parity = Parity.FOREHAND
    last_beat: float = -99.0
    last_x: int = 1
    last_y: int = 1
    last_direction: CutDirection | None = None
    #: Consecutive parity resets; used to stop reset chains accumulating.
    consecutive_resets: int = 0

    @property
    def has_swung(self) -> bool:
        return self.last_direction is not None


@dataclass(frozen=True, slots=True)
class TransitionScore:
    """Result of evaluating one candidate swing."""

    score: float
    is_reset: bool
    is_crossover: bool
    travel: float
    angle_delta: float
    feasible: bool
    reason: str = ""


def opposes(previous: CutDirection | None, candidate: CutDirection) -> bool:
    """Does this swing travel back against the previous one?

    The general form of the parity rule. A cut leaves the saber having moved
    along its direction; the next cut has to have a component coming back, or
    the player must sneak in an uncounted return stroke.
    """
    if previous is None:
        return True
    if previous is CutDirection.ANY or candidate is CutDirection.ANY:
        return True
    return angle_delta(previous, candidate) >= OPPOSING_SWING_ANGLE


def chase_distance(state: HandState, x: int, y: int) -> float:
    """How far the new note sits *along* the swing just made, in cells.

    Zero when the block is on the return path, where it belongs.
    """
    if not state.has_swung or state.last_direction is None:
        return 0.0
    vx, vy = DIRECTION_VECTORS[state.last_direction]
    return max(vx * (x - state.last_x) + vy * (y - state.last_y), 0.0)


def opposition_target(gap: float) -> float:
    """The turn a swing should clear, given the beats available to make it."""
    if gap >= RELAXED_OPPOSITION_GAP:
        return OPPOSING_SWING_ANGLE
    reach = max(gap, 0.0) / RELAXED_OPPOSITION_GAP
    return STRICT_OPPOSING_ANGLE - (STRICT_OPPOSING_ANGLE - OPPOSING_SWING_ANGLE) * reach


def required_parity(direction: CutDirection) -> Parity | None:
    """Which wrist state a cut needs. `None` means the cut is parity-neutral."""
    if direction in DOWNWARD_CUTS:
        return Parity.FOREHAND
    if direction in UPWARD_CUTS:
        return Parity.BACKHAND
    return None


def resulting_parity(direction: CutDirection, current: Parity) -> Parity:
    """Wrist state left behind by a swing."""
    if direction in DOWNWARD_CUTS:
        return Parity.BACKHAND
    if direction in UPWARD_CUTS:
        return Parity.FOREHAND
    if direction in HORIZONTAL_CUTS:
        # Horizontal swings act as resets: they flip the wrist either way.
        return Parity.BACKHAND if current is Parity.FOREHAND else Parity.FOREHAND
    return current


def is_crossover(hand: Hand, x: int) -> bool:
    """True when the hand reaches past the centre line into the other half."""
    return x >= 2 if hand is Hand.LEFT else x <= 1


#: What a lane actually costs each hand, 0.0 (own half) to 1.0 (full reach).
#:
#: Crossing used to be a flat boolean: the left hand in lane 2 was charged
#: exactly what it was charged in lane 3. Real maps say those are different
#: gestures. Across the reference corpus the left hand plays lane 2 about 22%
#: of the time and lane 3 about 0.7% -- one is a lean across the centre line,
#: the other drags the whole arm past the other saber. Pricing them the same
#: suppressed the common one (our lane-2 share sat at 4%) and left nothing
#: standing between the generator and the rare one.
#:
#: The lean ended up priced at nothing, which is the conclusion the reference
#: maps argue for: a hand plays the far side of the centre line about as often
#: as the near side of its own half, so it is an ordinary position rather than
#: a concession. The centre line is simply not where the cost is. The far lane
#: is, and it still carries the full charge.
#:
#: `is_crossover` stays the centre-line test regardless -- it is what Easy
#: hard-blocks on, and what the crossover statistic counts.
#: What the lean costs when nobody says otherwise. Difficulties override it --
#: see `DifficultyProfile.lean_cost` -- because the reference maps price it
#: very differently at each level: red is in lane 2 for 6% of their Hard notes
#: and 22% of Expert+.
DEFAULT_LEAN_COST = 0.0

#: Columns ordered outward from each hand's home lane.
_REACH_LANES: dict[Hand, tuple[int, ...]] = {
    Hand.LEFT: (0, 1, 2, 3),
    Hand.RIGHT: (3, 2, 1, 0),
}

#: Extra charge for the far lane, applied on top of the difficulty's crossover
#: allowance. A generous allowance should buy the lean, never the drag.
FAR_CROSS_PENALTY = 2.4

#: Room a cross-body reach needs on the way in before it is affordable at all.
#:
#: Charged flat, the far lane was simply unreachable — we placed red in the
#: rightmost column for 0.0% of notes against 3.3% of the reference maps' Hard.
#: But those maps do not use it *at speed*: across 110 visits the usual gap in
#: is a whole beat and almost none are under half of one, and every single
#: visit lasts exactly one note. It is a deliberate gesture that needs room to
#: set up and room to get out of, not a position.
FAR_CROSS_SETUP_GAP = 1.0

#: What the reach still costs once the player has that room. Not zero: it is
#: always a big movement, just an affordable one when the music leaves space.
FAR_CROSS_SETTLED_SHARE = 0.22


def far_cross_scale(gap: float) -> float:
    """How much of the cross-body charge applies, given the time available."""
    if gap <= 0.0:
        return 1.0
    room = min(gap / FAR_CROSS_SETUP_GAP, 1.0)
    return 1.0 - (1.0 - FAR_CROSS_SETTLED_SHARE) * room

#: Beats for which the other saber is still occupying the space it swung
#: through. Beyond this the hands are independent again.
HANDOFF_WINDOW = 0.75

#: Two swings closer than this in angle are travelling the same path.
SAME_PATH_ANGLE = 45.0


#: How much each hand wants each lane, 0.0-1.0. Human maps treat the *outer*
#: lane as a hand's home: across the reference corpus the left hand plays lane
#: 0 about 44% of the time, lane 1 about 33%, lane 2 about 22%. Our pattern
#: library is written in absolute columns and is centre-heavy (73% of its steps
#: sit in lanes 1 and 2), so without a counterweight the scorer parked 69% of
#: red notes in a single column. `reach_cost` says what a lane costs; this says
#: what it is worth.
#: Read off the reference maps' own distribution -- red plays lanes 0/1/2/3
#: about 44/33/22/1% of the time -- normalised against the home lane.
_LANE_AFFINITY: dict[Hand, tuple[float, ...]] = {
    Hand.LEFT: (1.00, 0.76, 0.58, 0.00),
    Hand.RIGHT: (0.00, 0.58, 0.76, 1.00),
}


def drags_across_body(hand: Hand, x: int, direction: CutDirection) -> bool:
    """Is a crossed-over hand being asked to cut back across the body?

    Once the left hand has reached into the right half its arm is extended
    across the chest. A cut with a leftward component from there drags the
    saber back through the body and through the other arm; there is nowhere
    for the elbow to go. Sweeping further outward, or straight up and down, is
    fine — the arm is already pointed that way.

    The reference maps treat this as absolute rather than merely expensive: of
    828 red notes sitting in the right half of the grid, 4 cut back across.
    Ours did it on 11% of them, and it is the single most obvious wrong-feeling
    block a player meets.
    """
    if direction is CutDirection.ANY:
        return False
    horizontal = DIRECTION_VECTORS[direction][0]
    if hand is Hand.LEFT:
        return x >= 2 and horizontal < 0.0
    return x <= 1 and horizontal > 0.0


#: Cuts that reach further from the body than the arm can comfortably go.
#:
#: Read off the reference maps rather than reasoned out in the abstract: these
#: are the combinations real mappers avoid outright, grouped into the smallest
#: set of rules that explains the avoidance. Together with `drags_across_body`
#: they rule out 4.9% of what the generator used to place and 0.21% of the
#: notes in the reference maps, so they cut the awkward reaches without cutting
#: anything a human mapper actually writes.
def awkward_reach(hand: Hand, x: int, y: int, direction: CutDirection) -> str:
    """Name the rule that makes this cut unplayable, or "" if it is fine."""
    if direction is CutDirection.ANY:
        return ""
    horizontal, vertical = DIRECTION_VECTORS[direction]

    # B — at the far crossed lane the arm is all the way over the body, so a
    #     vertical swing has to travel up or down the chest.
    far_lane = GRID_COLUMNS - 1 if hand is Hand.LEFT else 0
    if x == far_lane and horizontal == 0.0 and vertical != 0.0:
        return "B"

    # C — a top-row note sits around head height. You cut it on the upswing;
    #     cutting down through it means starting above your own head. Across
    #     the reference maps the top row is upward cuts almost exclusively:
    #     2 notes out of 11,293 cut downward there.
    if y == GRID_ROWS - 1 and vertical <= 0.0:
        return "C"

    # D — up and inward from the bottom row folds the wrist under the arm.
    inward = horizontal > 0.0 if hand is Hand.LEFT else horizontal < 0.0
    if y == 0 and vertical > 0.0 and inward:
        return "D"
    return ""


def unplayable_cut(hand: Hand, x: int, y: int, direction: CutDirection) -> str:
    """Name the rule ruling this note out, or "" if the arm can make it."""
    if drags_across_body(hand, x, direction):
        return "A"
    return awkward_reach(hand, x, y, direction)


def playable_directions(
    hand: Hand, x: int, y: int, options: Sequence[CutDirection]
) -> tuple[CutDirection, ...]:
    """The subset of `options` this hand can actually swing at (x, y)."""
    return tuple(
        direction
        for direction in options
        if not unplayable_cut(hand, x, y, direction)
    )


#: How much each column wants each row, 0.0-1.0, read off the reference maps
#: and normalised per column.
#:
#: Real maps hold a distinct shape: the two centre columns sit on the *bottom*
#: row (23% and 22% of all notes) and are all but empty in the middle (0.4% and
#: 0.2%), while the outer columns sit in the middle. That is what keeps the
#: middle of the grid clear — a block at eye level in the centre hides
#: everything approaching behind it, and in a dense passage that is most of the
#: next second of the map.
#:
#: Ours had it inverted on both axes: centre columns at mid height, outer
#: columns low, and 33% of every map sitting in the centre two-by-one against
#: their 0.66%.
#: Tuned above the reference distribution on the top row, deliberately. Real
#: maps sit lower than this; the point here is arm movement across the whole
#: song, and the top row is the one place the map can climb to without ever
#: getting in the player's way. The centre columns trade low against high
#: Not fitted for an even grid, deliberately. Fitting one is possible — the
#: table that lands every cell on a twelfth of the notes was measured — but an
#: even grid is a grid the hand can never settle in: it drops repeated cells
#: from 14% of swings to 5% and pushes long reaches from 34% to 44%, which is
#: the map that plays as arms spread from start to finish. The reference maps
#: are *less* evenly spread than that fit, and far more comfortable.
#:
#: The centre columns want all three rows about equally. Their middle row is
#: the one the sightline rules gate on crowding — and it turns out to be the
#: *rest* position of the whole grid, the place a hand goes when the music is
#: not asking for anything. Suppressing it forced every note out to an extreme,
#: low or high, inner or outer, and the map came out with the player's arms
#: spread the entire song. Crowding is what makes a centre block a problem, not
#: the block.
_HEIGHT_AFFINITY: dict[int, tuple[float, ...]] = {
    # column: (low, mid, high)
    0: (0.42, 1.00, 0.44),
    1: (1.00, 0.02, 0.14),
    2: (1.00, 0.01, 0.22),
    3: (0.40, 1.00, 0.36),
}


def height_affinity(x: int, y: int) -> float:
    """How natural this row is for this column, 0.0-1.0."""
    column = _HEIGHT_AFFINITY[max(0, min(x, GRID_COLUMNS - 1))]
    return column[max(0, min(y, len(column) - 1))]


def blocks_the_sightline(x: int, y: int) -> bool:
    """Is this the centre of the grid at eye level?

    The two centre columns in the middle row. Notes here sit directly between
    the player and everything behind them.
    """
    return x in (1, 2) and y == 1


def reach_cost(hand: Hand, x: int, lean_cost: float = DEFAULT_LEAN_COST) -> float:
    """How far past its own half this hand is being sent, 0.0-1.0.

    Home and the hand's own inner lane are free; the far lane always costs
    everything. Only the lean in between is a matter of taste, and that is what
    a difficulty gets to set.
    """
    lanes = _REACH_LANES[hand]
    offset = lanes.index(max(0, min(x, len(lanes) - 1)))
    return (0.0, 0.0, lean_cost, 1.0)[offset]


def lane_affinity(hand: Hand, x: int) -> float:
    """How natural this column is for this hand, 0.0-1.0."""
    lanes = _LANE_AFFINITY[hand]
    return lanes[max(0, min(x, len(lanes) - 1))]


def handoff_cost(
    other: HandState, beat: float, x: int, direction: CutDirection
) -> float:
    """What this swing costs because of where the *other* saber just went.

    Everything else in this module reasons about one hand at a time, which is
    why "red cuts right-to-left in the far right lane, then blue cuts
    right-to-left in the far right lane" passed every check: each hand's own
    parity is fine. The problem is between them -- the second saber is asked
    to travel through the space the first is still leaving, in the same
    direction, so the arms chase each other instead of trading off.

    Simultaneous notes are exempt: two hands landing on the same beat is a
    chord, and human maps are full of them. It is the short *lag* that hurts.
    """
    if not other.has_swung:
        return 0.0
    gap = beat - other.last_beat
    if gap <= 0.0 or gap > HANDOFF_WINDOW:
        return 0.0
    # The sooner the second swing follows, the less the first has cleared out.
    urgency = 1.0 - gap / HANDOFF_WINDOW

    cost = 0.0
    if x == other.last_x:
        cost += 0.45 * urgency
        if angle_delta(other.last_direction, direction) < SAME_PATH_ANGLE:
            cost += 1.30 * urgency

    # Arms crossed over each other: this hand is reaching past where the other
    # one is sitting, so the sabers end up on the wrong sides of the body.
    inverted = x > other.last_x if other.hand is Hand.RIGHT else x < other.last_x
    if inverted:
        cost += 0.55 * urgency
    return cost


def angle_delta(previous: CutDirection | None, candidate: CutDirection) -> float:
    """Smallest absolute change in swing angle, in degrees."""
    if previous is None:
        return 0.0
    if previous is CutDirection.ANY or candidate is CutDirection.ANY:
        return 0.0
    delta = abs(DIRECTION_ANGLES[candidate] - DIRECTION_ANGLES[previous]) % 360.0
    return min(delta, 360.0 - delta)


def preferred_directions(state: HandState) -> tuple[CutDirection, ...]:
    """Cut directions that keep this hand in natural alternation, best first."""
    if state.parity is Parity.FOREHAND:
        return (
            CutDirection.DOWN,
            CutDirection.DOWN_RIGHT,
            CutDirection.DOWN_LEFT,
            CutDirection.RIGHT,
            CutDirection.LEFT,
        )
    return (
        CutDirection.UP,
        CutDirection.UP_LEFT,
        CutDirection.UP_RIGHT,
        CutDirection.LEFT,
        CutDirection.RIGHT,
    )


class ParityEngine:
    """Scores and applies swings while tracking both hands."""

    def __init__(
        self,
        *,
        reset_min_gap_beats: float,
        movement_scale: float,
        crossover_probability: float,
        lean_cost: float = DEFAULT_LEAN_COST,
    ) -> None:
        self.reset_min_gap_beats = reset_min_gap_beats
        self.lean_cost = lean_cost
        self.movement_scale = max(movement_scale, 0.2)
        self.crossover_allowance = crossover_probability
        self._states: dict[Hand, HandState] = {
            Hand.LEFT: HandState(Hand.LEFT, last_x=1),
            Hand.RIGHT: HandState(Hand.RIGHT, last_x=2),
        }

    # -- state ------------------------------------------------------------

    def state(self, hand: Hand) -> HandState:
        return self._states[hand]

    def snapshot(self) -> dict[Hand, HandState]:
        return dict(self._states)

    def restore(self, snapshot: dict[Hand, HandState]) -> None:
        self._states = dict(snapshot)

    def reset(self) -> None:
        self._states = {
            Hand.LEFT: HandState(Hand.LEFT, last_x=1),
            Hand.RIGHT: HandState(Hand.RIGHT, last_x=2),
        }

    # -- scoring ----------------------------------------------------------

    def score_transition(
        self,
        state: HandState,
        beat: float,
        x: int,
        y: int,
        direction: CutDirection,
    ) -> TransitionScore:
        """Rate one candidate swing for one hand. Higher is better."""
        gap = beat - state.last_beat
        crossover = is_crossover(state.hand, x)

        REASONS = {
            "A": "crossed hand cutting back across the body",
            "B": "vertical swing at the far crossed lane",
            "C": "downward cut on a top-row note",
            "D": "scooping up and inward from the bottom row",
        }
        rule = unplayable_cut(state.hand, x, y, direction)
        if rule:
            return TransitionScore(
                score=-12.0,
                is_reset=False,
                is_crossover=crossover,
                travel=0.0,
                angle_delta=0.0,
                feasible=False,
                reason=REASONS[rule],
            )

        if not state.has_swung:
            return TransitionScore(
                score=1.0 - 0.8 * reach_cost(state.hand, x, self.lean_cost),
                is_reset=False,
                is_crossover=crossover,
                travel=0.0,
                angle_delta=0.0,
                feasible=True,
            )

        if gap < ABSOLUTE_MIN_SAME_HAND_GAP:
            return TransitionScore(
                score=-10.0,
                is_reset=False,
                is_crossover=crossover,
                travel=0.0,
                angle_delta=0.0,
                feasible=False,
                reason="same hand notes too close together",
            )

        needed = required_parity(direction)
        # Two independent ways to owe the player a return stroke: the wrist is
        # in the wrong orientation, or the saber is being asked to travel the
        # same way twice. Horizontals only ever trip the second one.
        parity_violation = needed is not None and needed is not state.parity
        not_opposing = not opposes(state.last_direction, direction)
        is_reset = parity_violation or not_opposing

        travel = ((x - state.last_x) ** 2 + (y - state.last_y) ** 2) ** 0.5
        delta = angle_delta(state.last_direction, direction)

        score = 1.0

        # --- parity ------------------------------------------------------
        if is_reset:
            # An identical swing is the worst kind of reset: a wrist-orientation
            # reset can be half-absorbed by rolling the hand, but repeating the
            # same direction needs the full return stroke. Hold it to the whole
            # window the profile promises rather than a fraction of it.
            identical = direction is state.last_direction
            floor = self.reset_min_gap_beats * (1.0 if identical else 0.55)
            if gap < floor:
                return TransitionScore(
                    score=-8.0,
                    is_reset=True,
                    is_crossover=crossover,
                    travel=travel,
                    angle_delta=delta,
                    feasible=False,
                    reason="parity reset with no recovery time",
                )
            # A reset costs less the more time the player has to re-cock.
            slack = min(gap / max(self.reset_min_gap_beats, 0.1), 2.0)
            score -= 2.6 / slack
            score -= 0.8 * state.consecutive_resets
        else:
            score += 0.85 if needed is not None else 0.35

        # --- repeated identical swing ------------------------------------
        if direction is state.last_direction:
            # Never free, even with a full beat of space: an identical swing
            # always means an uncounted return stroke somewhere.
            score -= 1.8 * max(0.45, 1.0 - gap)

        # --- swing opposition ---------------------------------------------
        # Reward swings that come back against the last one. This used to be
        # inverted — it penalised *large* angle changes, which is to say it
        # rewarded continuing in the same direction, and that is precisely the
        # motion an arm cannot make.
        #
        # The requirement is a function of the gap: with a beat to spare a
        # right-angle turn is fine, at speed only a real reversal is. A flat
        # threshold left 13% of fast transitions turning by exactly 90 deg
        # against 0.5% in the reference maps.
        if state.last_direction is not None and delta > 0.0:
            target = opposition_target(gap)
            if delta < target:
                score -= SHALLOW_TURN_PENALTY * (target - delta) / 90.0
            else:
                headroom = max(IDEAL_OPPOSING_ANGLE - target, 1.0)
                score += 0.7 * min((delta - target) / headroom, 1.0)

        # --- momentum -----------------------------------------------------
        # Where the block sits relative to the swing that just happened.
        chase = chase_distance(state, x, y)
        if chase > 0.0:
            score -= CHASE_PENALTY * min(chase / 2.0, 1.0)

        # --- travel -------------------------------------------------------
        # Distance is only a problem relative to the time available for it.
        allowed = self.movement_scale * (0.9 + 2.4 * min(gap, 2.0))
        if travel > allowed:
            score -= 1.1 * (travel - allowed) / max(allowed, 0.5)
        else:
            # Reward using the space, but not at the cost of everything else.
            score += 0.18 * min(travel / max(allowed, 0.5), 1.0)

        # --- crossovers ---------------------------------------------------
        if crossover:
            if self.crossover_allowance <= 0.001:
                return TransitionScore(
                    score=-6.0,
                    is_reset=is_reset,
                    is_crossover=True,
                    travel=travel,
                    angle_delta=delta,
                    feasible=False,
                    reason="crossovers not permitted at this difficulty",
                )
            cost = reach_cost(state.hand, x, self.lean_cost)
            score -= cost * (1.0 - min(self.crossover_allowance * 4.0, 1.0)) * 1.6
            if gap < 0.5:
                score -= 1.2 * cost
            # The far lane is charged beyond whatever the allowance forgives:
            # a difficulty that welcomes crossing is welcoming the lean, not
            # the arm dragged past the other saber. Scaled by the room the
            # player has, so the reach stays a gesture the music can afford
            # rather than something that never happens at all.
            score -= (
                FAR_CROSS_PENALTY
                * max(cost - 0.5, 0.0)
                * 2.0
                * far_cross_scale(gap)
            )

        return TransitionScore(
            score=score,
            is_reset=is_reset,
            is_crossover=crossover,
            travel=travel,
            angle_delta=delta,
            feasible=True,
        )

    # -- mutation ---------------------------------------------------------

    def apply(
        self,
        hand: Hand,
        beat: float,
        x: int,
        y: int,
        direction: CutDirection,
    ) -> Parity:
        """Commit a swing and return the hand's new parity."""
        state = self._states[hand]
        needed = required_parity(direction)
        was_reset = needed is not None and needed is not state.parity
        new_parity = resulting_parity(direction, state.parity)
        self._states[hand] = replace(
            state,
            parity=new_parity,
            last_beat=beat,
            last_x=x,
            last_y=y,
            last_direction=direction,
            consecutive_resets=state.consecutive_resets + 1 if was_reset else 0,
        )
        return new_parity


def validate_sequence(
    notes: list[tuple[float, Hand, int, int, CutDirection]],
    *,
    reset_min_gap_beats: float,
) -> list[str]:
    """Re-check a finished note list for parity problems.

    Used by the validator as an independent second opinion on the generator.
    """
    problems: list[str] = []
    states = {
        Hand.LEFT: HandState(Hand.LEFT, last_x=1),
        Hand.RIGHT: HandState(Hand.RIGHT, last_x=2),
    }
    for beat, hand, x, y, direction in sorted(notes, key=lambda item: item[0]):
        state = states[hand]
        if state.has_swung:
            gap = beat - state.last_beat
            needed = required_parity(direction)
            unopposed = not opposes(state.last_direction, direction)
            wrong_wrist = needed is not None and needed is not state.parity
            if (wrong_wrist or unopposed) and gap < reset_min_gap_beats * 0.55:
                problems.append(
                    f"parity reset at beat {beat:.3f} for the "
                    f"{hand.name.lower()} hand with only {gap:.2f} beats of recovery"
                )
            if (
                direction is state.last_direction
                and direction in DOWNWARD_CUTS | UPWARD_CUTS
                and gap < 0.3
            ):
                problems.append(
                    f"repeated {direction.name} cut at beat {beat:.3f} "
                    f"for the {hand.name.lower()} hand"
                )
        states[hand] = replace(
            state,
            parity=resulting_parity(direction, state.parity),
            last_beat=beat,
            last_x=x,
            last_y=y,
            last_direction=direction,
        )
    return problems
