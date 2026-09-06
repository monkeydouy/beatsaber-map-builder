"""End-to-end mapping quality checks against the generated audio fixture.

These are the tests that actually defend the product claim: that the generated
map is playable, that difficulty means something, and that the same inputs give
the same map.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

import pytest

from app.models.beatmap import GRID_COLUMNS, GRID_ROWS
from app.models.enums import (
    DIRECTION_VECTORS,
    HORIZONTAL_CUTS,
    CutDirection,
    Difficulty,
    Hand,
    MappingStyle,
    ObstacleKind,
)
from app.services.mapping.difficulty_profiles import PROFILES
from app.services.mapping.parity_engine import (
    angle_delta,
    blocks_the_sightline,
    drags_across_body,
    unplayable_cut,
    validate_sequence,
)

pytestmark = pytest.mark.slow

ALL_DIFFICULTIES = list(Difficulty)


def fingerprint(result) -> str:
    payload = [
        [note.beat, note.hand.value, note.x, note.y, note.direction.value]
        for note in result.beatmap.notes
    ]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


@pytest.fixture(scope="module")
def maps(analysis):
    """One map per difficulty, generated once and shared across the module."""
    from app.services.mapping.pipeline import GenerationOptions, generate_beatmap

    return {
        difficulty: generate_beatmap(
            analysis,
            GenerationOptions(difficulty=difficulty, intensity=0.6, seed=1234),
        )
        for difficulty in ALL_DIFFICULTIES
    }


class TestStructuralValidity:
    def test_every_difficulty_produces_a_valid_map(self, maps):
        for difficulty, result in maps.items():
            assert result.validation.ok, (
                f"{difficulty.label} failed validation: {result.validation.errors}"
            )

    def test_every_difficulty_produces_notes(self, maps):
        for difficulty, result in maps.items():
            assert result.beatmap.statistics.total_notes > 50, difficulty.label

    def test_all_notes_are_on_the_grid(self, maps):
        for difficulty, result in maps.items():
            for note in result.beatmap.notes:
                assert 0 <= note.x < GRID_COLUMNS, difficulty.label
                assert 0 <= note.y < GRID_ROWS, difficulty.label
                assert note.direction in CutDirection
                assert note.beat >= 0

    def test_notes_are_sorted_by_beat(self, maps):
        for result in maps.values():
            beats = [note.beat for note in result.beatmap.notes]
            assert beats == sorted(beats)

    def test_no_two_notes_share_a_position_and_beat(self, maps):
        for difficulty, result in maps.items():
            seen = {(round(n.beat, 4), n.x, n.y) for n in result.beatmap.notes}
            assert len(seen) == len(result.beatmap.notes), difficulty.label

    def test_no_same_hand_notes_at_the_same_instant(self, maps):
        for difficulty, result in maps.items():
            seen = {(round(n.beat, 4), n.hand) for n in result.beatmap.notes}
            assert len(seen) == len(result.beatmap.notes), difficulty.label

    def test_easy_never_uses_the_top_row(self, maps):
        assert all(note.y <= 1 for note in maps[Difficulty.EASY].beatmap.notes)


class TestPlayability:
    def test_parity_problems_stay_rare(self, maps):
        """A few deliberate resets are fine; a map full of them is not."""
        for difficulty, result in maps.items():
            notes = result.beatmap.notes
            problems = validate_sequence(
                [(n.beat, n.hand, n.x, n.y, n.direction) for n in notes],
                reset_min_gap_beats=PROFILES[difficulty].reset_min_gap_beats,
            )
            ratio = len(problems) / max(len(notes), 1)
            assert ratio < 0.03, f"{difficulty.label}: {ratio:.1%} parity problems"

    def test_no_rapid_repeated_down_cuts_on_one_hand(self, maps):
        """The classic unplayable shape the parity engine exists to prevent."""
        for difficulty, result in maps.items():
            by_hand: dict[int, list] = {}
            for note in result.beatmap.notes:
                by_hand.setdefault(note.hand.value, []).append(note)
            for notes in by_hand.values():
                for first, second, third in zip(notes, notes[1:], notes[2:]):
                    same = first.direction is second.direction is third.direction
                    rapid = third.beat - first.beat < 1.0
                    assert not (same and rapid and first.direction is CutDirection.DOWN), (
                        f"{difficulty.label}: three rapid DOWN cuts at beat {first.beat}"
                    )

    def test_hands_stay_roughly_balanced(self, maps):
        for difficulty, result in maps.items():
            stats = result.beatmap.statistics
            balance = min(stats.left_notes, stats.right_notes) / max(
                stats.left_notes, stats.right_notes, 1
            )
            assert balance > 0.7, f"{difficulty.label}: balance {balance:.2f}"

    def test_easy_has_no_crossovers(self, maps):
        assert maps[Difficulty.EASY].beatmap.statistics.crossovers == 0

    def test_directional_cuts_dominate_over_any(self, maps):
        for difficulty, result in maps.items():
            notes = result.beatmap.notes
            any_share = sum(1 for n in notes if n.direction is CutDirection.ANY) / len(notes)
            assert any_share < 0.2, f"{difficulty.label}: {any_share:.1%} ANY cuts"

    def test_hands_do_not_chase_each_other_through_the_same_lane(self, maps):
        """The shape a player notices: red cuts one way in a lane, then blue
        follows through the same lane the same way a moment later.

        Each hand's own parity is fine, which is why this survived until the
        generator started scoring the two hands against each other. Across the
        reference maps it happens in 0.6% of sequential cross-hand pairs.
        """
        for difficulty, result in maps.items():
            notes = sorted(result.beatmap.notes, key=lambda note: note.beat)
            pairs = chases = 0
            for first, second in zip(notes, notes[1:]):
                gap = second.beat - first.beat
                if first.hand is second.hand or not 0.0 < gap <= 0.55:
                    continue
                pairs += 1
                if first.x == second.x and first.direction is second.direction:
                    chases += 1
            if pairs < 20:
                continue
            share = chases / pairs
            assert share <= 0.01, f"{difficulty.label}: {share:.1%} of handoffs chase"

    def test_the_far_lane_stays_rare_for_the_wrong_hand(self, maps):
        """Red in the rightmost lane is a full cross-body drag.

        Human maps use it for well under 2% of notes; the flat crossover cost
        we used to charge gave the scorer no reason to prefer a lean over the
        centre line to a reach all the way past it.
        """
        for difficulty, result in maps.items():
            notes = result.beatmap.notes
            far = sum(
                1
                for note in notes
                if (note.hand is Hand.LEFT and note.x == 3)
                or (note.hand is Hand.RIGHT and note.x == 0)
            )
            share = far / len(notes)
            assert share < 0.02, f"{difficulty.label}: {share:.1%} far-lane notes"

    def test_each_hand_uses_its_own_outer_lane_most(self, maps):
        """Human mappers treat the outer lane as a hand's home position.

        Ours used to park 70% of red notes in lane 1 because the pattern
        library counts columns absolutely and is centre-heavy.
        """
        for difficulty, result in maps.items():
            if difficulty is Difficulty.EASY:
                continue  # too few notes for the distribution to mean anything
            for hand, home in ((Hand.LEFT, 0), (Hand.RIGHT, 3)):
                lanes = Counter(n.x for n in result.beatmap.notes if n.hand is hand)
                total = sum(lanes.values())
                assert lanes[home] / total > 0.3, (
                    f"{difficulty.label} {hand.name}: only "
                    f"{lanes[home] / total:.1%} in lane {home}"
                )

    def test_hands_use_all_three_of_their_reachable_columns(self, maps):
        """Home, own inner lane, and a lean over the centre line.

        The reference maps spread red across lanes 0/1/2 at roughly 44/33/22%.
        Ours managed 25/70/4 when the library counted columns absolutely: a
        lean for one hand implied a lean for the other, both crossed at once,
        and the scorer was right to throw it away. Hand-relative offsets are
        what let the two sides move independently.
        """
        # Bands bracket what the reference maps do at each level. Easy and
        # Normal keep each hand strictly on its own half; from Hard the lean
        # opens up steadily. It is a ladder, not one number — a single global
        # lean cost put Hard at 18%, which reads as an Expert map slowed down.
        # Widened when an even grid was adopted. Lane 2 is shared — red's lean
        # is blue's own inner lane — so an evenly used *cell* still leaves each
        # hand a minority of it, and the per-hand share drops accordingly.
        bands = {
            Difficulty.EASY: (0.0, 0.0),
            Difficulty.NORMAL: (0.0, 0.0),
            Difficulty.HARD: (0.02, 0.16),
            Difficulty.EXPERT: (0.05, 0.28),
            Difficulty.EXPERT_PLUS: (0.08, 0.34),
        }
        for difficulty, result in maps.items():
            low, high = bands[difficulty]
            for hand, lean in ((Hand.LEFT, 2), (Hand.RIGHT, 1)):
                lanes = Counter(n.x for n in result.beatmap.notes if n.hand is hand)
                total = sum(lanes.values())
                if total < 60:
                    continue
                share = lanes[lean] / total
                assert low <= share <= high, (
                    f"{difficulty.label} {hand.name}: {share:.1%} in lane {lean}, "
                    f"expected {low:.0%}-{high:.0%}"
                )

    def test_the_far_lane_is_touched_and_left(self, maps):
        """Flow rule 7: never trap a hand on the opposite side.

        Read off the reference maps, where all 110 visits to the far lane last
        exactly one note — the hand comes from home, reaches across, and goes
        straight back. It is a gesture, not a position.
        """
        for difficulty, result in maps.items():
            for hand, far in ((Hand.LEFT, GRID_COLUMNS - 1), (Hand.RIGHT, 0)):
                seq = [n for n in sorted(result.beatmap.notes, key=lambda n: n.beat)
                       if n.hand is hand]
                for first, second in zip(seq, seq[1:]):
                    assert not (first.x == far and second.x == far), (
                        f"{difficulty.label}: {hand.name} left stranded in lane "
                        f"{far} at beat {second.beat}"
                    )

    def test_reaching_across_the_body_gets_room_either_side(self, maps):
        """Flow rule 11: setup, motion, escape — with time for each."""
        for difficulty, result in maps.items():
            for hand, far in ((Hand.LEFT, GRID_COLUMNS - 1), (Hand.RIGHT, 0)):
                seq = [n for n in sorted(result.beatmap.notes, key=lambda n: n.beat)
                       if n.hand is hand]
                for before, crossing, after in zip(seq, seq[1:], seq[2:]):
                    if crossing.x != far:
                        continue
                    assert crossing.beat - before.beat >= 0.4, (
                        f"{difficulty.label}: no setup into lane {far} at "
                        f"beat {crossing.beat}"
                    )
                    assert after.beat - crossing.beat >= 0.4, (
                        f"{difficulty.label}: no escape from lane {far} at "
                        f"beat {crossing.beat}"
                    )

    def test_the_easier_difficulties_never_reach_across(self, maps):
        for difficulty in (Difficulty.EASY, Difficulty.NORMAL):
            notes = maps[difficulty].beatmap.notes
            assert not [
                n for n in notes
                if (n.hand is Hand.LEFT and n.x == GRID_COLUMNS - 1)
                or (n.hand is Hand.RIGHT and n.x == 0)
            ], difficulty.label

    def test_every_note_is_a_swing_the_arm_can_make(self, maps):
        """All four reach rules, enforced on the finished map.

        Read off the reference maps rather than reasoned out: these are the
        position-and-direction combinations real mappers avoid outright.
        """
        for difficulty, result in maps.items():
            for note in result.beatmap.notes:
                rule = unplayable_cut(note.hand, note.x, note.y, note.direction)
                assert not rule, (
                    f"{difficulty.label}: rule {rule} — {note.hand.name} at lane "
                    f"{note.x} row {note.y} cutting {note.direction.name} "
                    f"at beat {note.beat}"
                )

    def test_no_crossed_hand_cuts_back_across_the_body(self, maps):
        """The most obviously wrong-feeling block a player meets.

        Red in lanes 3-4 asked to swing right-to-left, or blue in lanes 1-2
        asked to swing left-to-right: the arm is already extended across the
        chest and the cut drags it back through the other one.
        """
        for difficulty, result in maps.items():
            for note in result.beatmap.notes:
                assert not drags_across_body(note.hand, note.x, note.direction), (
                    f"{difficulty.label}: {note.hand.name} in lane {note.x} "
                    f"cutting {note.direction.name} at beat {note.beat}"
                )

    def test_no_run_of_notes_lands_on_one_centre_cell(self, maps):
        """What hides a note is another note in front of it, on the same spot.

        One block at eye level in the middle of the grid hides nothing; a run
        of them arriving on the same cell hides everything behind the first.
        An earlier version of this rule gated the centre on how busy the song
        was, which is a far broader net — it emptied the middle of the grid to
        keep a much narrower problem away.
        """
        for difficulty, result in maps.items():
            notes = sorted(result.beatmap.notes, key=lambda note: note.beat)
            for first, second in zip(notes, notes[1:]):
                if not blocks_the_sightline(first.x, first.y):
                    continue
                assert (first.x, first.y) != (second.x, second.y), (
                    f"{difficulty.label}: two notes running on centre cell "
                    f"({first.x}, {first.y}) at beat {second.beat}"
                )

    def test_the_middle_of_the_grid_is_not_simply_banned(self, maps):
        """It is a crowding rule, not a hole in the grid.

        Held as an outright ban the centre read as dead space. Open passages
        are free to use it.
        """
        used = total = 0
        for result in maps.values():
            for note in result.beatmap.notes:
                total += 1
                if blocks_the_sightline(note.x, note.y):
                    used += 1
        assert total > 200
        assert used > 0, "the centre of the grid is never used at all"

    def test_horizontal_cuts_thin_out_as_difficulty_rises(self, maps):
        """Sideways travel aligns perfectly with LEFT/RIGHT, so unweighted they
        won every lateral transition and every lane change became a flick.

        Real maps use them as a readable gesture while there is time for one
        and drop them once there is not: across the reference maps horizontals
        run 8-14% up to Hard and 1.6-2.4% at Expert and above.
        """
        ceilings = {
            Difficulty.EASY: 0.20,
            Difficulty.NORMAL: 0.22,
            Difficulty.HARD: 0.16,
            Difficulty.EXPERT: 0.08,
            Difficulty.EXPERT_PLUS: 0.06,
        }
        for difficulty, result in maps.items():
            notes = result.beatmap.notes
            share = sum(1 for n in notes if n.direction in HORIZONTAL_CUTS) / len(notes)
            assert share < ceilings[difficulty], (
                f"{difficulty.label}: {share:.1%} horizontal cuts"
            )

    def test_fast_transitions_really_reverse(self, maps):
        """Flow rule 2: at speed a hand must turn by 135 degrees or more.

        A right-angle turn reverses the wrist but carries the arm on the same
        way across the body. The reference maps clear 135 degrees on 99.5% of
        their fast same-hand transitions; ours sat at 86% while a flat
        90-degree threshold treated every one of them as fine.

        Aggregated across difficulties on purpose. The fixture yields only
        40-120 transitions each, where a handful of notes swings the
        percentage by ten points, so a per-difficulty bar would measure noise.
        """
        good = total = 0
        for result in maps.values():
            for hand in (Hand.LEFT, Hand.RIGHT):
                seq = [n for n in sorted(result.beatmap.notes, key=lambda n: n.beat)
                       if n.hand is hand]
                for first, second in zip(seq, seq[1:]):
                    if not 0.0 < second.beat - first.beat <= 0.55:
                        continue
                    total += 1
                    if angle_delta(first.direction, second.direction) >= 135.0:
                        good += 1
        assert total > 100
        share = good / total
        # The bar is set for this fixture, which runs about five points below
        # real songs here — it is sparse, synthetic, and yields ~240 fast
        # transitions, so a handful of notes moves it a whole point. Measured
        # against the three reference songs the generator sits at 99.5%, level
        # with the maps it is copying. What this guards is the 86% the flat
        # 90-degree threshold used to produce.
        # Lowered when an even grid was adopted: spreading notes across every
        # cell forces some turns the hand would rather not make. Real songs
        # sit at 92%, down from 99.5% with the shaped grid — the cost is real
        # and this records it rather than hiding it.
        assert share > 0.86, f"only {share:.1%} of fast transitions really reverse"

    def test_notes_sit_on_the_return_path(self, maps):
        """Flow rule 4/15: the next block belongs where the recovery swing
        goes, not further along the swing just made.

        This is the rule that reads backwards from the obvious — "put the next
        note where the arm is heading" is wrong, because the arm is about to
        come back. Across the reference maps only 1.9% of same-hand follow-ups
        sit along the previous swing.
        """
        chases = total = 0
        for result in maps.values():
            for hand in (Hand.LEFT, Hand.RIGHT):
                seq = [n for n in sorted(result.beatmap.notes, key=lambda n: n.beat)
                       if n.hand is hand]
                for first, second in zip(seq, seq[1:]):
                    if not 0.0 < second.beat - first.beat <= 0.75:
                        continue
                    vector = DIRECTION_VECTORS[first.direction]
                    dx, dy = second.x - first.x, second.y - first.y
                    if (dx or dy) and (vector[0] or vector[1]):
                        total += 1
                        if vector[0] * dx + vector[1] * dy > 0:
                            chases += 1
        assert total > 100
        share = chases / total
        # The reference maps sit at 1.9%. An earlier config drove this to 0.05%
        # with a chase penalty three times heavier, and that config was worse
        # to play — being stricter than the maps we are copying is not a goal.
        assert share < 0.04, f"{share:.1%} of follow-ups chase the previous swing"

    def test_doubles_never_swing_into_each_other(self, maps):
        """Flow rule 9: the hands must never be told to clap."""
        for difficulty, result in maps.items():
            by_beat: dict[float, list] = {}
            for note in result.beatmap.notes:
                by_beat.setdefault(round(note.beat, 4), []).append(note)
            for beat, group in by_beat.items():
                if len(group) != 2 or group[0].hand is group[1].hand:
                    continue
                left = next(n for n in group if n.hand is Hand.LEFT)
                right = next(n for n in group if n.hand is Hand.RIGHT)
                converging = (
                    DIRECTION_VECTORS[left.direction][0] > 0
                    and DIRECTION_VECTORS[right.direction][0] < 0
                )
                assert not (converging and left.y == right.y and right.x - left.x <= 2), (
                    f"{difficulty.label}: hands swing together at beat {beat}"
                )

    def test_positions_are_varied(self, maps):
        """A map that hammers one cell is not a map."""
        for difficulty, result in maps.items():
            counts = Counter((n.x, n.y) for n in result.beatmap.notes)
            assert len(counts) >= 6, difficulty.label
            top_share = counts.most_common(1)[0][1] / len(result.beatmap.notes)
            assert top_share < 0.35, f"{difficulty.label}: {top_share:.1%} in one cell"

    def test_patterns_are_varied(self, maps):
        for difficulty, result in maps.items():
            used = {note.pattern for note in result.beatmap.notes if note.pattern}
            assert len(used) >= 3, f"{difficulty.label}: only used {used}"

    def test_walls_never_overlap_notes(self, maps):
        for difficulty, result in maps.items():
            for obstacle in result.beatmap.obstacles:
                for note in result.beatmap.notes:
                    if not obstacle.beat <= note.beat <= obstacle.end_beat:
                        continue
                    blocked = obstacle.covers_column(note.x) and (
                        obstacle.y <= note.y < obstacle.y + obstacle.height
                    )
                    assert not blocked, difficulty.label


class TestDifficultyProgression:
    def test_each_difficulty_is_denser_than_the_one_below(self, maps):
        previous = 0.0
        for difficulty in ALL_DIFFICULTIES:
            current = maps[difficulty].beatmap.statistics.average_nps
            assert current > previous, (
                f"{difficulty.label} ({current:.2f}) is not denser than the previous "
                f"difficulty ({previous:.2f})"
            )
            previous = current

    def test_density_respects_each_profile_ceiling(self, maps):
        for difficulty, result in maps.items():
            profile = PROFILES[difficulty]
            assert result.beatmap.statistics.peak_nps <= profile.peak_nps * 1.25, (
                difficulty.label
            )

    def test_difficulties_produce_genuinely_different_maps(self, maps):
        prints = {fingerprint(result) for result in maps.values()}
        assert len(prints) == len(maps)

    def test_higher_difficulties_use_finer_subdivisions(self, maps):
        def offbeat_share(result):
            notes = result.beatmap.notes
            return sum(1 for n in notes if abs(n.beat - round(n.beat)) > 1e-6) / len(notes)

        assert offbeat_share(maps[Difficulty.EASY]) < offbeat_share(maps[Difficulty.EXPERT])

    def test_note_jump_speed_rises_with_difficulty(self, maps):
        speeds = [maps[d].beatmap.note_jump_speed for d in ALL_DIFFICULTIES]
        assert speeds == sorted(speeds)


class TestDeterminism:
    def test_the_same_seed_produces_the_same_map(self, make_map):
        assert fingerprint(make_map(seed=42)) == fingerprint(make_map(seed=42))

    def test_a_different_seed_produces_a_different_map(self, make_map):
        assert fingerprint(make_map(seed=42)) != fingerprint(make_map(seed=43))

    def test_different_seeds_still_respect_the_profile(self, make_map):
        profile = PROFILES[Difficulty.EXPERT]
        for seed in (1, 500, 99999):
            result = make_map(seed=seed)
            assert result.validation.ok
            assert result.beatmap.statistics.peak_nps <= profile.peak_nps * 1.25

    def test_lighting_does_not_affect_note_generation(self, make_map):
        """Lighting is derived from the musical timeline, never from the notes,
        and each stage draws from its own RNG stream — so toggling it must not
        shift a single note."""
        lit = make_map(seed=7, enable_lighting=True)
        unlit = make_map(seed=7, enable_lighting=False)
        assert fingerprint(lit) == fingerprint(unlit)

    def test_walls_do_affect_note_generation(self, make_map):
        """Walls are planned before notes, so they shape where notes can go.

        This is the inverse of the lighting property above, and it is the whole
        point of planning walls first: if disabling walls left the notes
        untouched, the generator would not be routing around them at all.
        """
        walled = make_map(seed=7, enable_walls=True)
        unwalled = make_map(seed=7, enable_walls=False)
        assert walled.beatmap.statistics.total_walls > 0
        assert fingerprint(walled) != fingerprint(unwalled)


class TestIntensity:
    def test_intensity_raises_density_within_the_difficulty(self, make_map):
        low = make_map(intensity=0.0).beatmap.statistics.average_nps
        high = make_map(intensity=1.0).beatmap.statistics.average_nps
        assert high > low

    def test_intensity_never_escapes_the_difficulty(self, make_map):
        """Expert at intensity 1.0 must not become an Expert+ map."""
        profile = PROFILES[Difficulty.EXPERT]
        for intensity in (0.0, 0.25, 0.5, 0.75, 1.0):
            stats = make_map(intensity=intensity).beatmap.statistics
            assert stats.peak_nps <= profile.peak_nps
            assert stats.average_nps <= profile.target_nps_max * 1.15

    def test_max_intensity_hard_stays_within_hard(self, make_map):
        """Profile ranges overlap by design, so Hard at full intensity may well
        be denser than Expert at zero. What must hold is that it stays inside
        Hard's own boundaries."""
        profile = PROFILES[Difficulty.HARD]
        stats = make_map(difficulty=Difficulty.HARD, intensity=1.0).beatmap.statistics
        assert stats.average_nps <= profile.target_nps_max * 1.15
        assert stats.peak_nps <= profile.peak_nps


