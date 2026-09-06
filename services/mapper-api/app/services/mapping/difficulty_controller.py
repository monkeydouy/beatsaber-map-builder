"""Enforce the difficulty profile on a finished note list.

Pattern generation optimises for local flow and can, over a dense passage,
drift above the difficulty's ceiling. This stage is the safety net: it measures
rolling density, thins windows that exceed the profile, and guarantees the
recovery the profile promises. It only ever *removes* notes — adding notes here
would place them without musical justification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.models.beatmap import BeatNote, MapStatistics
from app.models.enums import DIFFICULTY_ORDER, Difficulty
from app.models.musical import BeatGrid
from app.services.mapping.difficulty_profiles import DifficultyProfile

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DensityReport:
    average_nps: float
    peak_nps: float
    max_local_nps: float
    window_peaks: dict[float, float]
    mapped_duration: float


def _rolling_peak(times: list[float], window: float) -> float:
    """Highest note rate over any `window`-second span.

    The rate for n notes spanning a window is ``(n - 1) / window``, not
    ``n / window``. The naive form double-counts the endpoints: a plain
    quarter-note stream at 128 BPM puts three notes inside any one-second
    window and would be reported as 3.0 NPS when the player is actually
    hitting 2.13 — enough to make the controller strip notes out of a
    perfectly reasonable Easy map.
    """
    if len(times) < 2 or window <= 0:
        return 0.0
    peak = 0.0
    start = 0
    for end in range(len(times)):
        while times[end] - times[start] > window:
            start += 1
        intervals = end - start
        peak = max(peak, intervals / window)
    return peak


def measure_density(
    notes: list[BeatNote], grid: BeatGrid, windows: tuple[float, ...]
) -> DensityReport:
    """Compute average and rolling-window note density."""
    if not notes:
        return DensityReport(0.0, 0.0, 0.0, {window: 0.0 for window in windows}, 0.0)

    times = sorted(grid.beat_to_time(note.beat) for note in notes)
    mapped = max(times[-1] - times[0], 1e-6)
    average = len(times) / mapped
    peaks = {window: _rolling_peak(times, window) for window in windows}
    shortest = min(windows) if windows else 1.0
    return DensityReport(
        average_nps=average,
        peak_nps=peaks.get(shortest, 0.0),
        max_local_nps=max(peaks.values()) if peaks else 0.0,
        window_peaks=peaks,
        mapped_duration=mapped,
    )


class DifficultyController:
    """Clamps a generated note list into its difficulty's envelope."""

    def __init__(
        self,
        profile: DifficultyProfile,
        grid: BeatGrid,
        *,
        windows: tuple[float, ...] = (1.0, 2.0, 4.0),
        intensity: float = 0.5,
    ) -> None:
        self.profile = profile
        self.grid = grid
        self.windows = windows
        self.intensity = min(max(intensity, 0.0), 1.0)

    def apply(self, notes: list[BeatNote]) -> tuple[list[BeatNote], DensityReport]:
        """Thin the note list until it respects the profile's ceilings."""
        if not notes:
            return notes, measure_density(notes, self.grid, self.windows)

        working = sorted(notes, key=lambda note: (note.beat, note.hand.value))
        working = self._enforce_window(working, window=1.0, ceiling=self.profile.peak_nps)
        working = self._enforce_window(
            working, window=4.0, ceiling=self.profile.sustained_nps
        )
        report = measure_density(working, self.grid, self.windows)

        removed = len(notes) - len(working)
        if removed:
            logger.info(
                "difficulty_clamped",
                extra={
                    "difficulty": self.profile.difficulty.value,
                    "removed_notes": removed,
                    "average_nps": round(report.average_nps, 3),
                    "peak_nps": round(report.peak_nps, 3),
                },
            )
        return working, report

    # -- internals --------------------------------------------------------

    def _enforce_window(
        self, notes: list[BeatNote], *, window: float, ceiling: float
    ) -> list[BeatNote]:
        """Remove the least important notes from over-dense windows.

        Doubles are dropped to singles before singles are dropped entirely, and
        notes on downbeats survive longest — thinning should cost the map its
        decoration, not its structure.
        """
        if ceiling <= 0:
            return notes
        # `_rolling_peak` measures intervals, so a window may hold one more
        # note than its rate implies.
        allowed = int(ceiling * window) + 1
        if allowed < 2:
            return notes

        times = [self.grid.beat_to_time(note.beat) for note in notes]
        survivors = [True] * len(notes)

        start = 0
        for end in range(len(notes)):
            while times[end] - times[start] > window:
                start += 1
            active = [index for index in range(start, end + 1) if survivors[index]]
            if len(active) <= allowed:
                continue
            excess = len(active) - allowed
            ranked = sorted(active, key=lambda index: self._importance(notes, index))
            for index in ranked[:excess]:
                survivors[index] = False

        return [note for note, keep in zip(notes, survivors) if keep]

    def _importance(self, notes: list[BeatNote], index: int) -> float:
        """Lower value means "drop me first"."""
        note = notes[index]
        score = note.confidence
        # A note sharing its beat with another is half of a double; doubles are
        # the cheapest thing to give up when a window is too dense.
        neighbours = 0
        for offset in (-1, 1):
            other = index + offset
            if 0 <= other < len(notes) and abs(notes[other].beat - note.beat) < 1e-3:
                neighbours += 1
        if neighbours:
            score -= 0.25
        # On-beat notes carry the pulse; keep them.
        fractional = abs(note.beat - round(note.beat))
        score += 0.5 * (1.0 - min(fractional * 4.0, 1.0))
        return score


