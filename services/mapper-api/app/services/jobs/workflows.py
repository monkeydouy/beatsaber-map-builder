"""The two long-running job workflows: analyse a song, and generate a map.

Splitting them is what makes "generate another difficulty" cheap — analysis
runs once per song and its result is cached on disk, while generation reruns
only the difficulty-dependent half of the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import partial
from pathlib import Path

from app.config import Settings
from app.models.enums import JobStatus
from app.models.musical import AnalysisResult
from app.services.analysis.audio_analyzer import analyze_audio
from app.services.analysis.event_detector import build_events
from app.services.analysis.requantize import requantize
from app.services.analysis.section_analyzer import analyze_sections
from app.services.export.beatsaber_exporter import MapMetadata
from app.services.export.cover_art import generate_cover
from app.services.export.package_builder import build_package
from app.services.jobs.store import JobStore
from app.services.mapping.difficulty_profiles import PROFILES
from app.services.mapping.pipeline import GenerationOptions, generate_beatmap
from app.services.media.base import AudioSource, MediaAcquisitionError
from app.services.media.normalizer import SONG_FILENAME, normalize_media

logger = logging.getLogger(__name__)

ANALYSIS_FILE = "analysis.json"
EVENTS_FILE = "musical_events.json"
RESULT_FILE = "result.json"
COVER_FILE = "cover.png"

#: How much of the analysis progress bar each stage owns.
ANALYSIS_STEPS: list[tuple[JobStatus, int, str]] = [
    (JobStatus.ACQUIRING_AUDIO, 8, "Fetching audio"),
    (JobStatus.NORMALIZING_AUDIO, 24, "Normalizing audio"),
    (JobStatus.ANALYZING_AUDIO, 30, "Analyzing audio"),
    (JobStatus.DETECTING_BEATS, 58, "Detecting BPM and beats"),
    (JobStatus.ANALYZING_SECTIONS, 82, "Finding musical sections"),
    (JobStatus.BUILDING_EVENT_TIMELINE, 92, "Building musical timeline"),
]


class WorkflowError(Exception):
    """A failure with a message that is safe to show the user."""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or message


async def run_analysis(
    *,
    job_id: str,
    source: AudioSource,
    store: JobStore,
    settings: Settings,
) -> None:
    """Acquire, normalise and analyse a song. Ends in the ANALYZED state."""
    job_dir = store.job_dir(job_id)
    started = time.perf_counter()

    async def step(index: int) -> None:
        status, progress, label = ANALYSIS_STEPS[index]
        await store.update(
            job_id, status=status, progress=progress, current_step=label, stage="analysis"
        )

    loop = asyncio.get_running_loop()

    def sync_step(status: str, percent: int, label: str) -> None:
        """Progress from inside the DSP worker thread."""
        asyncio.run_coroutine_threadsafe(
            store.update(
                job_id,
                status=JobStatus(status),
                progress=percent,
                current_step=label,
                stage="analysis",
            ),
            loop,
        )

    try:
        # -- acquire -------------------------------------------------------
        await step(0)
        media = await source.acquire()

        await store.update(
            job_id,
            metadata={
                "title": media.title,
                "artist": media.artist,
                "duration": round(media.duration, 2),
                "thumbnail": media.thumbnail,
                "original_url": media.original_url,
                "source_type": media.source_type.value,
            },
        )

        # -- normalise -----------------------------------------------------
        await step(1)
        normalization = await normalize_media(media.source_path, job_dir)

        # -- analyse -------------------------------------------------------
        await step(2)
        raw = await asyncio.to_thread(
            partial(
                analyze_audio,
                normalization.analysis_path,
                sample_rate=settings.analysis_sample_rate,
                hop_length=settings.analysis_hop_length,
                progress=sync_step,
                allow_variable_tempo=settings.enable_variable_tempo,
            )
        )

        await step(3)
        grid = raw.grid

        await step(4)
        sections = await asyncio.to_thread(analyze_sections, raw, grid)

        await step(5)
        events = await asyncio.to_thread(build_events, raw, grid, sections)

        analysis = AnalysisResult(
            duration=raw.duration,
            sample_rate=raw.sample_rate,
            grid=grid,
            beats=raw.beats.tolist(),
            beat_confidence=raw.beat_confidence.tolist(),
            downbeats=raw.downbeats.tolist(),
            onsets=raw.onsets.tolist(),
            onset_strengths=raw.onset_strengths.tolist(),
            sections=sections,
            events=events,
            features=raw.to_features(),
            tempo_candidates=[candidate.to_dict() for candidate in raw.tempo_candidates],
        )

        await asyncio.to_thread(_write_analysis, job_dir, analysis, settings.debug_mapping)

        # Generate the cover once, here, so every later generation reuses it.
        await asyncio.to_thread(
            generate_cover,
            job_dir / COVER_FILE,
            title=media.title,
            artist=media.artist,
            difficulty_label="Expert",
        )

        await store.update(
            job_id,
            status=JobStatus.ANALYZED,
            progress=100,
            current_step="Analysis complete",
            stage="analysis",
            analysis=summarize_analysis(analysis),
            error=None,
        )
        logger.info(
            "analysis_workflow_completed",
            extra={
                "job_id": job_id,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "bpm": analysis.bpm,
            },
        )

    except MediaAcquisitionError as exc:
        logger.warning("analysis_media_error", extra={"job_id": job_id, "detail": exc.detail})
        await store.fail(job_id, exc.message)
    except ValueError as exc:
        logger.warning("analysis_value_error", extra={"job_id": job_id}, exc_info=True)
        await store.fail(job_id, str(exc) or "Could not analyse that audio.")
    except Exception:
        logger.exception("analysis_failed", extra={"job_id": job_id})
        await store.fail(job_id, "Could not analyse that audio.")


def _write_analysis(job_dir: Path, analysis: AnalysisResult, debug: bool) -> None:
    payload = analysis.to_dict()
    (job_dir / ANALYSIS_FILE).write_text(json.dumps(payload), encoding="utf-8")
    if debug:
        (job_dir / EVENTS_FILE).write_text(
            json.dumps(payload["events"], indent=2), encoding="utf-8"
        )


def load_analysis(job_dir: Path) -> AnalysisResult:
    """Read the cached analysis for a job."""
    path = job_dir / ANALYSIS_FILE
    if not path.exists():
        raise WorkflowError("This song has not been analysed yet.")
    try:
        return AnalysisResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise WorkflowError("The stored analysis could not be read.") from exc


def summarize_analysis(analysis: AnalysisResult) -> dict[str, object]:
    """A compact, client-safe view of the analysis for the UI."""
    grid = analysis.grid
    return {
        "bpm": round(grid.bpm, 2),
        "duration": round(analysis.duration, 2),
        "tempo_confidence": round(grid.confidence, 3),
        "variable_tempo": grid.is_variable,
        "representative_bpm": round(grid.representative_bpm, 2),
        "beats_per_bar": grid.beats_per_bar,
        "meter": {2: "2/4", 3: "3/4", 4: "4/4", 6: "6/8"}.get(
            grid.beats_per_bar, f"{grid.beats_per_bar}/4"
        ),
        "triple_subdivision": grid.triple_subdivision,
        "tempo_segments": [
            {
                "start": round(segment.start_time, 2),
                "start_beat": round(segment.start_beat, 2),
                "bpm": round(segment.bpm, 2),
            }
            for segment in grid.segments
        ],
        "beat_count": len(analysis.beats),
        "onset_count": len(analysis.onsets),
        "event_count": len(analysis.events),
        "sections": [
            {
                "start": round(section.start, 2),
                "end": round(section.end, 2),
                "type": section.type.value,
                "intensity": round(section.intensity, 3),
            }
            for section in analysis.sections
        ],
        "energy_curve": _downsample(analysis.features.rms, 220),
        "onset_curve": _downsample(analysis.features.onset_strength, 220),
        "tempo_candidates": analysis.tempo_candidates[:4],
    }


def _downsample(values: list[float], target: int) -> list[float]:
    """Reduce a frame-level curve to a size the browser can draw."""
    if not values:
        return []
    if len(values) <= target:
        return [round(value, 4) for value in values]
    bucket = len(values) / target
    result: list[float] = []
    for index in range(target):
        start = int(index * bucket)
        end = max(int((index + 1) * bucket), start + 1)
        window = values[start:end]
        result.append(round(sum(window) / len(window), 4))
    return result


async def run_generation(
    *,
    job_id: str,
    store: JobStore,
    settings: Settings,
    options: GenerationOptions,
    metadata: MapMetadata,
) -> None:
    """Generate, validate and package a map for one difficulty."""
    job_dir = store.job_dir(job_id)
    started = time.perf_counter()

    async def progress(status: str, percent: int, label: str) -> None:
        await store.update(
            job_id,
            status=JobStatus(status),
            progress=percent,
            current_step=label,
            stage="generation",
        )

    loop = asyncio.get_running_loop()

    def sync_progress(status: str, percent: int, label: str) -> None:
        # Called from the worker thread; hand the update back to the loop.
        asyncio.run_coroutine_threadsafe(progress(status, percent, label), loop)

    try:
        await progress(JobStatus.SELECTING_RHYTHM.value, 5, "Applying difficulty profile")
        analysis = await asyncio.to_thread(load_analysis, job_dir)

        if options.bpm_override:
            # Re-fit the timeline onto the corrected grid. Cheap: the DSP
            # results are grid-independent and are reused as they are.
            try:
                analysis = await asyncio.to_thread(
                    requantize, analysis, options.bpm_override
                )
            except ValueError as exc:
                raise WorkflowError(str(exc)) from exc

        debug_dir = (job_dir / "debug") if settings.debug_mapping else None
        song_path = job_dir / SONG_FILENAME
        cover_path = job_dir / COVER_FILE

        result = await asyncio.to_thread(
            partial(
                generate_beatmap,
                analysis,
                options,
                progress=sync_progress,
                debug_dir=debug_dir,
                song_path=song_path,
                cover_path=cover_path,
            )
        )

        if not result.validation.ok:
            detail = result.validation.errors[0] if result.validation.errors else ""
            logger.warning(
                "generation_validation_failed",
                extra={"job_id": job_id, "errors": result.validation.errors},
            )
            raise WorkflowError(f"Map validation failed. {detail}".strip())

        await progress(JobStatus.EXPORTING.value, 90, "Exporting Beat Saber map")

        await progress(JobStatus.PACKAGING.value, 96, "Packaging download")
        package = await asyncio.to_thread(
            build_package,
            beatmaps=[result.beatmap],
            metadata=metadata,
            grid=analysis.grid,
            song_source=song_path,
            output_dir=job_dir,
            cover_source=cover_path if cover_path.exists() else None,
        )

        profile = PROFILES[options.difficulty]
        payload = {
            "difficulty": options.difficulty.value,
            "difficulty_label": options.difficulty.label,
            "style": options.style.value,
            "intensity": round(options.intensity, 3),
            "seed": options.seed,
            "bpm": round(analysis.bpm, 2),
            "bpm_overridden": options.bpm_override is not None,
            "variable_tempo": analysis.grid.is_variable,
            "tempo_segment_count": len(analysis.grid.segments),
            "beats_per_bar": analysis.grid.beats_per_bar,
            "triple_subdivision": analysis.grid.triple_subdivision,
            "duration": round(analysis.duration, 2),
            "note_jump_speed": result.beatmap.note_jump_speed,
            "note_jump_offset": result.beatmap.note_jump_offset,
            "statistics": result.beatmap.statistics.to_dict(),
            "validation": result.validation.to_dict(),
            "profile": {
                "target_nps_min": profile.target_nps_min,
                "target_nps_max": profile.target_nps_max,
                "peak_nps": profile.peak_nps,
                "effective_target_nps": round(
                    profile.interpolated_nps(options.intensity), 3
                ),
            },
            "package": {
                "filename": package.filename,
                "contents": package.contents,
                "size_bytes": package.size_bytes,
            },
            "song": {"title": metadata.title, "artist": metadata.artist, "mapper": metadata.mapper},
        }
        (job_dir / RESULT_FILE).write_text(json.dumps(payload, indent=2), encoding="utf-8")

        await store.update(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            current_step="Map ready",
            stage="generation",
            result=payload,
            error=None,
        )
        logger.info(
            "generation_workflow_completed",
            extra={
                "job_id": job_id,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "difficulty": options.difficulty.value,
                "notes": result.beatmap.statistics.total_notes,
            },
        )

    except WorkflowError as exc:
        logger.warning("generation_workflow_error", extra={"job_id": job_id, "detail": exc.detail})
        await store.fail(job_id, exc.message)
    except FileNotFoundError:
        logger.exception("generation_missing_asset", extra={"job_id": job_id})
        await store.fail(job_id, "The song audio for this job is no longer available.")
    except Exception:
        logger.exception("generation_failed", extra={"job_id": job_id})
        await store.fail(job_id, "Could not generate a map for this song.")