class TestStyles:
    def test_each_style_produces_a_distinct_valid_map(self, make_map):
        prints = set()
        for style in MappingStyle:
            result = make_map(style=style)
            assert result.validation.ok, style.value
            prints.add(fingerprint(result))
        assert len(prints) == len(list(MappingStyle))

    def test_dance_moves_more_than_technical(self, make_map):
        def travel(result):
            notes = result.beatmap.notes
            total = sum(
                ((b.x - a.x) ** 2 + (b.y - a.y) ** 2) ** 0.5
                for a, b in zip(notes, notes[1:])
            )
            return total / max(len(notes) - 1, 1)

        assert travel(make_map(style=MappingStyle.DANCE)) > travel(
            make_map(style=MappingStyle.TECHNICAL)
        )

    def test_dance_uses_the_outer_lanes_more_than_technical(self, make_map):
        """What separates the styles is where the hands go, not how often they
        cross. Dance sweeps the outside; technical works tight and central."""

        def outer_share(result):
            notes = result.beatmap.notes
            return sum(1 for note in notes if note.x in (0, 3)) / len(notes)

        assert outer_share(make_map(style=MappingStyle.DANCE)) > outer_share(
            make_map(style=MappingStyle.TECHNICAL)
        )

    def test_technical_reaches_for_its_own_patterns(self, make_map):
        from collections import Counter

        result = make_map(style=MappingStyle.TECHNICAL)
        used = Counter(note.pattern for note in result.beatmap.notes)
        technical_notes = sum(
            count
            for name, count in used.items()
            if name in {"TechnicalRotation", "TowerTap", "ComplexCrossoverStream"}
        )
        balanced = make_map(style=MappingStyle.BALANCED)
        balanced_used = Counter(note.pattern for note in balanced.beatmap.notes)
        balanced_technical = sum(
            count
            for name, count in balanced_used.items()
            if name in {"TechnicalRotation", "TowerTap", "ComplexCrossoverStream"}
        )
        assert technical_notes > balanced_technical


