"""Swing parity modelling."""

from __future__ import annotations

import pytest

from app.models.enums import CutDirection, Hand, Parity
from app.services.mapping.parity_engine import (
    HandState,
    ParityEngine,
    angle_delta,
    blocks_the_sightline,
    chase_distance,
    drags_across_body,
    far_cross_scale,
    handoff_cost,
    height_affinity,
    is_crossover,
    lane_affinity,
    opposition_target,
    playable_directions,
    preferred_directions,
    reach_cost,
    required_parity,
    resulting_parity,
    unplayable_cut,
    validate_sequence,
)


class TestParityRules:
    def test_down_cuts_need_forehand_and_leave_backhand(self):
        for direction in (CutDirection.DOWN, CutDirection.DOWN_LEFT, CutDirection.DOWN_RIGHT):
            assert required_parity(direction) is Parity.FOREHAND
            assert resulting_parity(direction, Parity.FOREHAND) is Parity.BACKHAND

    def test_up_cuts_need_backhand_and_leave_forehand(self):
        for direction in (CutDirection.UP, CutDirection.UP_LEFT, CutDirection.UP_RIGHT):
            assert required_parity(direction) is Parity.BACKHAND
            assert resulting_parity(direction, Parity.BACKHAND) is Parity.FOREHAND

    def test_horizontal_cuts_are_parity_neutral_but_flip_state(self):
        assert required_parity(CutDirection.LEFT) is None
        assert resulting_parity(CutDirection.LEFT, Parity.FOREHAND) is Parity.BACKHAND
        assert resulting_parity(CutDirection.LEFT, Parity.BACKHAND) is Parity.FOREHAND

    def test_preferred_direction_alternates_with_state(self):
        forehand = HandState(Hand.RIGHT, parity=Parity.FOREHAND)
        backhand = HandState(Hand.RIGHT, parity=Parity.BACKHAND)
        assert preferred_directions(forehand)[0] is CutDirection.DOWN
        assert preferred_directions(backhand)[0] is CutDirection.UP


class TestCrossovers:
    def test_left_hand_crosses_on_the_right_half(self):
        assert not is_crossover(Hand.LEFT, 0)
        assert not is_crossover(Hand.LEFT, 1)
        assert is_crossover(Hand.LEFT, 2)

    def test_right_hand_crosses_on_the_left_half(self):
        assert is_crossover(Hand.RIGHT, 1)
        assert not is_crossover(Hand.RIGHT, 2)

    def test_leaning_over_the_centre_costs_far_less_than_reaching_past_it(self):
        # Both are "crossovers" by the boolean, but human maps play the left
        # hand in lane 2 about 22% of the time and lane 3 about 0.7%. Charging
        # them the same is what pinned red into a single column.
        for lean in (0.0, 0.25, 0.68):
            assert reach_cost(Hand.LEFT, 2, lean) < reach_cost(Hand.LEFT, 3, lean)
            assert reach_cost(Hand.RIGHT, 1, lean) < reach_cost(Hand.RIGHT, 0, lean)

    def test_a_hand_pays_nothing_on_its_own_half(self):
        # Whatever a difficulty charges for the lean, its own half is free.
        for lean in (0.0, 0.68, 1.0):
            assert reach_cost(Hand.LEFT, 0, lean) == 0.0
            assert reach_cost(Hand.LEFT, 1, lean) == 0.0
            assert reach_cost(Hand.RIGHT, 2, lean) == 0.0
            assert reach_cost(Hand.RIGHT, 3, lean) == 0.0

    def test_only_the_lean_is_a_difficulty_choice(self):
        """The far lane always costs everything, whoever is playing."""
        for lean in (0.0, 0.25, 0.68, 1.0):
            assert reach_cost(Hand.LEFT, 2, lean) == lean
            assert reach_cost(Hand.RIGHT, 1, lean) == lean
            assert reach_cost(Hand.LEFT, 3, lean) == 1.0
            assert reach_cost(Hand.RIGHT, 0, lean) == 1.0

    def test_each_hand_is_most_at_home_in_its_outer_lane(self):
        left = [lane_affinity(Hand.LEFT, x) for x in range(4)]
        assert left == sorted(left, reverse=True)
        right = [lane_affinity(Hand.RIGHT, x) for x in range(4)]
        assert right == sorted(right)