def _class_boundaries() -> list[tuple[Difficulty, float]]:
    """Density boundaries between difficulty classes, taken from the profiles.

    Deriving these instead of hardcoding them keeps the estimate honest: if a
    profile's NPS range is retuned, the label moves with it. Hardcoded
    thresholds drift out of sync and start reporting Expert maps as Hard.
    """
    from app.services.mapping.difficulty_profiles import PROFILES

    boundaries: list[tuple[Difficulty, float]] = []
    ordered = list(DIFFICULTY_ORDER)
    for current, following in zip(ordered, ordered[1:]):
        lower, upper = PROFILES[current], PROFILES[following]
        # Midpoint of the overlap between the two ranges.
        boundary = (lower.target_nps_max + upper.target_nps_min) / 2.0
        boundaries.append((current, boundary))
    return boundaries


def estimate_difficulty(report: DensityReport, notes: list[BeatNote]) -> str:
    """Name the difficulty class the finished map actually lands in."""
    del notes  # density alone decides the class today
    for difficulty, ceiling in _class_boundaries():
        if report.average_nps < ceiling:
            return difficulty.label
    return Difficulty.EXPERT_PLUS.label


def build_statistics(
    notes: list[BeatNote],
    report: DensityReport,
    *,
    song_duration: float,
    walls: int,
    bombs: int,
    lights: int,
    resets: int,
    crossovers: int,
) -> MapStatistics:
    """Assemble the statistics shown to the user and used by the validator."""
    beats: dict[float, int] = {}
    for note in notes:
        key = round(note.beat, 4)
        beats[key] = beats.get(key, 0) + 1
    doubles = sum(1 for count in beats.values() if count >= 2)

    return MapStatistics(
        total_notes=len(notes),
        left_notes=sum(1 for note in notes if note.hand.value == 0),
        right_notes=sum(1 for note in notes if note.hand.value == 1),
        total_walls=walls,
        total_bombs=bombs,
        total_lights=lights,
        average_nps=report.average_nps,
        song_nps=len(notes) / song_duration if song_duration > 0 else 0.0,
        peak_nps=report.peak_nps,
        max_local_nps=report.max_local_nps,
        mapped_duration=report.mapped_duration,
        doubles=doubles,
        crossovers=crossovers,
        resets=resets,
        estimated_difficulty=estimate_difficulty(report, notes),
    )