class TestWallPlanning:
    """Walls are reserved before notes exist, so notes must route around them."""

    def test_no_note_ever_sits_inside_a_wall(self, maps):
        for difficulty, result in maps.items():
            beatmap = result.beatmap
            for obstacle in beatmap.obstacles:
                for note in beatmap.notes:
                    if not obstacle.beat <= note.beat <= obstacle.end_beat:
                        continue
                    inside_columns = obstacle.covers_column(note.x)
                    inside_rows = obstacle.y <= note.y < obstacle.y + obstacle.height
                    assert not (inside_columns and inside_rows), (
                        f"{difficulty.label}: note at beat {note.beat} "
                        f"({note.x},{note.y}) sits inside a {obstacle.kind.value} wall"
                    )

    def test_dodge_walls_leave_their_lane_completely_empty(self, maps):
        """Not merely 'few notes' — the reserved lane must be untouched."""
        for difficulty, result in maps.items():
            for obstacle in result.beatmap.obstacles:
                if obstacle.kind is ObstacleKind.CROUCH:
                    continue
                occupying = [
                    note
                    for note in result.beatmap.notes
                    if obstacle.beat <= note.beat <= obstacle.end_beat
                    and obstacle.covers_column(note.x)
                ]
                assert not occupying, f"{difficulty.label}: {len(occupying)} notes in a dodge lane"

    def test_crouch_walls_leave_the_top_row_empty(self, maps):
        for difficulty, result in maps.items():
            for obstacle in result.beatmap.obstacles:
                if obstacle.kind is not ObstacleKind.CROUCH:
                    continue
                high = [
                    note
                    for note in result.beatmap.notes
                    if obstacle.beat <= note.beat <= obstacle.end_beat
                    and note.y >= obstacle.y
                ]
                assert not high, f"{difficulty.label}: {len(high)} notes under a crouch wall"

    def test_walls_do_not_overlap_each_other(self, maps):
        for difficulty, result in maps.items():
            obstacles = sorted(result.beatmap.obstacles, key=lambda item: item.beat)
            for earlier, later in zip(obstacles, obstacles[1:]):
                assert later.beat >= earlier.end_beat, difficulty.label

    def test_wall_geometry_stays_on_the_grid(self, maps):
        for difficulty, result in maps.items():
            for obstacle in result.beatmap.obstacles:
                assert obstacle.duration > 0, difficulty.label
                assert 0 <= obstacle.x < GRID_COLUMNS
                assert obstacle.x + obstacle.width <= GRID_COLUMNS
                assert obstacle.y >= 0 and obstacle.width > 0 and obstacle.height > 0

    def test_wall_frequency_rises_with_difficulty(self, maps):
        easy = maps[Difficulty.EASY].beatmap.statistics.total_walls
        expert_plus = maps[Difficulty.EXPERT_PLUS].beatmap.statistics.total_walls
        assert easy <= expert_plus

    def test_higher_difficulties_actually_get_walls(self, maps):
        """Guards the regression this change fixed: when walls were filtered
        after the notes, dense maps ended up with almost none."""
        assert maps[Difficulty.EXPERT].beatmap.statistics.total_walls >= 2

    def test_dodge_walls_push_notes_to_the_far_side(self, maps):
        """A dodge wall should read as choreography, not just an absence."""
        result = maps[Difficulty.EXPERT]
        notes = result.beatmap.notes
        song_mean = sum(note.x for note in notes) / len(notes)

        checked = 0
        for obstacle in result.beatmap.obstacles:
            if obstacle.kind is ObstacleKind.CROUCH:
                continue
            under = [
                note for note in notes if obstacle.beat <= note.beat <= obstacle.end_beat
            ]
            if len(under) < 3:
                continue
            local_mean = sum(note.x for note in under) / len(under)
            if obstacle.kind is ObstacleKind.DODGE_LEFT:
                assert local_mean > song_mean, "dodge-left wall did not push right"
            else:
                assert local_mean < song_mean, "dodge-right wall did not push left"
            checked += 1
        assert checked > 0, "no dodge wall had enough notes under it to judge"