class TestHandoff:
    """What one saber leaves behind for the other."""

    @staticmethod
    def other(direction=CutDirection.LEFT, x=3, beat=0.0):
        return HandState(Hand.LEFT, last_beat=beat, last_x=x, last_y=1,
                         last_direction=direction)

    def test_following_the_other_saber_through_the_same_lane_costs(self):
        # The reported shape: red cuts right-to-left in the far right lane,
        # then blue cuts right-to-left in the far right lane.
        assert handoff_cost(self.other(), 0.25, 3, CutDirection.LEFT) > 1.0

    def test_the_same_lane_the_other_way_costs_much_less(self):
        chasing = handoff_cost(self.other(), 0.25, 3, CutDirection.LEFT)
        trading = handoff_cost(self.other(), 0.25, 3, CutDirection.RIGHT)
        assert trading < chasing / 2.0

    def test_a_different_lane_is_cheaper_than_the_same_one(self):
        same = handoff_cost(self.other(), 0.25, 3, CutDirection.LEFT)
        apart = handoff_cost(self.other(), 0.25, 1, CutDirection.LEFT)
        assert apart < same

    def test_simultaneous_notes_are_free(self):
        # Two hands on one beat is a chord, and human maps are full of them.
        assert handoff_cost(self.other(), 0.0, 3, CutDirection.LEFT) == 0.0

    def test_the_cost_fades_as_the_gap_grows(self):
        tight = handoff_cost(self.other(), 0.1, 3, CutDirection.LEFT)
        loose = handoff_cost(self.other(), 0.6, 3, CutDirection.LEFT)
        assert 0.0 < loose < tight

    def test_nothing_is_owed_beyond_the_window(self):
        assert handoff_cost(self.other(), 4.0, 3, CutDirection.LEFT) == 0.0

    def test_nothing_is_owed_before_the_other_hand_has_swung(self):
        fresh = HandState(Hand.LEFT)
        assert handoff_cost(fresh, 1.0, 3, CutDirection.LEFT) == 0.0

    def test_reaching_past_where_the_other_hand_sits_costs(self):
        # A right-hand note to the left of where red is standing.
        red_at_two = HandState(Hand.LEFT, last_beat=0.0, last_x=2, last_y=1,
                               last_direction=CutDirection.DOWN)
        assert handoff_cost(red_at_two, 0.25, 1, CutDirection.UP) > 0.0
        assert handoff_cost(red_at_two, 0.25, 3, CutDirection.UP) == 0.0


class TestAngles:
    def test_opposite_directions_are_180_apart(self):
        assert angle_delta(CutDirection.UP, CutDirection.DOWN) == pytest.approx(180.0)

    def test_angle_delta_takes_the_short_way_round(self):
        assert angle_delta(CutDirection.RIGHT, CutDirection.DOWN_RIGHT) == pytest.approx(45.0)

    def test_any_direction_has_no_angle_cost(self):
        assert angle_delta(CutDirection.ANY, CutDirection.UP) == 0.0


