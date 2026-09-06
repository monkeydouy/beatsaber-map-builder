"""Difficulty-aware rhythm selection.

Given the one musical timeline produced by analysis, decide *which* moments
this particular difficulty should represent. This is where Easy and Expert+
genuinely diverge: they are not the same map thinned or padded, they are two
different readings of the same music.

Selection is importance-driven, not mathematical downsampling. Each event gets
a musical importance score (what kind of event it is, how strong it is, how
prominent its grid position is), and a per-section budget derived from the
difficulty's target density and that section's energy decides how many of them
survive.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from random import Random

from app.models.enums import MusicalEventType
from app.models.musical import BeatGrid, MusicalEvent, Section
from app.services.mapping.difficulty_profiles import DifficultyProfile

logger = logging.getLogger(__name__)

#: Base importance by event type, before strength weighting.
TYPE_IMPORTANCE: dict[MusicalEventType, float] = {
    MusicalEventType.DOWNBEAT: 1.00,
    MusicalEventType.ACCENT: 0.92,
    MusicalEventType.TRANSITION: 0.88,
    MusicalEventType.KICK: 0.80,
    MusicalEventType.SNARE: 0.78,
    MusicalEventType.MELODY: 0.55,
    MusicalEventType.PERCUSSION: 0.45,
    MusicalEventType.GENERIC_ONSET: 0.42,
    MusicalEventType.BEAT: 0.38,
}

#: Coarse grid positions read as more important than fine ones.
DIVISION_IMPORTANCE: dict[int, float] = {1: 1.0, 2: 0.72, 3: 0.52, 4: 0.50, 6: 0.34, 8: 0.30}

#: How strongly a section's energy modulates its note budget. The range is
#: wide on purpose: a quiet passage mapped at 40% of average still feels busy,
#: and the difference between an intro and a drop is what makes a map feel
#: like it is following the song rather than running underneath it.
SECTION_DENSITY_FLOOR = 0.18
SECTION_DENSITY_CEILING = 1.70

#: Exponent applied to a section's relative loudness. Above 1 it exaggerates
#: the gap between quiet and loud, which is what human mappers do.
SECTION_DYNAMICS_GAMMA = 1.5

#: Roughly the share of slots that are accent-like enough to carry a double.
#: Used only to size the slot budget; the actual choice is made per slot.
ELIGIBLE_DOUBLE_SHARE = 0.22

#: How a profile's duple subdivision ladder reads when the beat divides in
#: three. A profile says how *finely* a difficulty may subdivide the beat, not
#: which arithmetic the music uses — so "may use half-beats" becomes "may use
#: third-beats" in a shuffle, rather than the difficulty losing its offbeats
#: entirely because thirds are not on its list.
TRIPLE_SUBDIVISION_LADDER: dict[int, int] = {1: 1, 2: 3, 3: 3, 4: 6, 6: 6, 8: 12}


def effective_subdivisions(
    allowed: tuple[int, ...], triple_subdivision: bool
) -> tuple[int, ...]:
    """Translate a difficulty's subdivision ladder into the song's own terms."""
    if not triple_subdivision:
        return allowed
    mapped = {TRIPLE_SUBDIVISION_LADDER.get(division, division) for division in allowed}
    return tuple(sorted(mapped))


@dataclass(slots=True)
class RhythmSlot:
    """One moment the map will place at least one note on."""

    beat: float
    time: float
    event: MusicalEvent
    importance: float
    section_index: int
    #: Set by the selector when this slot should carry a double.
    wants_double: bool = False
    #: True when the slot sits inside an engineered recovery window.
    in_recovery: bool = False


def _snap_to_allowed(
    event: MusicalEvent, allowed: tuple[int, ...], tolerance: float
) -> tuple[float, int] | None:
    """Re-snap an analysis event onto this difficulty's coarser grid.

    Returns ``None`` when the event cannot be represented — a 1/4-beat hi-hat
    has no honest place in an Easy map, and forcing it onto a beat would put a
    note where the music has nothing.
    """
    best: tuple[float, int, float] | None = None
    for division in allowed:
        snapped = round(event.quantized_beat * division) / division
        error = abs(snapped - event.quantized_beat)
        if best is None or error < best[2]:
            best = (snapped, division, error)
    if best is None or best[2] > tolerance:
        return None
    return best[0], best[1]


def _importance(event: MusicalEvent, division: int, section: Section | None) -> float:
    base = TYPE_IMPORTANCE.get(event.event_type, 0.4)
    grid_weight = DIVISION_IMPORTANCE.get(division, 0.3)
    strength = 0.25 + 0.75 * event.strength
    score = base * 0.45 + grid_weight * 0.30 + strength * 0.35
    if event.is_downbeat:
        score += 0.16
    if section is not None:
        # Loud sections deserve their fine detail; quiet ones do not.
        score += 0.10 * (section.intensity - 0.5)
    return score * (0.6 + 0.4 * event.confidence)


class RhythmSelector:
    """Chooses the rhythmic skeleton of the map for one difficulty."""

    def __init__(
        self,
        profile: DifficultyProfile,
        grid: BeatGrid,
        rng: Random,
        *,
        intensity: float,
    ) -> None:
        self.profile = profile
        self.grid = grid
        self.rng = rng
        self.intensity = min(max(intensity, 0.0), 1.0)

    def select(
        self,
        events: list[MusicalEvent],
        sections: list[Section],
        duration: float,
    ) -> list[RhythmSlot]:
        """Return the ordered rhythm slots for this difficulty."""
        if not events:
            return []

        allowed = effective_subdivisions(
            self.profile.allowed_subdivisions, self.grid.triple_subdivision
        )
        finest = max(allowed)
        tolerance = 0.5 / finest * 0.85

        by_section: dict[int, list[RhythmSlot]] = {}
        section_lookup = {section.index: section for section in sections}

        for event in events:
            snapped = _snap_to_allowed(event, allowed, tolerance)
            if snapped is None:
                continue
            beat, division = snapped
            section = section_lookup.get(event.section_index)
            by_section.setdefault(event.section_index, []).append(
                RhythmSlot(
                    beat=beat,
                    time=self.grid.beat_to_time(beat),
                    event=event,
                    importance=_importance(event, division, section),
                    section_index=event.section_index,
                )
            )

        target_nps = self.profile.interpolated_nps(self.intensity)
        # Doubles add notes without adding slots, so the slot budget has to be
        # discounted by the share of slots that will carry two notes. Only
        # accents are eligible for a double and only some of those take one, so
        # the realised rate is a fraction of the profile's probability — using
        # the raw probability here leaves every map short of its NPS target.
        double_rate = self.profile.double_probability * ELIGIBLE_DOUBLE_SHARE
        slot_rate = target_nps / (1.0 + double_rate)

        factors = self._normalized_density_factors(sections)

        selected: list[RhythmSlot] = []
        for section in sections:
            candidates = by_section.get(section.index, [])
            if not candidates:
                continue
            selected.extend(
                self._select_in_section(
                    candidates, section, slot_rate, factors.get(section.index, 1.0)
                )
            )

        selected.sort(key=lambda slot: slot.beat)
        selected = self._enforce_spacing(selected)
        selected = self._apply_recovery(selected, duration)
        self._assign_doubles(selected, section_lookup)

        logger.info(
            "rhythm_selected",
            extra={
                "difficulty": self.profile.difficulty.value,
                "slots": len(selected),
                "target_nps": round(target_nps, 3),
                "effective_slot_nps": round(len(selected) / max(duration, 1e-6), 3),
            },
        )
        return selected

    # -- internals --------------------------------------------------------

    def _section_density_factor(self, section: Section) -> float:
        """Scale a section's budget by how loud it actually is.

        Uses `loudness` (the real ratio against the song's peak) rather than
        `intensity` (a rank). Ranks are evenly spaced whatever the music does,
        so a song whose intro is a fifth as loud as its drop and one whose
        intro is nearly as loud produced identical density curves — which is
        why quiet passages came out almost as busy as the drops.
        """
        factor = max(section.loudness, 0.0) ** SECTION_DYNAMICS_GAMMA
        return min(max(factor, SECTION_DENSITY_FLOOR), SECTION_DENSITY_CEILING)

    def _normalized_density_factors(self, sections: list[Section]) -> dict[int, float]:
        """Per-section density factors, renormalised to preserve the target.

        Section factors express *relative* dynamics — a break should be
        quieter than a drop. But a song that is mostly quiet would otherwise
        land well under the difficulty's target NPS, because every section
        individually scaled itself down. Dividing by the duration-weighted mean
        keeps the dynamics while making the song-wide average match the target.
        """
        raw = {section.index: self._section_density_factor(section) for section in sections}
        total_duration = sum(max(section.duration, 0.0) for section in sections)
        if total_duration <= 0:
            return raw
        mean = (
            sum(raw[section.index] * max(section.duration, 0.0) for section in sections)
            / total_duration
        )
        if mean <= 1e-6:
            return raw
        return {index: factor / mean for index, factor in raw.items()}

    def _select_in_section(
        self,
        candidates: list[RhythmSlot],
        section: Section,
        slot_rate: float,
        density_factor: float,
    ) -> list[RhythmSlot]:
        """Greedily take the most musically important slots within budget."""
        duration = max(section.duration, 1e-6)
        budget = int(round(slot_rate * density_factor * duration))
        budget = max(budget, 1 if duration > 2.0 else 0)
        if budget <= 0:
            return []
        if len(candidates) <= budget:
            chosen = list(candidates)
        else:
            min_gap_beats = self.profile.min_note_interval / self.grid.seconds_per_beat
            ranked = sorted(candidates, key=lambda slot: slot.importance, reverse=True)
            chosen = []
            taken_beats: list[float] = []
            for slot in ranked:
                if len(chosen) >= budget:
                    break
                if any(abs(slot.beat - beat) < min_gap_beats - 1e-9 for beat in taken_beats):
                    continue
                chosen.append(slot)
                taken_beats.append(slot.beat)

        chosen.sort(key=lambda slot: slot.beat)
        # Judge "is this a hole?" against *this section's* rate, not the
        # song's. Measured against the global average, every gap in a quiet
        # passage looks like a hole, and filling them all back in undoes the
        # dynamics the budget just established.
        return self._fill_rhythmic_gaps(
            chosen, candidates, section, slot_rate * density_factor
        )

    def _fill_rhythmic_gaps(
        self,
        chosen: list[RhythmSlot],
        candidates: list[RhythmSlot],
        section: Section,
        slot_rate: float,
    ) -> list[RhythmSlot]:
        """Patch holes so a section does not stall on a strong beat.

        Pure importance ranking can leave a two-bar silence in the middle of an
        active passage. Where a gap is much longer than the section's own
        average spacing, pull back the best available candidate inside it.

        `slot_rate` here is the rate for *this section*, already scaled by its
        loudness — a quiet passage is supposed to be sparse, and its long gaps
        are the point rather than a defect to repair.
        """
        if len(chosen) < 2:
            return chosen
        expected_gap = 1.0 / max(slot_rate, 0.05) / self.grid.seconds_per_beat
        max_gap = expected_gap * 2.6
        by_beat = {slot.beat for slot in chosen}
        additions: list[RhythmSlot] = []
        min_gap_beats = self.profile.min_note_interval / self.grid.seconds_per_beat

        for previous, following in zip(chosen, chosen[1:]):
            gap = following.beat - previous.beat
            if gap <= max_gap:
                continue
            window = [
                slot
                for slot in candidates
                if previous.beat + min_gap_beats <= slot.beat <= following.beat - min_gap_beats
                and slot.beat not in by_beat
            ]
            if not window:
                continue
            # Prefer something near the middle of the hole, strongest first.
            midpoint = (previous.beat + following.beat) / 2.0
            window.sort(key=lambda slot: (-slot.importance, abs(slot.beat - midpoint)))
            best = window[0]
            additions.append(best)
            by_beat.add(best.beat)

        if additions:
            chosen = sorted(chosen + additions, key=lambda slot: slot.beat)
        return chosen

    def _enforce_spacing(self, slots: list[RhythmSlot]) -> list[RhythmSlot]:
        """Drop slots that violate the difficulty's minimum note interval."""
        if not slots:
            return slots
        min_gap = self.profile.min_note_interval
        kept = [slots[0]]
        for slot in slots[1:]:
            if slot.time - kept[-1].time < min_gap - 1e-9:
                if slot.importance > kept[-1].importance * 1.15:
                    kept[-1] = slot
                continue
            kept.append(slot)
        return kept

    def _apply_recovery(self, slots: list[RhythmSlot], duration: float) -> list[RhythmSlot]:
        """Thin out sustained dense passages so the player can breathe.

        A run of notes above the profile's sustained ceiling for longer than
        `max_sustained_seconds` gets a window where only the strongest events
        survive. Recovery means lower density, not silence.
        """
        if len(slots) < 8:
            return slots

        profile = self.profile
        window = profile.max_sustained_seconds
        recovery = profile.recovery_seconds
        if window <= 0 or recovery <= 0:
            return slots

        result: list[RhythmSlot] = []
        run_start = slots[0].time
        index = 0
        while index < len(slots):
            slot = slots[index]
            if slot.time - run_start > window:
                # Measure density over the run just completed.
                run = [item for item in slots if run_start <= item.time <= slot.time]
                density = len(run) / max(slot.time - run_start, 1e-6)
                if density >= profile.sustained_nps * 0.72:
                    end = slot.time + recovery
                    keep_threshold = 0.72
                    while index < len(slots) and slots[index].time < end:
                        candidate = slots[index]
                        if candidate.importance >= keep_threshold or candidate.event.is_downbeat:
                            candidate.in_recovery = True
                            result.append(candidate)
                        index += 1
                    run_start = end
                    continue
                run_start = slot.time
            result.append(slot)
            index += 1
        return result

    def _assign_doubles(
        self, slots: list[RhythmSlot], sections: dict[int, Section]
    ) -> None:
        """Mark the accents that deserve both hands at once.

        Doubles are earned by the music (accents, downbeats, section openings),
        never sprinkled — "every strong onset becomes a double" is one of the
        fastest ways to make a map feel machine-generated.
        """
        probability = self.profile.double_probability
        if probability <= 0 or len(slots) < 3:
            return
        last_double_time = -99.0
        # Doubles need clear space either side; scale the guard with density.
        min_spacing = max(1.2, 2.0 / max(self.profile.target_nps_max, 1.0))

        # Judge "isolated enough for a double" against the map's own rhythm.
        # An absolute gap threshold rejects every candidate in a dense map,
        # which is how a generator ends up emitting no doubles at all.
        gaps = sorted(later.time - earlier.time for earlier, later in zip(slots, slots[1:]))
        median_gap = gaps[len(gaps) // 2] if gaps else self.profile.min_note_interval
        required_gap = max(median_gap * 0.85, self.profile.min_note_interval * 0.95)

        for index, slot in enumerate(slots):
            if slot.in_recovery:
                continue
            event = slot.event
            if event.event_type not in {
                MusicalEventType.ACCENT,
                MusicalEventType.DOWNBEAT,
                MusicalEventType.TRANSITION,
            }:
                continue
            if slot.time - last_double_time < min_spacing:
                continue
            # Require breathing room on both sides so the double reads clearly.
            before = slots[index - 1].time if index > 0 else slot.time - 10.0
            after = slots[index + 1].time if index + 1 < len(slots) else slot.time + 10.0
            gap = min(slot.time - before, after - slot.time)
            if gap < required_gap:
                continue

            section = sections.get(slot.section_index)
            weight = 0.6 + 0.8 * (section.intensity if section else 0.5)
            if self.rng.random() < probability * weight * 2.2:
                slot.wants_double = True
                last_double_time = slot.time