class TestArmMovement:
    """Does the output move the way an arm can move?"""

    @staticmethod
    def _same_hand_pairs(result):
        by_hand: dict[int, list] = {}
        for note in result.beatmap.notes:
            by_hand.setdefault(note.hand.value, []).append(note)
        for notes in by_hand.values():
            notes.sort(key=lambda note: note.beat)
            yield from zip(notes, notes[1:])

    def test_no_hand_repeats_a_direction_without_time_to_recover(self, maps):
        """The complaint that started this: cutting RIGHT, then RIGHT again.

        You must bring the saber back across before you can swing across
        again, so a repeat always hides a return stroke. With a full beat of
        space that is a deliberate reset and real maps use them; rushed, it is
        simply unplayable. The bug was neither — it was `HorizontalFlow`
        asking one hand for RIGHT over and over as its steady state.
        """
        for difficulty, result in maps.items():
            # Each profile states how much recovery it considers enough, and
            # the higher difficulties deliberately allow tighter resets. Hold
            # the map to its own promise rather than to a flat number.
            allowed = PROFILES[difficulty].reset_min_gap_beats
            rushed = [
                (a, b)
                for a, b in self._same_hand_pairs(result)
                if a.direction is b.direction
                and a.direction is not CutDirection.ANY
                and b.beat - a.beat < allowed
            ]
            assert not rushed, (
                f"{difficulty.label}: {len(rushed)} same-hand direction repeats "
                f"inside its own {allowed} beat reset window, first at beat "
                f"{rushed[0][0].beat:.2f}"
            )

    def test_direction_repeats_are_rare_even_when_spaced(self, maps):
        for difficulty, result in maps.items():
            pairs = list(self._same_hand_pairs(result))
            repeats = [
                (a, b)
                for a, b in pairs
                if a.direction is b.direction and a.direction is not CutDirection.ANY
            ]
            share = len(repeats) / max(len(pairs), 1)
            assert share < 0.02, f"{difficulty.label}: {share:.1%} of pairs repeat"

    def test_consecutive_swings_oppose_each_other(self, maps):
        from app.services.mapping.parity_engine import opposes

        for difficulty, result in maps.items():
            by_hand: dict[int, list] = {}
            for note in result.beatmap.notes:
                by_hand.setdefault(note.hand.value, []).append(note)
            unopposed = 0
            total = 0
            for notes in by_hand.values():
                notes.sort(key=lambda note: note.beat)
                for a, b in zip(notes, notes[1:]):
                    total += 1
                    if b.beat - a.beat < 1.5 and not opposes(a.direction, b.direction):
                        unopposed += 1
            assert unopposed / max(total, 1) < 0.03, difficulty.label