class TestTransitionScoring:
    @staticmethod
    def engine(**kwargs):
        defaults = {
            "reset_min_gap_beats": 0.75,
            "movement_scale": 1.0,
            "crossover_probability": 0.1,
        }
        return ParityEngine(**{**defaults, **kwargs})

    def test_natural_alternation_beats_a_reset(self):
        engine = self.engine()
        state = HandState(Hand.RIGHT, parity=Parity.BACKHAND, last_beat=0.0,
                          last_direction=CutDirection.DOWN, last_x=2, last_y=1)
        good = engine.score_transition(state, 0.5, 2, 1, CutDirection.UP)
        bad = engine.score_transition(state, 0.5, 2, 1, CutDirection.DOWN)
        assert good.score > bad.score
        assert not good.is_reset
        assert bad.is_reset

    def test_rapid_reset_is_infeasible(self):
        engine = self.engine(reset_min_gap_beats=1.0)
        state = HandState(Hand.RIGHT, parity=Parity.BACKHAND, last_beat=0.0,
                          last_direction=CutDirection.DOWN, last_x=2, last_y=1)
        assert not engine.score_transition(state, 0.25, 2, 1, CutDirection.DOWN).feasible

    def test_reset_becomes_acceptable_with_recovery_time(self):
        engine = self.engine(reset_min_gap_beats=1.0)
        state = HandState(Hand.RIGHT, parity=Parity.BACKHAND, last_beat=0.0,
                          last_direction=CutDirection.DOWN, last_x=2, last_y=1)
        assert engine.score_transition(state, 4.0, 2, 1, CutDirection.DOWN).feasible

    def test_same_hand_notes_too_close_are_rejected(self):
        engine = self.engine()
        state = HandState(Hand.RIGHT, parity=Parity.FOREHAND, last_beat=0.0,
                          last_direction=CutDirection.UP, last_x=2, last_y=1)
        assert not engine.score_transition(state, 0.02, 2, 1, CutDirection.DOWN).feasible

    def test_crossovers_are_blocked_when_the_profile_forbids_them(self):
        engine = self.engine(crossover_probability=0.0)
        state = HandState(Hand.LEFT, parity=Parity.FOREHAND, last_beat=0.0,
                          last_direction=CutDirection.UP, last_x=1, last_y=1)
        result = engine.score_transition(state, 1.0, 3, 1, CutDirection.DOWN)
        assert not result.feasible
        assert result.is_crossover

    def test_long_travel_costs_more_when_time_is_short(self):
        engine = self.engine()
        state = HandState(Hand.RIGHT, parity=Parity.BACKHAND, last_beat=0.0,
                          last_direction=CutDirection.DOWN, last_x=0, last_y=0)
        rushed = engine.score_transition(state, 0.25, 3, 2, CutDirection.UP)
        relaxed = engine.score_transition(state, 2.0, 3, 2, CutDirection.UP)
        assert relaxed.score > rushed.score

    def test_apply_updates_state(self):
        engine = self.engine()
        parity = engine.apply(Hand.RIGHT, 1.0, 2, 1, CutDirection.DOWN)
        assert parity is Parity.BACKHAND
        state = engine.state(Hand.RIGHT)
        assert state.last_beat == 1.0 and state.last_x == 2
        assert state.last_direction is CutDirection.DOWN

    def test_snapshot_and_restore_are_lossless(self):
        engine = self.engine()
        snapshot = engine.snapshot()
        engine.apply(Hand.LEFT, 1.0, 1, 1, CutDirection.DOWN)
        engine.restore(snapshot)
        assert not engine.state(Hand.LEFT).has_swung


class TestSequenceValidation:
    def test_a_clean_alternating_sequence_passes(self):
        notes = [
            (0.0, Hand.RIGHT, 2, 1, CutDirection.DOWN),
            (0.5, Hand.LEFT, 1, 1, CutDirection.DOWN),
            (1.0, Hand.RIGHT, 2, 1, CutDirection.UP),
            (1.5, Hand.LEFT, 1, 1, CutDirection.UP),
        ]
        assert validate_sequence(notes, reset_min_gap_beats=0.75) == []

    def test_repeated_down_cuts_on_one_hand_are_reported(self):
        """The canonical unplayable pattern: DOWN DOWN DOWN with one hand."""
        notes = [
            (0.0, Hand.RIGHT, 2, 1, CutDirection.DOWN),
            (0.25, Hand.RIGHT, 2, 1, CutDirection.DOWN),
            (0.5, Hand.RIGHT, 2, 1, CutDirection.DOWN),
        ]
        problems = validate_sequence(notes, reset_min_gap_beats=0.75)
        assert len(problems) >= 2


class TestOppositionTightensWithSpeed:
    """90 degrees is conditional, not simply allowed (flow rule 2)."""

    def test_a_hand_with_time_may_turn_a_right_angle(self):
        assert opposition_target(1.5) == pytest.approx(90.0)

    def test_a_hand_with_none_must_really_reverse(self):
        assert opposition_target(0.0) == pytest.approx(135.0)

    def test_the_requirement_eases_as_the_gap_grows(self):
        targets = [opposition_target(gap) for gap in (0.0, 0.2, 0.4, 0.6, 0.8)]
        assert targets == sorted(targets, reverse=True)
        assert all(90.0 <= t <= 135.0 for t in targets)


