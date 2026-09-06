"""Mapping pipeline orchestration.

Turns one analysis result plus one set of generation options into a validated
beatmap. Everything here is deterministic given the seed: each stage draws from
its own derived RNG stream, so adding a stage later cannot change the output of
the stages before it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from random import Random

from app.models.beatmap import GeneratedBeatmap
from app.models.enums import Difficulty, MappingStyle
from app.models.musical import AnalysisResult
from app.services.mapping.config import MappingConfig, weights_for_style
from app.services.mapping.difficulty_controller import (
    DifficultyController,
    build_statistics,
)
from app.services.mapping.difficulty_profiles import DifficultyProfile, get_profile
from app.services.mapping.lighting_generator import generate_lighting
from app.services.mapping.map_validator import ValidationReport, validate_beatmap
from app.services.mapping.obstacle_generator import WallPlan, plan_obstacles
from app.services.mapping.pattern_generator import PatternGenerator
from app.services.mapping.rhythm_selector import RhythmSelector

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, str], None]


@dataclass(slots=True)
class GenerationOptions:
    """Everything the user chose for one generation run."""

    difficulty: Difficulty = Difficulty.EXPERT
    style: MappingStyle = MappingStyle.BALANCED
    intensity: float = 0.6
    seed: int = 0
    enable_bombs: bool = False
    enable_walls: bool = True
    enable_lighting: bool = True
    #: User-supplied tempo, replacing whatever detection concluded.
    bpm_override: float | None = None
    #: Override the default run-in silence before the first note.
    lead_in_seconds: float | None = None


@dataclass(slots=True)
class MappingResult:
    beatmap: GeneratedBeatmap
    profile: DifficultyProfile
    validation: ValidationReport


def _derive(seed: int, stream: str) -> Random:
    """A private RNG per stage, so stage order cannot perturb determinism."""
    return Random(f"{seed}:{stream}")


#: Beat Saber halves the default half-jump duration until the jump distance
#: fits inside this many metres. Mirrored here so our offset lands where we
#: think it does.
MAX_JUMP_DISTANCE = 17.999
DEFAULT_HALF_JUMP_BEATS = 4.0
MIN_HALF_JUMP_BEATS = 0.25


def default_half_jump_beats(njs: float, bpm: float) -> float:
    """Reproduce the game's own half-jump-duration calculation.

    Beat Saber starts at 4 beats and halves until the resulting jump distance
    fits within roughly 18 metres. Knowing the value the game will pick is the
    only way to choose a meaningful `_noteJumpStartBeatOffset`.
    """
    seconds_per_beat = 60.0 / bpm
    half_jump = DEFAULT_HALF_JUMP_BEATS
    while njs * seconds_per_beat * half_jump > MAX_JUMP_DISTANCE and half_jump > MIN_HALF_JUMP_BEATS:
        half_jump /= 2.0
    return max(half_jump, MIN_HALF_JUMP_BEATS)


def compute_note_jump_speed(
    profile: DifficultyProfile, bpm: float, average_nps: float, intensity: float
) -> tuple[float, float]:
    """Derive NJS and spawn offset from tempo, density and difficulty.

    A fixed NJS is wrong in both directions: at 90 BPM the default leaves notes
    hanging in the air, at 200 BPM it buries them in the wall. We scale toward
    a roughly constant reaction distance, then set the beat offset so the time
    a note is actually visible matches what the difficulty asks for.
    """
    reference_bpm = 130.0
    tempo_factor = (bpm / reference_bpm) ** 0.45
    density_factor = 1.0 + 0.06 * max(average_nps - profile.target_nps_min, 0.0)
    intensity_factor = 0.94 + 0.12 * min(max(intensity, 0.0), 1.0)

    njs = profile.base_njs * tempo_factor * density_factor * intensity_factor
    njs = round(min(max(njs, 8.0), 23.0), 2)

    seconds_per_beat = 60.0 / bpm
    base_half_jump = default_half_jump_beats(njs, bpm)
    wanted_half_jump = profile.reaction_time / seconds_per_beat

    offset = wanted_half_jump - base_half_jump
    # Keep the correction modest; a large offset makes a map unreadable in a
    # way players cannot compensate for.
    offset = max(min(offset, 1.0), -1.0)
    if base_half_jump + offset < 0.5:
        offset = 0.5 - base_half_jump

    return njs, round(offset, 2)


def generate_beatmap(
    analysis: AnalysisResult,
    options: GenerationOptions,
    *,
    config: MappingConfig | None = None,
    progress: ProgressCallback | None = None,
    debug_dir: Path | None = None,
    song_path: Path | None = None,
    cover_path: Path | None = None,
) -> MappingResult:
    """Run the full mapping pipeline for one difficulty.

    `song_path` and `cover_path` are optional so the engine can be exercised
    without touching the filesystem; when given, the validator also confirms
    the packaged assets are present.
    """
    config = config or MappingConfig()
    profile = get_profile(options.difficulty, options.intensity)
    weights = weights_for_style(config.weights, options.style)
    grid = analysis.grid

    def report(status: str, percent: int, step: str) -> None:
        if progress is not None:
            progress(status, percent, step)

    # -- rhythm ------------------------------------------------------------
    report("SELECTING_RHYTHM", 10, "Selecting rhythm")
    selector = RhythmSelector(
        profile, grid, _derive(options.seed, "rhythm"), intensity=options.intensity
    )
    slots = selector.select(analysis.events, analysis.sections, analysis.duration)

    lead_in = (
        options.lead_in_seconds
        if options.lead_in_seconds is not None
        else config.lead_in_seconds
    )
    if lead_in > 0 and slots:
        kept = [slot for slot in slots if slot.time >= lead_in]
        # Only enforce it if there is still a map left afterwards; a very short
        # clip should not be emptied out by its own run-in.
        if len(kept) >= max(8, len(slots) // 4):
            dropped = len(slots) - len(kept)
            slots = kept
            if dropped:
                logger.info(
                    "lead_in_applied",
                    extra={"seconds": lead_in, "slots_dropped": dropped},
                )
    _dump(debug_dir, "selected_rhythm.json", [
        {
            "beat": round(slot.beat, 4),
            "time": round(slot.time, 4),
            "importance": round(slot.importance, 4),
            "event_type": slot.event.event_type.value,
            "double": slot.wants_double,
            "recovery": slot.in_recovery,
        }
        for slot in slots
    ])

    # -- walls -------------------------------------------------------------
    # Planned before notes: the wall claims the space the music asked for, and
    # pattern generation routes around what is left. Filtering walls out after
    # the fact only ever yields the few that survived by luck.
    report("GENERATING_OBSTACLES", 22, "Planning obstacles")
    walls = (
        plan_obstacles(
            slots,
            analysis.sections,
            grid,
            profile,
            _derive(options.seed, "walls"),
            song_duration_beats=grid.time_to_beat(analysis.duration),
        )
        if options.enable_walls and config.enable_walls
        else WallPlan()
    )

    # -- patterns ----------------------------------------------------------
    report("GENERATING_PATTERNS", 35, "Generating patterns")
    generator = PatternGenerator(
        profile=profile,
        config=config,
        weights=weights,
        style=options.style,
        intensity=options.intensity,
        grid=grid,
        sections=analysis.sections,
        rng=_derive(options.seed, "patterns"),
        walls=walls,
    )
    notes = generator.generate(slots)
    _dump(debug_dir, "generated_patterns.json", [note.to_dict() for note in notes])
    _dump(
        debug_dir,
        "planned_walls.json",
        [
            {
                "beat": round(obstacle.beat, 4),
                "duration": obstacle.duration,
                "kind": obstacle.kind.value,
                "x": obstacle.x,
                "y": obstacle.y,
                "width": obstacle.width,
                "height": obstacle.height,
            }
            for obstacle in walls.obstacles
        ],
    )

    # -- parity + difficulty ------------------------------------------------
    report("VALIDATING_PARITY", 55, "Checking swing parity")
    report("VALIDATING_MAP", 62, "Checking difficulty")
    controller = DifficultyController(
        profile, grid, windows=config.nps_windows, intensity=options.intensity
    )
    notes, density = controller.apply(notes)
    _dump(
        debug_dir,
        "difficulty_analysis.json",
        {
            "average_nps": round(density.average_nps, 4),
            "peak_nps": round(density.peak_nps, 4),
            "max_local_nps": round(density.max_local_nps, 4),
            "window_peaks": {str(k): round(v, 4) for k, v in density.window_peaks.items()},
            "profile": {
                "difficulty": profile.difficulty.value,
                "target_nps": round(profile.interpolated_nps(options.intensity), 4),
                "peak_nps": profile.peak_nps,
                "sustained_nps": profile.sustained_nps,
            },
        },
    )

    # -- lighting -----------------------------------------------------------
    report("GENERATING_LIGHTING", 74, "Generating lighting")
    lights = (
        generate_lighting(
            analysis.events, analysis.sections, grid, _derive(options.seed, "lighting")
        )
        if options.enable_lighting and config.enable_lighting
        else []
    )

    obstacles = walls.as_list()
    statistics = build_statistics(
        notes,
        density,
        song_duration=analysis.duration,
        walls=len(obstacles),
        bombs=0,
        lights=len(lights),
        resets=generator.total_resets,
        crossovers=generator.total_crossovers,
    )

    njs, njs_offset = compute_note_jump_speed(
        profile, grid.bpm, statistics.average_nps, options.intensity
    )

    beatmap = GeneratedBeatmap(
        difficulty=options.difficulty,
        style=options.style,
        intensity=options.intensity,
        seed=options.seed,
        bpm=grid.bpm,
        duration=analysis.duration,
        notes=notes,
        obstacles=obstacles,
        bombs=[],
        lights=lights,
        note_jump_speed=njs,
        note_jump_offset=njs_offset,
        statistics=statistics,
    )
    beatmap.sort()

    # -- validation ---------------------------------------------------------
    report("VALIDATING_MAP", 82, "Validating Beat Saber map")
    validation = validate_beatmap(
        beatmap, profile, song_path=song_path, cover_path=cover_path
    )

    logger.info(
        "beatmap_generated",
        extra={
            "difficulty": options.difficulty.value,
            "style": options.style.value,
            "intensity": options.intensity,
            "seed": options.seed,
            "notes": statistics.total_notes,
            "average_nps": round(statistics.average_nps, 3),
            "peak_nps": round(statistics.peak_nps, 3),
            "valid": validation.ok,
        },
    )
    return MappingResult(beatmap=beatmap, profile=profile, validation=validation)


def _dump(directory: Path | None, filename: str, payload: object) -> None:
    """Write a debug artefact when DEBUG_MAPPING is enabled."""
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(json.dumps(payload, indent=2), encoding="utf-8")