class TestDynamics:
    """Does density follow the song, or run flat underneath it?"""

    def test_quiet_sections_get_measurably_fewer_notes(self, maps, analysis):
        grid = analysis.grid
        for difficulty, result in maps.items():
            notes = result.beatmap.notes
            if len(notes) < 100:
                continue
            rates = {}
            for section in analysis.sections:
                if section.duration < 5.0:
                    continue
                count = sum(
                    1
                    for note in notes
                    if section.start <= grid.beat_to_time(note.beat) < section.end
                )
                rates[section.index] = count / section.duration

            loud = max(analysis.sections, key=lambda s: s.loudness)
            quiet = min(
                (s for s in analysis.sections if s.duration >= 5.0),
                key=lambda s: s.loudness,
            )
            if loud.index not in rates or quiet.index not in rates:
                continue
            assert rates[loud.index] > rates[quiet.index] * 1.6, (
                f"{difficulty.label}: loudest section {rates[loud.index]:.2f} NPS vs "
                f"quietest {rates[quiet.index]:.2f} NPS — density is not following "
                f"the music"
            )

    def test_section_loudness_records_real_ratios(self, analysis):
        """`intensity` is a rank and evenly spaced; `loudness` must not be."""
        loudness = sorted(section.loudness for section in analysis.sections)
        assert loudness[-1] == pytest.approx(1.0, abs=1e-3)
        assert loudness[0] < 0.8, "no section is quieter than the peak?"
        gaps = [b - a for a, b in zip(loudness, loudness[1:])]
        assert max(gaps) - min(gaps) > 0.02, "loudness looks rank-normalised"


class TestLeadIn:
    """Players need a moment before the first note arrives.

    Across a corpus of community-made levels the median gap before the first
    note is 4.3 seconds — long enough to see the track start moving, settle
    your grip and find the tempo.
    """

    def test_the_map_leaves_room_at_the_start(self, analysis, make_map):
        from app.services.mapping.config import MappingConfig

        expected = MappingConfig().lead_in_seconds
        for difficulty in Difficulty:
            result = make_map(difficulty=difficulty)
            first = min(
                analysis.grid.beat_to_time(note.beat) for note in result.beatmap.notes
            )
            assert first >= expected - 0.75, (
                f"{difficulty.label}: first note at {first:.2f}s, wanted "
                f"about {expected:.1f}s of run-in"
            )

    def test_the_lead_in_can_be_turned_off(self, analysis, make_map):
        without = make_map(lead_in_seconds=0.0)
        first = min(
            analysis.grid.beat_to_time(note.beat) for note in without.beatmap.notes
        )
        assert first < 4.0

    def test_a_lead_in_never_empties_the_map(self, analysis, make_map):
        """A short clip must not be erased by its own run-in."""
        result = make_map(lead_in_seconds=1000.0)
        assert result.beatmap.statistics.total_notes > 50