class TestMomentum:
    """A block sits on the return path, not the follow-through (flow rule 4)."""

    @staticmethod
    def swung_down():
        return HandState(Hand.RIGHT, last_beat=0.0, last_x=2, last_y=2,
                         last_direction=CutDirection.DOWN)

    def test_a_block_below_a_downward_swing_is_a_chase(self):
        assert chase_distance(self.swung_down(), 2, 0) > 0.0

    def test_a_block_above_a_downward_swing_is_free(self):
        # The upward recovery passes through it, which is the whole point.
        assert chase_distance(self.swung_down(), 2, 2) == 0.0

    def test_a_hand_that_has_not_swung_owes_nothing(self):
        assert chase_distance(HandState(Hand.LEFT), 0, 0) == 0.0

    def test_chasing_further_costs_more(self):
        state = HandState(Hand.RIGHT, last_beat=0.0, last_x=3, last_y=2,
                          last_direction=CutDirection.LEFT)
        assert chase_distance(state, 1, 2) > chase_distance(state, 2, 2)


class TestTheCrossBodyReachNeedsRoom:
    """Setup and escape, expressed as time (flow rules 7 and 11).

    Charged flat, the far lane was simply never used — 0.0% of our notes
    against 3.3% of the reference maps' Hard. But those maps do not reach
    across *at speed*: the usual gap in is a whole beat.
    """

    def test_a_rushed_reach_pays_in_full(self):
        assert far_cross_scale(0.0) == pytest.approx(1.0)

    def test_room_makes_it_affordable(self):
        assert far_cross_scale(1.0) < 0.3

    def test_it_never_becomes_free(self):
        # It is always a big movement, just an affordable one.
        assert far_cross_scale(4.0) > 0.0

    def test_the_charge_only_eases(self):
        scales = [far_cross_scale(gap) for gap in (0.0, 0.25, 0.5, 0.75, 1.0, 2.0)]
        assert scales == sorted(scales, reverse=True)


class TestACrossedHandNeverCutsBack:
    """A hand reaching across the body must keep sweeping outward.

    Once the left hand is in the right half its arm lies across the chest, and
    a cut with any leftward component drags the saber back through the body and
    the other arm. The reference maps treat this as absolute: of 828 red notes
    in the right half, 4 cut back across. Ours did it on 11% of them.
    """

    def test_red_on_the_right_may_not_cut_leftwards(self):
        for lane in (2, 3):
            for direction in (CutDirection.LEFT, CutDirection.UP_LEFT, CutDirection.DOWN_LEFT):
                assert drags_across_body(Hand.LEFT, lane, direction)

    def test_blue_on_the_left_may_not_cut_rightwards(self):
        for lane in (0, 1):
            for direction in (CutDirection.RIGHT, CutDirection.UP_RIGHT, CutDirection.DOWN_RIGHT):
                assert drags_across_body(Hand.RIGHT, lane, direction)

    def test_sweeping_outward_is_always_fine(self):
        assert not drags_across_body(Hand.LEFT, 3, CutDirection.RIGHT)
        assert not drags_across_body(Hand.LEFT, 3, CutDirection.DOWN_RIGHT)
        assert not drags_across_body(Hand.RIGHT, 0, CutDirection.LEFT)
        assert not drags_across_body(Hand.RIGHT, 0, CutDirection.UP_LEFT)

    def test_vertical_cuts_are_always_fine(self):
        for lane in range(4):
            for direction in (CutDirection.UP, CutDirection.DOWN, CutDirection.ANY):
                assert not drags_across_body(Hand.LEFT, lane, direction)
                assert not drags_across_body(Hand.RIGHT, lane, direction)

    def test_a_hand_on_its_own_half_may_cut_either_way(self):
        for direction in (CutDirection.LEFT, CutDirection.RIGHT):
            assert not drags_across_body(Hand.LEFT, 0, direction)
            assert not drags_across_body(Hand.LEFT, 1, direction)
            assert not drags_across_body(Hand.RIGHT, 2, direction)
            assert not drags_across_body(Hand.RIGHT, 3, direction)

    def test_the_engine_refuses_the_swing_outright(self):
        engine = ParityEngine(reset_min_gap_beats=0.5, movement_scale=1.0,
                              crossover_probability=0.2)
        state = HandState(Hand.LEFT, parity=Parity.FOREHAND, last_beat=0.0,
                          last_x=1, last_y=1, last_direction=CutDirection.UP)
        result = engine.score_transition(state, 1.0, 3, 1, CutDirection.LEFT)
        assert not result.feasible
        assert "across the body" in result.reason


