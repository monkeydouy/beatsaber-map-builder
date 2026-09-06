"""Turn rhythm slots into notes by instantiating and scoring patterns.

The generator works on *runs* — groups of slots close enough together to read
as one musical gesture. For each run it builds many candidate realisations
(different patterns, mirrored or not, starting with either hand, shifted
sideways), scores each one against the parity engine and the scoring weights,
and commits the best. Scoring the whole run rather than each note in isolation
is what produces flow: a locally attractive note that strands the next three is
correctly rejected.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, replace
from random import Random

from app.models.beatmap import GRID_COLUMNS, GRID_ROWS, BeatNote
from app.models.enums import (
    DIRECTION_VECTORS,
    HORIZONTAL_CUTS,
    MIRRORED_DIRECTION,
    CutDirection,
    Difficulty,
    Hand,
    MappingStyle,
    MusicalEventType,
)
from app.models.musical import BeatGrid, Section
from app.services.mapping.config import MappingConfig, ScoringWeights, tag_bias
from app.services.mapping.difficulty_profiles import DifficultyProfile
from app.services.mapping.obstacle_generator import WallPlan
from app.services.mapping.parity_engine import (
    RELAXED_OPPOSITION_GAP,
    HandState,
    ParityEngine,
    angle_delta,
    blocks_the_sightline,
    chase_distance,
    handoff_cost,
    height_affinity,
    lane_affinity,
    opposition_target,
    playable_directions,
    preferred_directions,
    reach_cost,
    required_parity,
    resulting_parity,
    unplayable_cut,
)
from app.services.mapping.pattern_library import (
    BOTH,
    OFF,
    PatternDefinition,
    PatternStep,
    eligible_patterns,
)
from app.services.mapping.rhythm_selector import RhythmSlot

logger = logging.getLogger(__name__)

#: How often a block sitting on the floor of an *outer* column is lifted to
#: mid height instead.
#:
#: The pattern library counts rows absolutely and leans low — 22 of its
#: outer-column steps sit on the bottom row — so the map came out crouched,
#: with four notes in five on the floor. This lifts some of them without
#: touching the centre columns, which have to stay low or they block the view.
#: A share rather than a rule, so the map keeps some floor notes to fall back
#: to; lifting all of them put 95% of the outer columns at one height.
OUTER_FLOOR_LIFT = 0.75

#: Beats within which the *same* centre cell must not be used twice.
#:
#: What hides a note is another note in front of it, on the same spot. One
#: block at eye level in the middle of the grid hides nothing; a run of them
#: arriving on the same cell hides everything behind the first. So this is a
#: rule about a cell repeating, not about how busy the song is — an earlier
#: version gated the centre on overall note density, a far broader net that
#: emptied the middle of the grid to keep a much narrower problem away.
SIGHTLINE_REPEAT_GAP = 2.0

#: How readily a block moves off the row its pattern asked for, toward the row
#: its column actually sits at. 0.0 keeps every pattern's vertical shape and
#: leaves the map crouched; 1.0 reproduces the reference distribution exactly
#: and discards the shape. See `PatternGenerator._row_at_the_column_s_height`.
ROW_REDISTRIBUTION = 0.60


@dataclass(slots=True)
class Run:
    """A group of rhythm slots mapped as one gesture."""

    slots: list[RhythmSlot]

    @property
    def start_beat(self) -> float:
        return self.slots[0].beat

    @property
    def end_beat(self) -> float:
        return self.slots[-1].beat

    @property
    def length(self) -> int:
        return len(self.slots)

    @property
    def mean_intensity(self) -> float:
        return sum(slot.event.strength for slot in self.slots) / max(self.length, 1)


@dataclass(slots=True)
class Candidate:
    """One fully realised interpretation of a run."""

    notes: list[BeatNote]
    score: float
    pattern: PatternDefinition
    resets: int
    crossovers: int


class PatternGenerator:
    """Builds the note list for one difficulty."""

    def __init__(
        self,
        *,
        profile: DifficultyProfile,
        config: MappingConfig,
        weights: ScoringWeights,
        style: MappingStyle,
        intensity: float,
        grid: BeatGrid,
        sections: list[Section],
        rng: Random,
        walls: WallPlan | None = None,
    ) -> None:
        self.profile = profile
        # Walls are planned before notes, so their reserved cells are a hard
        # constraint here rather than something to be reconciled afterwards.
        self.walls = walls or WallPlan()
        self.config = config
        self.weights = weights
        self.style = style
        self.intensity = min(max(intensity, 0.0), 1.0)
        self.grid = grid
        self.sections = {section.index: section for section in sections}
        self.rng = rng
        self.parity = ParityEngine(
            reset_min_gap_beats=profile.reset_min_gap_beats,
            movement_scale=profile.movement_scale,
            crossover_probability=profile.crossover_probability,
            lean_cost=profile.lean_cost,
        )
        self._recent_patterns: deque[str] = deque(maxlen=config.repetition_memory)
        self._recent_positions: deque[tuple[int, int]] = deque(maxlen=8)
        self._lead: Hand = Hand.RIGHT
        #: Clear air around each slot, filled in by `generate`.
        self._room: dict[float, float] = {}
        #: When each centre cell was last used, so runs of them can be charged.
        self._centre_last_used: dict[tuple[int, int], float] = {}
        self.total_resets = 0
        self.total_crossovers = 0

    # -- entry point -------------------------------------------------------

    def generate(self, slots: list[RhythmSlot]) -> list[BeatNote]:
        """Produce the full note list for the given rhythm."""
        if not slots:
            return []

        # Crowding is a property of the song, not of the run the slot lands in.
        self._room = {}
        for index, slot in enumerate(slots):
            before = slot.beat - slots[index - 1].beat if index > 0 else 99.0
            after = slots[index + 1].beat - slot.beat if index + 1 < len(slots) else 99.0
            self._room[round(slot.beat, 4)] = min(before, after)

        notes: list[BeatNote] = []
        for run in self._build_runs(slots):
            candidates = self._build_candidates(run)
            if not candidates:
                continue
            best = max(candidates, key=lambda candidate: candidate.score)
            self._commit(best)
            notes.extend(best.notes)

        notes.sort(key=lambda note: (note.beat, note.hand.value))
        logger.info(
            "patterns_generated",
            extra={
                "difficulty": self.profile.difficulty.value,
                "notes": len(notes),
                "resets": self.total_resets,
                "crossovers": self.total_crossovers,
            },
        )
        return notes

    # -- runs --------------------------------------------------------------

    def _build_runs(self, slots: list[RhythmSlot]) -> list[Run]:
        """Split the rhythm into gesture-sized groups."""
        runs: list[Run] = []
        current: list[RhythmSlot] = [slots[0]]
        # Long runs get hard-split so one pattern does not dominate a section.
        max_run = 4 if self.profile.difficulty.rank <= Difficulty.NORMAL.rank else 8

        for slot in slots[1:]:
            gap = slot.beat - current[-1].beat
            same_section = slot.section_index == current[-1].section_index
            if gap > self.config.run_gap_beats or not same_section or len(current) >= max_run:
                runs.append(Run(current))
                current = [slot]
            else:
                current.append(slot)
        runs.append(Run(current))
        return runs

    # -- candidates --------------------------------------------------------

    def _candidate_patterns(self, run: Run) -> list[PatternDefinition]:
        """Pick the pattern shapes worth trying for this run."""
        profile = self.profile
        exclude: list[str] = []
        if run.length == 1:
            # Single hits either accent with a double or take a simple swing.
            require_double = run.slots[0].wants_double
            pool = eligible_patterns(
                profile.difficulty,
                profile.pattern_complexity,
                require_tags=("double",) if require_double else (),
                exclude_tags=() if require_double else ("double", "stream", "burst"),
            )
            return pool or eligible_patterns(profile.difficulty, profile.pattern_complexity)

        if not any(slot.wants_double for slot in run.slots):
            exclude.append("double")
        if run.length < 4:
            exclude.append("stream")
        if any(slot.in_recovery for slot in run.slots):
            exclude.extend(("technical", "crossover", "stream"))

        pool = eligible_patterns(
            profile.difficulty, profile.pattern_complexity, exclude_tags=exclude
        )
        if not pool:
            pool = eligible_patterns(profile.difficulty, profile.pattern_complexity)

        # Bias the pool by style affinity, section intensity and the technical
        # allowance, then sample so different runs pick different shapes.
        section = self.sections.get(run.slots[0].section_index)
        section_intensity = section.intensity if section else 0.5
        weighted: list[tuple[PatternDefinition, float]] = []
        for pattern in pool:
            weight = pattern.affinity(self.style) * tag_bias(self.style, pattern.tags)
            if "technical" in pattern.tags:
                weight *= profile.technical_pattern_probability * 2.5 + 0.15
            if "crossover" in pattern.tags:
                weight *= profile.crossover_probability * 3.0 + 0.05
            if "recovery" in pattern.tags:
                weight *= 1.6 if any(slot.in_recovery for slot in run.slots) else 0.35
            # Busy sections want movement; quiet ones want calm.
            weight *= 1.0 + (section_intensity - 0.5) * (pattern.movement_score - 0.4)
            if pattern.name in self._recent_patterns:
                weight *= 0.35
            weighted.append((pattern, max(weight, 0.01)))

        wanted = min(self.config.max_candidates // 2, len(weighted))
        wanted = max(wanted, min(self.config.min_candidates // 2, len(weighted)))
        return self._weighted_sample(weighted, wanted)

    def _weighted_sample(
        self, weighted: list[tuple[PatternDefinition, float]], count: int
    ) -> list[PatternDefinition]:
        pool = list(weighted)
        chosen: list[PatternDefinition] = []
        for _ in range(min(count, len(pool))):
            total = sum(weight for _pattern, weight in pool)
            if total <= 0:
                break
            target = self.rng.random() * total
            running = 0.0
            for index, (pattern, weight) in enumerate(pool):
                running += weight
                if running >= target:
                    chosen.append(pattern)
                    pool.pop(index)
                    break
        return chosen or [pattern for pattern, _weight in weighted[:1]]

    def _build_candidates(self, run: Run) -> list[Candidate]:
        """Realise and score every variation worth considering."""
        candidates: list[Candidate] = []
        patterns = self._candidate_patterns(run)
        budget = self.config.max_candidates

        wants_double = any(slot.wants_double for slot in run.slots)
        double_modes = (True, False) if wants_double else (False,)

        for pattern in patterns:
            mirrors = (False, True) if pattern.allow_mirror else (False,)
            for mirror in mirrors:
                for lead in (self._lead, self._lead.other):
                    for honour_doubles in double_modes:
                        if len(candidates) >= budget:
                            break
                        notes = self._instantiate(
                            run,
                            pattern,
                            mirror=mirror,
                            lead=lead,
                            honour_doubles=honour_doubles,
                        )
                        if notes is None:
                            continue
                        score, resets, crossovers = self._score(run, pattern, notes)
                        if honour_doubles:
                            # Reward realising the accent the music asked for,
                            # but never at the cost of a playable transition.
                            score += self.weights.musical_match * 0.35
                        candidates.append(
                            Candidate(
                                notes=notes,
                                score=score,
                                pattern=pattern,
                                resets=resets,
                                crossovers=crossovers,
                            )
                        )
        return candidates

    # -- instantiation -----------------------------------------------------

    def _instantiate(
        self,
        run: Run,
        pattern: PatternDefinition,
        *,
        mirror: bool,
        lead: Hand,
        honour_doubles: bool = True,
    ) -> list[BeatNote] | None:
        """Lay a pattern over a run's slots, resolving hands and directions."""
        notes: list[BeatNote] = []
        snapshot = self.parity.snapshot()
        states = dict(snapshot)
        current_lead = lead
        allowed_rows = self.profile.allowed_rows

        try:
            for index, slot in enumerate(run.slots):
                step = pattern.steps[index % pattern.length]
                wants_double = honour_doubles and slot.wants_double

                if step.role == BOTH or wants_double:
                    # A slot the rhythm selector marked as an accent gets both
                    # hands whatever pattern is being laid over it.
                    double_step = step if step.role == BOTH else self._double_step(step)
                    placed = self._place_double(slot, double_step, states, mirror)
                    if placed is None:
                        return None
                    notes.extend(placed)
                    for note in placed:
                        states[note.hand] = self._advance(states[note.hand], note)
                else:
                    hand = current_lead if step.role != OFF else current_lead.other
                    note = self._place_single(
                        slot,
                        step,
                        hand,
                        states[hand],
                        mirror,
                        allowed_rows,
                        pattern.name,
                        self._elbow_room(slot.beat),
                    )
                    if note is None:
                        return None
                    notes.append(note)
                    states[hand] = self._advance(states[hand], note)
                    if step.swap_lead:
                        current_lead = hand.other
        finally:
            self.parity.restore(snapshot)

        return notes

    def _place_single(
        self,
        slot: RhythmSlot,
        step: PatternStep,
        hand: Hand,
        state: HandState,
        mirror: bool,
        allowed_rows: tuple[int, ...],
        pattern_name: str,
        elbow_room: float = 99.0,
    ) -> BeatNote | None:
        x = self._resolve_lane(step.offset, hand, state)
        y = self._resolve_row(step.row, allowed_rows, state)
        x, y = self._cell_off_the_swing_path(x, y, hand, state, slot, allowed_rows)
        y = self._row_clear_of_the_sightline(x, y, allowed_rows)
        y = self._row_lifted_off_the_floor(x, y, allowed_rows)
        y = self._row_the_hand_can_cut(x, y, hand, state, allowed_rows)
        placed = self._clear_cell(slot.beat, x, y, hand, allowed_rows)
        if placed is None:
            # Every cell this hand could reach is walled at this instant.
            return None
        x, y = placed
        direction = self._resolve_direction(step.direction, mirror, state, slot, x, y)
        if direction is None:
            return None
        return BeatNote(
            beat=slot.beat,
            hand=hand,
            x=x,
            y=y,
            direction=direction,
            source_event_index=-1,
            confidence=slot.event.confidence,
            pattern=pattern_name,
        )

    def _double_step(self, step: PatternStep) -> PatternStep:
        """Build a two-hand step from an ordinary one.

        Wide doubles read as bigger accents but demand more reach, so they are
        gated on the profile's movement allowance.
        """
        # Row is left unset so each hand can take the one that puts the block
        # on its own return path. Pinning both hands to a single row made a
        # third of accents unplaceable once momentum was scored.
        row = None
        wide = self.profile.movement_scale >= 0.85 and self.rng.random() < 0.35
        # Home lanes for a wide accent, inner lanes for a tight one.
        reach = 0 if wide else 1
        # Directions are left unset so each hand swings from its own parity.
        # Forcing both hands DOWN, as a fixed template would, guarantees a
        # reset for whichever hand happens to be backhand at that moment.
        return PatternStep(
            role=BOTH,
            offset=reach,
            row=row,
            direction=None,
            partner_offset=reach,
            partner_row=row,
            partner_direction=None,
            swap_lead=False,
        )

    def _place_double(
        self,
        slot: RhythmSlot,
        step: PatternStep,
        states: dict[Hand, HandState],
        mirror: bool,
    ) -> list[BeatNote] | None:
        """Place a simultaneous pair, keeping the hands on their own sides."""
        allowed_rows = self.profile.allowed_rows
        # Both halves are offsets from their own hand's home lane, so each side
        # resolves independently and the pair cannot come out crossed.
        left_lane = self._resolve_lane(
            step.offset if step.offset is not None else 1, Hand.LEFT, states[Hand.LEFT]
        )
        right_lane = self._resolve_lane(
            step.partner_offset if step.partner_offset is not None else 1,
            Hand.RIGHT,
            states[Hand.RIGHT],
        )
        if left_lane > right_lane:
            left_lane, right_lane = right_lane, left_lane

        left_state, right_state = states[Hand.LEFT], states[Hand.RIGHT]

        # Rows first: which cuts an arm can make depends on how high the block
        # is, so the row has to be settled before a direction can be checked.
        left_row = self._row_clear_of_the_sightline(
            left_lane,
            self._double_row(step.row, left_state, left_lane, allowed_rows),
            allowed_rows,
        )
        right_row = self._row_clear_of_the_sightline(
            right_lane,
            self._double_row(
                step.partner_row if step.partner_row is not None else step.row,
                right_state,
                right_lane,
                allowed_rows,
            ),
            allowed_rows,
        )

        if step.direction is None and step.partner_direction is None:
            # Let parity choose. Matching directions read better, so when both
            # hands are in the same state they swing together; when they differ
            # each hand takes the swing that keeps it in alternation.
            left_dir = self._first_usable(left_state, left_lane, left_row)
            right_dir = (
                left_dir
                if left_state.parity is right_state.parity
                and not unplayable_cut(Hand.RIGHT, right_lane, right_row, left_dir)
                else self._first_usable(right_state, right_lane, right_row)
            )
        else:
            left_dir = step.direction or CutDirection.DOWN
            right_dir = step.partner_direction or left_dir
            if mirror:
                left_dir, right_dir = MIRRORED_DIRECTION[right_dir], MIRRORED_DIRECTION[left_dir]
            if unplayable_cut(Hand.LEFT, left_lane, left_row, left_dir):
                left_dir = self._first_usable(left_state, left_lane, left_row)
            if unplayable_cut(Hand.RIGHT, right_lane, right_row, right_dir):
                right_dir = self._first_usable(right_state, right_lane, right_row)

        # A double whose hands have nowhere legal to swing is not a double.
        if unplayable_cut(Hand.LEFT, left_lane, left_row, left_dir) or unplayable_cut(
            Hand.RIGHT, right_lane, right_row, right_dir
        ):
            return None

        # Rule 9: never ask the hands to swing into each other. Two inward
        # cuts on the same row at neighbouring lanes is the pattern that reads
        # as being told to clap.
        left_vector = DIRECTION_VECTORS[left_dir]
        right_vector = DIRECTION_VECTORS[right_dir]
        converging = left_vector[0] > 0.0 and right_vector[0] < 0.0
        if converging and left_row == right_row and right_lane - left_lane <= 2:
            return None

        left_placed = self._clear_cell(
            slot.beat,
            max(0, min(left_lane, GRID_COLUMNS - 1)),
            left_row,
            Hand.LEFT,
            allowed_rows,
        )
        right_placed = self._clear_cell(
            slot.beat,
            max(0, min(right_lane, GRID_COLUMNS - 1)),
            right_row,
            Hand.RIGHT,
            allowed_rows,
        )
        if left_placed is None or right_placed is None:
            return None

        notes = [
            BeatNote(
                beat=slot.beat,
                hand=Hand.LEFT,
                x=left_placed[0],
                y=left_placed[1],
                direction=left_dir,
                confidence=slot.event.confidence,
                pattern="Double",
            ),
            BeatNote(
                beat=slot.beat,
                hand=Hand.RIGHT,
                x=right_placed[0],
                y=right_placed[1],
                direction=right_dir,
                confidence=slot.event.confidence,
                pattern="Double",
            ),
        ]
        if notes[0].x == notes[1].x and notes[0].y == notes[1].y:
            return None
        # Relocation must not leave the hands crossed over each other.
        if notes[0].x > notes[1].x:
            return None
        return notes

    def _cell_off_the_swing_path(
        self,
        x: int,
        y: int,
        hand: Hand,
        state: HandState,
        slot: RhythmSlot,
        allowed_rows: tuple[int, ...],
    ) -> tuple[int, int]:
        """Keep the pattern's cell unless it sits along the swing just made.

        The companion to overruling a pattern's cut direction, and the half
        that direction alone cannot fix: a block is reached by where it is as
        much as by which way it is cut. Patterns name their columns and rows in
        advance, so a phrase could still drop a block exactly where the arm had
        just travelled away from — the player stops, comes back, and hits it
        with no swing behind the cut.

        Moved as little as possible. The lane distribution and the wall
        choreography are both carried by these positions, so this stays a nudge
        off the swing path rather than a free hand to reposition.

        Bounded by time, like every other rule here: given a beat to get there
        the player can simply reposition, and the pattern's own shape is worth
        more than the correction. Vetoing regardless doubled the number of
        crossovers entered without any setup, because relocating notes the arm
        had time to reach anyway kept flinging hands across the centre line.
        """
        if not state.has_swung or chase_distance(state, x, y) <= 0.0:
            return x, y
        if slot.beat - state.last_beat > RELAXED_OPPOSITION_GAP:
            return x, y

        best: tuple[tuple[int, float, int], tuple[int, int]] | None = None
        for candidate_y in allowed_rows:
            for candidate_x in range(GRID_COLUMNS):
                if chase_distance(state, candidate_x, candidate_y) > 0.0:
                    continue
                if self.walls.blocks(slot.beat, candidate_x, candidate_y):
                    continue
                drift = abs(candidate_x - x) + abs(candidate_y - y)
                key = (
                    drift,
                    reach_cost(hand, candidate_x, self.profile.lean_cost),
                    candidate_x,
                )
                if best is None or key < best[0]:
                    best = (key, (candidate_x, candidate_y))
        return best[1] if best is not None else (x, y)

    def _first_usable(self, state: HandState, x: int, y: int) -> CutDirection:
        """This hand's best parity-appropriate cut that it can reach from `x`.

        A double picks its directions outside the usual path, so it needs the
        same guard: a hand placed across the body must not be handed a cut that
        drags it back.
        """
        playable = playable_directions(state.hand, x, y, preferred_directions(state))
        return playable[0] if playable else CutDirection.ANY

    def _elbow_room(self, beat: float) -> float:
        """Beats of clear air around the slot at `beat`.

        Measured across the whole song, not within the run. Runs hold at most
        eight slots, so measuring inside one reported the first and last of
        every run as wide open — a quarter of all slots, in the middle of the
        densest passages, exempted from the crowding rule that exists for
        exactly those passages.
        """
        return self._room.get(round(beat, 4), 99.0)

    def _row_clear_of_the_sightline(
        self,
        x: int,
        y: int,
        allowed_rows: tuple[int, ...],
    ) -> int:
        """Drop a centre-column block off eye level.

        The pattern library counts rows absolutely, and its convention is the
        inverse of what real maps do: it puts the centre columns at mid height
        and the outer columns low, where the reference maps put the centre
        columns on the floor (23% and 22% of all their notes) and keep the
        middle of the grid all but empty (0.4% and 0.2%).

        A block at eye level in the centre hides everything approaching behind
        it, which in a dense passage is most of the next second of the map.
        Rather than rewrite every pattern's vertical shape, move the offending
        block to the row its own column wants.
        """
        if not blocks_the_sightline(x, y):
            return y
        candidates = [row for row in allowed_rows if not blocks_the_sightline(x, row)]
        if not candidates:
            return y
        return max(candidates, key=lambda row: (height_affinity(x, row), -abs(row - y)))

    def _row_at_the_column_s_height(
        self,
        x: int,
        y: int,
        allowed_rows: tuple[int, ...],
        elbow_room: float = 0.0,
    ) -> int:
        """Settle a block at the height its column actually sits at.

        The other half of the shape real maps hold: the centre columns sit low,
        the outer ones sit at mid height with real use of the top row. The
        pattern library writes rows absolutely and leans low — 22 of its
        outer-column steps sit on the floor — so left alone the whole map came
        out crouched, 79% of notes on the bottom row against 56% in the
        reference maps.

        The column's affinities *are* the reference distribution, normalised,
        so sampling them reproduces it by construction. The pattern's own row
        keeps a standing bias, which is what stops this flattening a shape the
        pattern meant — a deliberate reach for the top row usually survives.
        """
        # Eye level in the centre is on the table only where the map has left
        # air around this note; in a packed run it stays off.
        candidates = list(allowed_rows)
        if not candidates:
            return y
        weights = [
            height_affinity(x, row) * (1.0 if row == y else ROW_REDISTRIBUTION)
            for row in candidates
        ]
        if sum(weights) <= 0.0:
            return y
        target = self.rng.random() * sum(weights)
        running = 0.0
        for row, weight in zip(candidates, weights):
            running += weight
            if running >= target:
                return row
        return candidates[-1]

    def _row_lifted_off_the_floor(
        self, x: int, y: int, allowed_rows: tuple[int, ...]
    ) -> int:
        """Raise an outer-column block from the floor to mid height.

        Only the outer columns: the centre two have to stay off eye level or
        they hide what is behind them, so their floor notes stay put.
        """
        if x in (1, 2) or y != 0 or 1 not in allowed_rows:
            return y
        return 1 if self.rng.random() < OUTER_FLOOR_LIFT else y

    def _row_the_hand_can_cut(
        self,
        x: int,
        y: int,
        hand: Hand,
        state: HandState,
        allowed_rows: tuple[int, ...],
    ) -> int:
        """Move a block off a row this hand has no legal swing for.

        Which cuts an arm can make depends on the height of the block, so a
        row and a hand's wrist state together can leave nothing to swing: a
        forehand hand owes a downward cut, and the top row takes upward cuts
        only. That is a real constraint, not a scoring preference — the answer
        is to put the block where the hand can reach it, keeping the row the
        pattern asked for whenever it works.
        """
        if playable_directions(hand, x, y, preferred_directions(state)):
            return y
        # Nearest legal row, but never rescue a note by parking it in the
        # middle of the grid: this runs after the sightline has been cleared
        # and would otherwise put the block straight back in front of the
        # player's view.
        ordered = sorted(
            allowed_rows,
            key=lambda row: (blocks_the_sightline(x, row), abs(row - y), row),
        )
        for candidate in ordered:
            if playable_directions(hand, x, candidate, preferred_directions(state)):
                return candidate
        return y

    def _double_row(
        self,
        row: int | None,
        state: HandState,
        x: int,
        allowed_rows: tuple[int, ...],
    ) -> int:
        """Row for one half of a double, honouring the pattern if it named one.

        Otherwise take the row that leaves the block on this hand's return
        path rather than further along the swing it just made.
        """
        if row is not None:
            return self._resolve_row(row, allowed_rows, state)
        if not state.has_swung:
            return self._resolve_row(None, allowed_rows, state)
        return min(
            allowed_rows,
            key=lambda candidate: (
                chase_distance(state, x, candidate),
                abs(candidate - state.last_y),
            ),
        )

    def _clear_cell(
        self,
        beat: float,
        x: int,
        y: int,
        hand: Hand,
        allowed_rows: tuple[int, ...],
    ) -> tuple[int, int] | None:
        """Nudge a position out of a planned wall, or give up.

        Preference order is deliberate: keep the row and move sideways first,
        because a pattern's vertical shape carries more of its character than
        its exact column. Lanes on the hand's own side are tried before
        crossing the centre line.
        """
        if self.walls.is_empty or not self.walls.blocks(beat, x, y):
            return x, y

        for candidate_y in sorted(allowed_rows, key=lambda row: (abs(row - y), row)):
            for candidate_x in self._lane_preference(x, hand):
                if not self.walls.blocks(beat, candidate_x, candidate_y):
                    return candidate_x, candidate_y
        return None

    def _lane_preference(self, x: int, hand: Hand) -> list[int]:
        """Columns ordered by how good a substitute they are for `x`."""
        own_side = (0, 1) if hand is Hand.LEFT else (2, 3)

        def rank(candidate: int) -> tuple[int, int]:
            # Nearest column wins; ties break toward the hand's own half.
            return (abs(candidate - x), 0 if candidate in own_side else 1)

        return sorted(range(GRID_COLUMNS), key=rank)

    def _resolve_lane(self, offset: int | None, hand: Hand, state: HandState) -> int:
        """Turn a step's hand-relative offset into an actual column.

        Offsets count inward from the hand's own outer lane, so the same step
        puts red on the left of the grid and blue on the right without either
        being written down twice — and without a lane-2 step for one hand
        implying a lane-1 step for the other, which is what used to send both
        hands across the centre line together.
        """
        if offset is None:
            home = 0 if hand is Hand.LEFT else GRID_COLUMNS - 1
            return state.last_x if 0 <= state.last_x < GRID_COLUMNS else home
        offset = max(0, min(offset, self.profile.max_offset))
        return offset if hand is Hand.LEFT else GRID_COLUMNS - 1 - offset

    def _resolve_row(
        self, row: int | None, allowed_rows: tuple[int, ...], state: HandState
    ) -> int:
        if row is None:
            row = state.last_y
        row = max(0, min(row, GRID_ROWS - 1))
        if row in allowed_rows:
            return row
        return min(allowed_rows, key=lambda candidate: abs(candidate - row))

    def _resolve_direction(
        self,
        direction: CutDirection | None,
        mirror: bool,
        state: HandState,
        slot: RhythmSlot,
        x: int,
        y: int,
    ) -> CutDirection | None:
        if direction is not None:
            wanted = MIRRORED_DIRECTION[direction] if mirror else direction
            return self._honour_or_replace(wanted, state, slot, x, y)

        # No explicit direction: let parity choose, with a rare ANY on quiet
        # low-confidence events at easier difficulties.
        if (
            self.profile.any_direction_share > 0
            and slot.event.event_type is MusicalEventType.BEAT
            and self.rng.random() < self.profile.any_direction_share
        ):
            return CutDirection.ANY

        options = preferred_directions(state)
        if not options:
            return CutDirection.DOWN
        # Parity-valid is not the same as playable. These options are only
        # filtered by which way the wrist is facing, so the weighted pick below
        # was free to land on a cut 90 degrees from the last one — which is
        # where most of the remaining right-angle turns were coming from, not
        # from the patterns that declare their directions outright.
        gap = slot.beat - state.last_beat
        return self._direction_following_travel(
            self._sound_options(options, state, gap, x, y), state, x, y
        )

    def _swing_is_sound(
        self, direction: CutDirection, state: HandState, gap: float, x: int, y: int
    ) -> bool:
        """Can this hand actually make this cut, from there and from then?"""
        if unplayable_cut(state.hand, x, y, direction):
            return False
        if direction is CutDirection.ANY or not state.has_swung:
            return True
        needed = required_parity(direction)
        if needed is not None and needed is not state.parity:
            return False
        return angle_delta(state.last_direction, direction) >= opposition_target(gap)

    def _sound_options(
        self,
        options: tuple[CutDirection, ...],
        state: HandState,
        gap: float,
        x: int,
        y: int,
    ) -> tuple[CutDirection, ...]:
        """Narrow a hand's options to the cuts it can actually make.

        Horizontals stay in the pool: they are parity-neutral and so survive
        this test almost unconditionally, but excluding them for that reason
        drove their share to zero against a human 8-14% below Expert. How
        often they actually win is the profile's `horizontal_cut_bias` to
        decide, downstream.
        """
        sound = tuple(
            candidate
            for candidate in options
            if self._swing_is_sound(candidate, state, gap, x, y)
        )
        # Never fall back onto a cut the arm cannot make: reachability is not a
        # preference the pool is allowed to run out of.
        return sound or playable_directions(state.hand, x, y, options)

    def _honour_or_replace(
        self,
        wanted: CutDirection,
        state: HandState,
        slot: RhythmSlot,
        x: int,
        y: int,
    ) -> CutDirection:
        """Keep the pattern's cut direction unless the arm cannot make it.

        A pattern's directions carry its character, so they win whenever they
        are playable — this is a veto, not a free hand. They stop winning at
        the seam between two phrases: each pattern wrote its directions down
        in advance, neither knows what the other just did, and the player gets
        handed a right-angle turn at speed for no musical reason. That seam is
        where the last of our sub-135-degree transitions lived.

        When the pattern is overruled the replacement is the *closest* sound
        direction, so the shape survives the correction as far as it can.
        """
        gap = slot.beat - state.last_beat if state.has_swung else 99.0
        if self._swing_is_sound(wanted, state, gap, x, y):
            return wanted
        options = self._sound_options(preferred_directions(state), state, gap, x, y)
        if not options or all(
            not self._swing_is_sound(candidate, state, gap, x, y)
            for candidate in options
        ):
            # Nothing on offer is better; keep the pattern rather than invent
            # a direction that is equally unsound and off-character too.
            return wanted
        # Nearest in angle, but a horizontal is never the nearest thing to a
        # diagonal in character: swapping one in turns a swing into a flick and
        # changes what the pattern is. Substituting like for like kept Expert+
        # horizontals at 2% rather than 13%.
        keep_shape = wanted not in HORIZONTAL_CUTS
        return min(
            options,
            key=lambda candidate: (
                keep_shape and candidate in HORIZONTAL_CUTS,
                angle_delta(wanted, candidate),
            ),
        )

    def _direction_following_travel(
        self,
        options: tuple[CutDirection, ...],
        state: HandState,
        x: int,
        y: int,
    ) -> CutDirection:
        """Pick the parity-valid cut that continues the hand's motion.

        Always taking the first option meant always taking pure DOWN or pure
        UP: the diagonals sat in the list and were never reached, and maps came
        out 88% straight up-down against 27% for a human-made map of the same
        song. A piston is not how an arm moves.

        Reaching right and then cutting down-right carries the momentum
        through the block, which is what makes a run feel like one gesture
        rather than a series of separate stabs.
        """
        travel_x = x - state.last_x if state.has_swung else 0
        travel_y = y - state.last_y if state.has_swung else 0

        if travel_x == 0 and travel_y == 0:
            # Nothing to follow. Vary anyway, so a static passage does not
            # collapse into the same cut over and over.
            weights = [
                (1.0 if index == 0 else 0.62)
                * (
                    self.profile.horizontal_cut_bias
                    if candidate in HORIZONTAL_CUTS
                    else 1.0
                )
                for index, candidate in enumerate(options)
            ]
            return self._weighted_choice(options, weights)

        length = (travel_x**2 + travel_y**2) ** 0.5
        unit = (travel_x / length, travel_y / length)

        # A cross-body reach *is* a sweep. The reference maps cut horizontally
        # on 58% of their far-lane notes against about 2% of notes overall, so
        # the difficulty's horizontal bias — which is about ordinary
        # transitions — has no business applying to this one.
        sweeping = reach_cost(state.hand, x, 0.0) >= 1.0

        weights = []
        for index, candidate in enumerate(options):
            vector = DIRECTION_VECTORS[candidate]
            alignment = vector[0] * unit[0] + vector[1] * unit[1]
            # Rank order still matters — it encodes wrist comfort — but the
            # travel direction is what breaks the tie.
            weight = max(0.05, (1.0 + alignment) ** 2.2) * (1.0 - 0.12 * index)
            if candidate in HORIZONTAL_CUTS and not sweeping:
                weight *= self.profile.horizontal_cut_bias
            weights.append(weight)
        return self._weighted_choice(options, weights)

    def _weighted_choice(
        self, options: tuple[CutDirection, ...], weights: list[float]
    ) -> CutDirection:
        total = sum(weights)
        if total <= 0:
            return options[0]
        target = self.rng.random() * total
        running = 0.0
        for candidate, weight in zip(options, weights):
            running += weight
            if running >= target:
                return candidate
        return options[-1]

    def _advance(self, state: HandState, note: BeatNote) -> HandState:
        needed = required_parity(note.direction)
        was_reset = needed is not None and needed is not state.parity
        return replace(
            state,
            parity=resulting_parity(note.direction, state.parity),
            last_beat=note.beat,
            last_x=note.x,
            last_y=note.y,
            last_direction=note.direction,
            consecutive_resets=state.consecutive_resets + 1 if was_reset else 0,
        )

    # -- scoring -----------------------------------------------------------

    def _score(
        self,
        run: Run,
        pattern: PatternDefinition,
        notes: list[BeatNote],
    ) -> tuple[float, int, int]:
        """Score a realised candidate. Returns (score, resets, crossovers)."""
        weights = self.weights
        states = self.parity.snapshot()
        total = 0.0
        resets = 0
        crossovers = 0
        positions: list[tuple[int, int]] = []

        for note in notes:
            # Defence in depth: placement already routes around walls, but a
            # note inside one is never acceptable, so reject rather than
            # merely penalise.
            if self.walls.blocks(note.beat, note.x, note.y):
                return -1e6, 0, 0

            transition = self.parity.score_transition(
                states[note.hand], note.beat, note.x, note.y, note.direction
            )
            if not transition.feasible:
                return -1e6, 0, 0

            total += weights.parity * transition.score
            total += weights.movement * min(transition.travel / 3.0, 1.0)
            # What the *other* hand just did is part of whether this swing is
            # playable; the parity engine only sees one hand at a time.
            total -= weights.handoff_penalty * handoff_cost(
                states[note.hand.other], note.beat, note.x, note.direction
            )
            if transition.is_reset:
                resets += 1
                total -= weights.reset_penalty
            if transition.is_crossover:
                crossovers += 1
                # Charged in proportion to how far the hand actually reaches.
                # Flat, this was the last thing pricing a lean over the centre
                # line the same as an arm dragged to the far lane, and it kept
                # red out of lane 2 entirely (2% against a human 22%).
                total -= (
                    weights.hand_cross_penalty
                    * reach_cost(note.hand, note.x, self.profile.lean_cost)
                    * (1.0 - min(self.profile.crossover_probability * 4.0, 0.9))
                )
            positions.append((note.x, note.y))
            states[note.hand] = self._advance(states[note.hand], note)

        count = max(len(notes), 1)
        total /= count

        # --- musical match ---------------------------------------------
        # Strong events deserve emphatic placements; quiet ones do not.
        emphasis = sum(
            (0.5 + note.y * 0.25) * slot.event.strength
            for note, slot in zip(notes, self._slots_for(run, notes))
        ) / count
        total += weights.musical_match * emphasis

        # --- flow -------------------------------------------------------
        total += weights.flow * self._flow_score(notes)


        # --- position / vision blocking ---------------------------------
        centre_share = sum(1 for x, y in positions if x in (1, 2) and y >= 1) / count
        tolerance = self.profile.vision_block_tolerance
        if centre_share > tolerance:
            total -= weights.vision_block_penalty * (centre_share - tolerance) * 2.0
        total += weights.position * (1.0 - abs(centre_share - 0.35))

        # --- variety and repetition -------------------------------------
        unique_positions = len(set(positions)) / count
        total += weights.variety * unique_positions

        # --- height shape -------------------------------------------------
        # Keep the centre of the grid low, where it cannot block the view of
        # what is behind it, and let the outer columns carry the mid row.
        total += weights.height_shape * (
            sum(height_affinity(note.x, note.y) for note in notes) / count
        )

        # --- lane spread ------------------------------------------------
        # Reward each hand for sitting where real mappers put it. Without
        # this the library's centre-heavy lane vocabulary wins by default and
        # both hands live in the two middle columns for the whole song.
        total += weights.lane_spread * (
            sum(lane_affinity(note.hand, note.x) for note in notes) / count
        )
        if pattern.name in self._recent_patterns:
            occurrences = sum(1 for name in self._recent_patterns if name == pattern.name)
            total -= weights.repetition_penalty * occurrences / self.config.repetition_memory
        repeated_positions = sum(
            1 for position in positions if position in self._recent_positions
        )
        total -= weights.repetition_penalty * 0.35 * repeated_positions / count

        # --- style ------------------------------------------------------
        total += weights.style * (pattern.affinity(self.style) - 1.0)

        # --- wall choreography ------------------------------------------
        # A dodge wall should read as the map moving the player, so reward
        # notes that sit on the side it is herding them toward. The average is
        # taken over the affected notes only: a wall covering two beats of a
        # long run would otherwise have its signal diluted to nothing by the
        # notes it does not touch.
        if not self.walls.is_empty:
            dodge_bonus = 0.0
            affected = 0
            for note in notes:
                direction = self.walls.dodge_direction(note.beat)
                if direction == 0:
                    continue
                affected += 1
                centred = (note.x - (GRID_COLUMNS - 1) / 2) / ((GRID_COLUMNS - 1) / 2)
                dodge_bonus += max(0.0, centred * direction)
            if affected:
                # Weighted to stay audible against the momentum term: a
                # wall that does not actually move the player is just
                # scenery.
                total += weights.position * 14.0 * dodge_bonus / affected

        # --- stacked / duplicate notes ----------------------------------
        if len({(note.beat, note.x, note.y) for note in notes}) != len(notes):
            total -= weights.stack_penalty

        # --- complexity fit ---------------------------------------------
        # Prefer patterns whose complexity matches where intensity sits inside
        # the difficulty, rather than always reaching for the ceiling.
        wanted_complexity = self.profile.pattern_complexity * (0.55 + 0.45 * self.intensity)
        total -= 0.6 * abs(pattern.complexity - wanted_complexity)

        return total, resets, crossovers

    def _slots_for(self, run: Run, notes: list[BeatNote]) -> list[RhythmSlot]:
        """Pair each note with the slot it came from (doubles share a slot)."""
        by_beat = {slot.beat: slot for slot in run.slots}
        fallback = run.slots[0]
        return [by_beat.get(note.beat, fallback) for note in notes]

    def _flow_score(self, notes: list[BeatNote]) -> float:
        """Reward sequences whose swings continue in a consistent direction."""
        if len(notes) < 2:
            return 0.5

        score = 0.0
        for previous, following in zip(notes, notes[1:]):
            if previous.hand is following.hand:
                continue
            previous_vector = DIRECTION_VECTORS[previous.direction]
            following_vector = DIRECTION_VECTORS[following.direction]
            # Alternating hands should swing in opposing directions.
            dot = (
                previous_vector[0] * following_vector[0]
                + previous_vector[1] * following_vector[1]
            )
            score += (1.0 - dot) / 2.0
        return score / max(len(notes) - 1, 1)

    # -- commit ------------------------------------------------------------

    def _commit(self, candidate: Candidate) -> None:
        for note in candidate.notes:
            if blocks_the_sightline(note.x, note.y):
                self._centre_last_used[(note.x, note.y)] = note.beat
            note.resulting_parity = self.parity.apply(
                note.hand, note.beat, note.x, note.y, note.direction
            )
            self._recent_positions.append((note.x, note.y))
        self._recent_patterns.append(candidate.pattern.name)
        self.total_resets += candidate.resets
        self.total_crossovers += candidate.crossovers
        if candidate.notes:
            self._lead = candidate.notes[-1].hand.other
