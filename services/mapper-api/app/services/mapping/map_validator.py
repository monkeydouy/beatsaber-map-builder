"""Playability and structural validation.

Runs against the generated beatmap before anything is packaged. Errors block
the download; warnings are reported to the user but do not stop it. The parity
check here deliberately re-derives its own state rather than trusting what the
generator recorded — an independent second opinion is the point.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.models.beatmap import GRID_COLUMNS, GRID_ROWS, GeneratedBeatmap
from app.models.enums import CutDirection
from app.services.mapping.difficulty_profiles import DifficultyProfile
from app.services.mapping.parity_engine import validate_sequence

logger = logging.getLogger(__name__)

#: Two notes for the same hand closer than this (beats) are unplayable.
MIN_SAME_HAND_GAP = 0.1

#: Tolerated overshoot above the profile's ceilings before it becomes an error.
NPS_ERROR_MARGIN = 1.25
NPS_WARNING_MARGIN = 1.02


@dataclass(slots=True)
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "checks": self.checks,
        }


def validate_beatmap(
    beatmap: GeneratedBeatmap,
    profile: DifficultyProfile,
    *,
    song_path: Path | None = None,
    cover_path: Path | None = None,
) -> ValidationReport:
    """Check a generated map for structural and playability problems."""
    report = ValidationReport()
    notes = sorted(beatmap.notes, key=lambda note: note.beat)

    _check_presence(report, notes, song_path, cover_path)
    _check_grid(report, notes)
    _check_ordering(report, notes)
    _check_duplicates(report, notes)
    _check_obstacles(report, beatmap)
    _check_density(report, beatmap, profile)
    _check_parity(report, notes, profile)

    logger.info(
        "map_validated",
        extra={
            "difficulty": profile.difficulty.value,
            "errors": len(report.errors),
            "warnings": len(report.warnings),
        },
    )
    return report


# ---------------------------------------------------------------------------


def _check_presence(report, notes, song_path, cover_path) -> None:
    """Check what is actually checkable.

    Asset paths are optional: the mapping engine can be validated on its own,
    before anything is written to disk. When a path is not supplied the check
    is *omitted* rather than recorded as failed — reporting a red cross for a
    test that never ran tells the user their map is broken when it is fine.
    """
    report.checks["notes_present"] = bool(notes)
    if not notes:
        report.errors.append("The generated map contains no notes.")

    for label, path, message in (
        ("audio_present", song_path, "The song audio file is missing."),
        ("cover_present", cover_path, "The cover image is missing."),
    ):
        if path is None:
            continue
        present = path.exists() and path.stat().st_size > 0
        report.checks[label] = present
        if not present:
            report.errors.append(message)


def _check_grid(report, notes) -> None:
    bad_positions = 0
    bad_directions = 0
    negative_beats = 0
    valid_directions = {member.value for member in CutDirection}

    for note in notes:
        if not (0 <= note.x < GRID_COLUMNS) or not (0 <= note.y < GRID_ROWS):
            bad_positions += 1
        if note.direction.value not in valid_directions:
            bad_directions += 1
        if note.beat < 0:
            negative_beats += 1

    report.checks["positions_valid"] = bad_positions == 0
    report.checks["directions_valid"] = bad_directions == 0
    report.checks["beats_non_negative"] = negative_beats == 0

    if bad_positions:
        report.errors.append(f"{bad_positions} notes fall outside the 4x3 grid.")
    if bad_directions:
        report.errors.append(f"{bad_directions} notes use an invalid cut direction.")
    if negative_beats:
        report.errors.append(f"{negative_beats} notes have a negative beat position.")


def _check_ordering(report, notes) -> None:
    ordered = all(
        earlier.beat <= later.beat for earlier, later in zip(notes, notes[1:])
    )
    report.checks["beats_sorted"] = ordered
    if not ordered:
        report.errors.append("Note beats are not in ascending order.")


def _check_duplicates(report, notes) -> None:
    seen: set[tuple[float, int, int]] = set()
    exact_duplicates = 0
    stacked = 0
    last_by_hand: dict[int, float] = {}
    too_close = 0

    for note in notes:
        key = (round(note.beat, 4), note.x, note.y)
        if key in seen:
            exact_duplicates += 1
        seen.add(key)

        previous = last_by_hand.get(note.hand.value)
        if previous is not None:
            gap = note.beat - previous
            if gap < 1e-4:
                stacked += 1
            elif gap < MIN_SAME_HAND_GAP:
                too_close += 1
        last_by_hand[note.hand.value] = note.beat

    report.checks["no_duplicate_notes"] = exact_duplicates == 0
    report.checks["no_simultaneous_same_hand"] = stacked == 0
    report.checks["same_hand_spacing_ok"] = too_close == 0

    if exact_duplicates:
        report.errors.append(f"{exact_duplicates} notes occupy an identical position and beat.")
    if stacked:
        report.errors.append(f"{stacked} pairs of same-hand notes land on the same beat.")
    if too_close:
        report.errors.append(
            f"{too_close} same-hand notes are too close together to be cut."
        )


def _check_obstacles(report, beatmap) -> None:
    invalid = 0
    conflicting = 0
    for obstacle in beatmap.obstacles:
        if (
            obstacle.duration <= 0
            or obstacle.width <= 0
            or obstacle.height <= 0
            or not (0 <= obstacle.x < GRID_COLUMNS)
            or obstacle.x + obstacle.width > GRID_COLUMNS
            or obstacle.y < 0
        ):
            invalid += 1
            continue
        for note in beatmap.notes:
            if not (obstacle.beat <= note.beat <= obstacle.end_beat):
                continue
            if obstacle.covers_column(note.x) and obstacle.y <= note.y < obstacle.y + obstacle.height:
                conflicting += 1
                break

    report.checks["obstacles_valid"] = invalid == 0
    report.checks["obstacles_do_not_block_notes"] = conflicting == 0
    if invalid:
        report.errors.append(f"{invalid} walls have invalid dimensions.")
    if conflicting:
        report.errors.append(f"{conflicting} walls overlap notes the player must hit.")


def _check_density(report, beatmap, profile) -> None:
    statistics = beatmap.statistics
    peak_limit = profile.peak_nps * NPS_ERROR_MARGIN
    report.checks["peak_nps_within_profile"] = statistics.peak_nps <= peak_limit
    if statistics.peak_nps > peak_limit:
        report.errors.append(
            f"Peak density of {statistics.peak_nps:.1f} NPS exceeds the "
            f"{profile.difficulty.label} ceiling."
        )
    elif statistics.peak_nps > profile.peak_nps * NPS_WARNING_MARGIN:
        report.warnings.append(
            f"Peak density of {statistics.peak_nps:.1f} NPS sits at the top of the "
            f"{profile.difficulty.label} range."
        )

    average = statistics.average_nps
    report.checks["average_nps_reasonable"] = average <= profile.target_nps_max * 1.6
    if average > profile.target_nps_max * 1.6:
        report.errors.append(
            f"Average density of {average:.1f} NPS is far above the "
            f"{profile.difficulty.label} range."
        )
    elif average < profile.target_nps_min * 0.45 and statistics.total_notes > 0:
        report.warnings.append(
            f"Average density of {average:.1f} NPS is sparse for "
            f"{profile.difficulty.label}; the song may be quiet or slow."
        )

    balance = (
        min(statistics.left_notes, statistics.right_notes)
        / max(statistics.left_notes, statistics.right_notes, 1)
    )
    report.checks["hands_balanced"] = balance >= 0.6
    if balance < 0.6 and statistics.total_notes > 20:
        report.warnings.append("One hand is used noticeably more than the other.")


def _check_parity(report, notes, profile) -> None:
    problems = validate_sequence(
        [(note.beat, note.hand, note.x, note.y, note.direction) for note in notes],
        reset_min_gap_beats=profile.reset_min_gap_beats,
    )
    report.checks["parity_ok"] = not problems
    if problems:
        # Parity issues degrade playability but do not corrupt the file, so a
        # small number is reported as a warning rather than blocking download.
        threshold = max(3, len(notes) // 100)
        message = f"{len(problems)} swing-parity issues detected."
        if len(problems) > threshold:
            report.errors.append(message + " The map may be uncomfortable to play.")
        else:
            report.warnings.append(message)
        for problem in problems[:5]:
            report.warnings.append(problem)