class TestReachesTheArmCannotMake:
    """The three rules beyond the body-drag one, all read off the reference maps.

    Together with `drags_across_body` they rule out 4.9% of what the generator
    used to place and 0.21% of the notes in the reference maps.
    """

    def test_the_far_crossed_lane_takes_no_vertical_swing(self):
        # Arm fully across the body; up or down has to travel the chest.
        for direction in (CutDirection.UP, CutDirection.DOWN):
            assert unplayable_cut(Hand.LEFT, 3, 1, direction) == "B"
            assert unplayable_cut(Hand.RIGHT, 0, 1, direction) == "B"

    def test_the_far_crossed_lane_still_takes_an_outward_sweep(self):
        assert not unplayable_cut(Hand.LEFT, 3, 1, CutDirection.RIGHT)
        assert not unplayable_cut(Hand.LEFT, 3, 1, CutDirection.DOWN_RIGHT)
        assert not unplayable_cut(Hand.RIGHT, 0, 1, CutDirection.LEFT)

    def test_the_top_row_takes_upward_cuts_only(self):
        # A top-row note is around head height: cutting down through it means
        # starting above your own head. 2 of 11,293 reference notes do it.
        for direction in (CutDirection.DOWN, CutDirection.DOWN_LEFT,
                          CutDirection.DOWN_RIGHT, CutDirection.LEFT,
                          CutDirection.RIGHT):
            assert unplayable_cut(Hand.LEFT, 1, 2, direction) == "C"
        for direction in (CutDirection.UP, CutDirection.UP_LEFT, CutDirection.UP_RIGHT):
            assert not unplayable_cut(Hand.LEFT, 1, 2, direction)

    def test_the_bottom_row_takes_no_scoop_toward_the_middle(self):
        assert unplayable_cut(Hand.LEFT, 1, 0, CutDirection.UP_RIGHT) == "D"
        assert unplayable_cut(Hand.RIGHT, 2, 0, CutDirection.UP_LEFT) == "D"

    def test_the_bottom_row_still_takes_the_outward_scoop(self):
        assert not unplayable_cut(Hand.LEFT, 1, 0, CutDirection.UP_LEFT)
        assert not unplayable_cut(Hand.RIGHT, 2, 0, CutDirection.UP_RIGHT)
        assert not unplayable_cut(Hand.LEFT, 1, 0, CutDirection.UP)

    def test_any_is_never_ruled_out(self):
        for x in range(4):
            for y in range(3):
                assert not unplayable_cut(Hand.LEFT, x, y, CutDirection.ANY)

    def test_playable_directions_filters_and_keeps_order(self):
        options = (CutDirection.DOWN, CutDirection.UP, CutDirection.DOWN_LEFT)
        assert playable_directions(Hand.LEFT, 1, 2, options) == (CutDirection.UP,)

    def test_a_hand_on_its_own_half_mid_row_keeps_everything(self):
        options = tuple(CutDirection)
        assert len(playable_directions(Hand.LEFT, 0, 1, options)) == len(options)

    def test_the_engine_refuses_each_of_them(self):
        engine = ParityEngine(reset_min_gap_beats=0.5, movement_scale=1.0,
                              crossover_probability=0.2)
        state = HandState(Hand.LEFT, parity=Parity.FOREHAND, last_beat=0.0,
                          last_x=1, last_y=1, last_direction=CutDirection.UP)
        for x, y, direction in ((3, 1, CutDirection.DOWN), (1, 2, CutDirection.DOWN)):
            assert not engine.score_transition(state, 1.0, x, y, direction).feasible


class TestTheGridHasAShape:
    """Centre columns low, outer columns at mid height."""

    def test_the_centre_of_the_grid_at_eye_level_is_the_blocking_cell(self):
        for x in (1, 2):
            assert blocks_the_sightline(x, 1)
            assert not blocks_the_sightline(x, 0)
            assert not blocks_the_sightline(x, 2)

    def test_the_outer_columns_never_block(self):
        for x in (0, 3):
            for y in range(3):
                assert not blocks_the_sightline(x, y)

    def test_centre_columns_want_the_floor(self):
        # Eye level in the centre is what hides the notes behind it, so those
        # columns sit low. An evenly-used grid was tried instead and played
        # worse: it costs the rest and some of the transition quality.
        for x in (1, 2):
            assert height_affinity(x, 0) > height_affinity(x, 1)
            assert height_affinity(x, 0) > height_affinity(x, 2)

    def test_outer_columns_want_mid_height(self):
        for x in (0, 3):
            assert height_affinity(x, 1) > height_affinity(x, 0)
            assert height_affinity(x, 1) > height_affinity(x, 2)
