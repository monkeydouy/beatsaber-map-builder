"""Job endpoints: upload, YouTube, status, generate, download."""

from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from app.api.deps import AppState, get_state
from app.models.enums import JobStatus, SourceType
from app.schemas.jobs import (
    BatchItem,
    BatchUploadResponse,
    GenerationRequest,
    JobCreatedResponse,
    JobResponse,
    YouTubeBatchItem,
    YouTubeBatchRequest,
    YouTubeBatchResponse,
    YouTubePreviewResponse,
    YouTubeRequest,
)
from app.services.export.beatsaber_exporter import MapMetadata
from app.services.export.package_builder import build_bundle
from app.services.jobs.store import JobRecord
from app.services.jobs.workflows import run_analysis, run_generation
from app.services.mapping.pipeline import GenerationOptions
from app.services.media.base import MediaAcquisitionError
from app.services.media.upload_source import UploadedFileSource, sanitize_filename
from app.services.media.youtube_source import YouTubeSource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: Read uploads in chunks so an oversized file is rejected before it lands.
UPLOAD_CHUNK = 1024 * 1024

MAX_SEED = 2**31 - 1

#: Cap on one multi-file upload, so a stray folder drop cannot flood storage.
MAX_BATCH_FILES = 15

#: Cap on one batch of links. Lower than the file cap because each one costs a
#: round trip to YouTube before the job even starts.
MAX_BATCH_URLS = 8

#: Metadata lookups run concurrently, but only this many at once — enough to
#: keep a batch responsive without hammering a volunteer-run service.
YOUTUBE_METADATA_CONCURRENCY = 3

#: Statuses from which a new generation may be started.
GENERATABLE = frozenset({JobStatus.ANALYZED, JobStatus.COMPLETED, JobStatus.FAILED})


def _to_response(record: JobRecord) -> JobResponse:
    return JobResponse(**record.to_dict())


async def _require_job(state: AppState, job_id: str) -> JobRecord:
    record = await state.store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return record


def _capacity_guard(state: AppState) -> None:
    if state.manager.active_count >= state.settings.max_concurrent_jobs:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="The server is busy processing other songs. Please try again shortly.",
        )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@router.post("/upload", response_model=JobCreatedResponse, status_code=201)
async def create_upload_job(
    file: UploadFile = File(...),
    state: AppState = Depends(get_state),
) -> JobCreatedResponse:
    """Accept an audio upload and start analysis."""
    _capacity_guard(state)

    filename = sanitize_filename(file.filename or "upload.mp3")
    try:
        UploadedFileSource.validate_metadata(filename, file.content_type)
    except MediaAcquisitionError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    record = await state.store.create(SourceType.UPLOAD)
    job_dir = state.store.job_dir(record.id)
    # The client's filename is never used for storage; only its extension is.
    suffix = Path(filename).suffix.lower() or ".mp3"
    stored_path = job_dir / f"source{suffix}"

    written = 0
    limit = state.settings.max_upload_bytes
    try:
        with stored_path.open("wb") as handle:
            while chunk := await file.read(UPLOAD_CHUNK):
                written += len(chunk)
                if written > limit:
                    handle.close()
                    await state.store.delete(record.id)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {state.settings.max_upload_mb} MB limit.",
                    )
                handle.write(chunk)
    except HTTPException:
        raise
    except OSError as exc:
        await state.store.delete(record.id)
        logger.exception("upload_write_failed", extra={"job_id": record.id})
        raise HTTPException(status_code=500, detail="Could not store the upload.") from exc
    finally:
        await file.close()

    if written == 0:
        await state.store.delete(record.id)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    source = UploadedFileSource(
        stored_path,
        original_filename=filename,
        content_type=file.content_type,
        max_duration=state.settings.max_audio_duration_seconds,
    )

    await state.manager.submit(
        record.id,
        lambda: run_analysis(
            job_id=record.id, source=source, store=state.store, settings=state.settings
        ),
        operation="analysis",
    )
    logger.info(
        "upload_accepted", extra={"job_id": record.id, "bytes": written}
    )
    return JobCreatedResponse(job_id=record.id, status=record.status.value, metadata={})


async def _store_upload(state: AppState, file: UploadFile) -> tuple[str, str]:
    """Persist one upload and start its analysis. Returns (job_id, filename)."""
    filename = sanitize_filename(file.filename or "upload.mp3")
    UploadedFileSource.validate_metadata(filename, file.content_type)

    record = await state.store.create(SourceType.UPLOAD)
    job_dir = state.store.job_dir(record.id)
    suffix = Path(filename).suffix.lower() or ".mp3"
    stored_path = job_dir / f"source{suffix}"

    written = 0
    limit = state.settings.max_upload_bytes
    try:
        with stored_path.open("wb") as handle:
            while chunk := await file.read(UPLOAD_CHUNK):
                written += len(chunk)
                if written > limit:
                    handle.close()
                    await state.store.delete(record.id)
                    raise MediaAcquisitionError(
                        f"File exceeds the {state.settings.max_upload_mb} MB limit."
                    )
                handle.write(chunk)
    except MediaAcquisitionError:
        raise
    except OSError as exc:
        await state.store.delete(record.id)
        logger.exception("upload_write_failed", extra={"job_id": record.id})
        raise MediaAcquisitionError("Could not store the upload.") from exc
    finally:
        await file.close()

    if written == 0:
        await state.store.delete(record.id)
        raise MediaAcquisitionError("The uploaded file is empty.")

    source = UploadedFileSource(
        stored_path,
        original_filename=file.filename or filename,
        content_type=file.content_type,
        max_duration=state.settings.max_audio_duration_seconds,
    )
    await state.manager.submit(
        record.id,
        lambda: run_analysis(
            job_id=record.id, source=source, store=state.store, settings=state.settings
        ),
        operation="analysis",
    )
    return record.id, filename


@router.post("/upload/batch", response_model=BatchUploadResponse, status_code=201)
async def create_upload_jobs(
    files: list[UploadFile] = File(...),
    state: AppState = Depends(get_state),
) -> BatchUploadResponse:
    """Accept several songs at once.

    Each file becomes its own job, and one bad file does not sink the rest —
    the response reports per-file outcomes rather than failing the whole batch.
    Analysis still runs `MAX_CONCURRENT_JOBS` at a time; the extras queue.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded.")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"Please upload at most {MAX_BATCH_FILES} files at a time.",
        )

    items: list[BatchItem] = []
    for file in files:
        name = sanitize_filename(file.filename or "upload.mp3")
        try:
            job_id, _stored = await _store_upload(state, file)
        except MediaAcquisitionError as exc:
            items.append(BatchItem(filename=name, error=exc.message))
        except Exception:
            logger.exception("batch_upload_item_failed", extra={"filename": name})
            items.append(BatchItem(filename=name, error="Could not accept this file."))
        else:
            items.append(BatchItem(filename=name, job_id=job_id, status="CREATED"))

    accepted = sum(1 for item in items if item.job_id)
    logger.info(
        "batch_upload_accepted",
        extra={"files": len(items), "accepted": accepted},
    )
    return BatchUploadResponse(
        accepted=accepted, rejected=len(items) - accepted, items=items
    )


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------


@router.post("/youtube/preview", response_model=YouTubePreviewResponse)
async def preview_youtube(
    payload: YouTubeRequest,
    state: AppState = Depends(get_state),
) -> YouTubePreviewResponse:
    """Fetch video details so the user can confirm before anything downloads."""
    if not state.settings.enable_youtube:
        raise HTTPException(status_code=503, detail="YouTube input is disabled on this server.")
    try:
        source = YouTubeSource(
            payload.url,
            Path("."),
            max_duration=state.settings.max_audio_duration_seconds,
        )
        metadata = await source.fetch_metadata()
    except MediaAcquisitionError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    return YouTubePreviewResponse(
        video_id=str(metadata["video_id"]),
        title=str(metadata["title"]),
        artist=str(metadata["artist"]),
        channel=str(metadata["channel"]),
        duration=float(metadata["duration"]),  # type: ignore[arg-type]
        thumbnail=metadata.get("thumbnail"),  # type: ignore[arg-type]
        url=str(metadata["url"]),
    )


@router.post("/youtube", response_model=JobCreatedResponse, status_code=201)
async def create_youtube_job(
    payload: YouTubeRequest,
    state: AppState = Depends(get_state),
) -> JobCreatedResponse:
    """Start analysis for a YouTube video the user has confirmed rights to."""
    if not state.settings.enable_youtube:
        raise HTTPException(status_code=503, detail="YouTube input is disabled on this server.")
    if not payload.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Please confirm you have permission to process this audio.",
        )
    _capacity_guard(state)

    record = await state.store.create(SourceType.YOUTUBE)
    job_dir = state.store.job_dir(record.id)

    try:
        source = YouTubeSource(
            payload.url,
            job_dir,
            max_duration=state.settings.max_audio_duration_seconds,
        )
        metadata = await source.fetch_metadata()
    except MediaAcquisitionError as exc:
        await state.store.delete(record.id)
        raise HTTPException(status_code=400, detail=exc.message) from exc

    await state.store.update(
        record.id,
        metadata={
            "title": metadata["title"],
            "artist": metadata["artist"],
            "duration": metadata["duration"],
            "thumbnail": metadata.get("thumbnail"),
            "original_url": metadata["url"],
            "source_type": SourceType.YOUTUBE.value,
        },
    )

    await state.manager.submit(
        record.id,
        lambda: run_analysis(
            job_id=record.id, source=source, store=state.store, settings=state.settings
        ),
        operation="analysis",
    )
    return JobCreatedResponse(
        job_id=record.id, status=record.status.value, metadata=dict(metadata)
    )


# ---------------------------------------------------------------------------
# Status / generation / download
# ---------------------------------------------------------------------------


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, state: AppState = Depends(get_state)) -> JobResponse:
    """Poll job state. The frontend drives its progress bar from this."""
    return _to_response(await _require_job(state, job_id))


@router.post("/youtube/batch", response_model=YouTubeBatchResponse, status_code=201)
async def create_youtube_jobs(
    payload: YouTubeBatchRequest,
    state: AppState = Depends(get_state),
) -> YouTubeBatchResponse:
    """Start analysis for several videos at once.

    Metadata is resolved first, so a link that turns out to be a livestream or
    a private video is reported rather than silently producing a failed job.
    One bad link does not sink the rest of the batch.
    """
    if not state.settings.enable_youtube:
        raise HTTPException(status_code=503, detail="YouTube input is disabled on this server.")
    if not payload.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Please confirm you have permission to process this audio.",
        )

    # De-duplicate while preserving the order the user typed.
    urls: list[str] = []
    for raw in payload.urls:
        candidate = raw.strip()
        if candidate and candidate not in urls:
            urls.append(candidate)
    if not urls:
        raise HTTPException(status_code=400, detail="No links were provided.")
    if len(urls) > MAX_BATCH_URLS:
        raise HTTPException(
            status_code=413,
            detail=f"Please add at most {MAX_BATCH_URLS} links at a time.",
        )

    limiter = asyncio.Semaphore(YOUTUBE_METADATA_CONCURRENCY)

    async def resolve(url: str) -> tuple[str, dict[str, object] | None, str | None]:
        async with limiter:
            try:
                source = YouTubeSource(
                    url, Path("."), max_duration=state.settings.max_audio_duration_seconds
                )
                return url, await source.fetch_metadata(), None
            except MediaAcquisitionError as exc:
                return url, None, exc.message
            except Exception:
                logger.exception("youtube_batch_metadata_failed")
                return url, None, "Could not read that video's details."

    resolved = await asyncio.gather(*(resolve(url) for url in urls))

    items: list[YouTubeBatchItem] = []
    for url, metadata, error in resolved:
        if metadata is None:
            items.append(YouTubeBatchItem(url=url, error=error))
            continue
        try:
            record = await state.store.create(SourceType.YOUTUBE)
            job_dir = state.store.job_dir(record.id)
            source = YouTubeSource(
                url, job_dir, max_duration=state.settings.max_audio_duration_seconds
            )
            await state.store.update(
                record.id,
                metadata={
                    "title": metadata["title"],
                    "artist": metadata["artist"],
                    "duration": metadata["duration"],
                    "thumbnail": metadata.get("thumbnail"),
                    "original_url": metadata["url"],
                    "source_type": SourceType.YOUTUBE.value,
                },
            )
            await state.manager.submit(
                record.id,
                lambda job_id=record.id, src=source: run_analysis(
                    job_id=job_id, source=src, store=state.store, settings=state.settings
                ),
                operation="analysis",
            )
        except Exception:
            logger.exception("youtube_batch_start_failed", extra={"url": url[:80]})
            items.append(YouTubeBatchItem(url=url, error="Could not start that job."))
            continue

        items.append(
            YouTubeBatchItem(
                url=url,
                job_id=record.id,
                title=str(metadata["title"]),
                channel=str(metadata.get("channel") or ""),
                duration=float(metadata["duration"]),  # type: ignore[arg-type]
                thumbnail=metadata.get("thumbnail"),  # type: ignore[arg-type]
            )
        )

    accepted = sum(1 for item in items if item.job_id)
    logger.info(
        "youtube_batch_accepted", extra={"links": len(items), "accepted": accepted}
    )
    return YouTubeBatchResponse(
        accepted=accepted, rejected=len(items) - accepted, items=items
    )


@router.post("/{job_id}/generate", response_model=JobResponse, status_code=202)
async def generate(
    job_id: str,
    payload: GenerationRequest,
    state: AppState = Depends(get_state),
) -> JobResponse:
    """Generate a map for the selected difficulty, reusing the cached analysis."""
    record = await _require_job(state, job_id)

    if state.manager.is_running(job_id):
        raise HTTPException(status_code=409, detail="This song is still being processed.")
    if record.status not in GENERATABLE:
        raise HTTPException(
            status_code=409,
            detail="This song has not finished analysis yet.",
        )
    if record.analysis is None:
        raise HTTPException(status_code=409, detail="This song has not been analysed yet.")
    _capacity_guard(state)

    seed = payload.seed if payload.seed is not None else secrets.randbelow(MAX_SEED)
    options = GenerationOptions(
        difficulty=payload.parsed_difficulty,
        style=payload.parsed_style,
        intensity=payload.intensity,
        seed=seed,
        enable_bombs=payload.enable_bombs,
        enable_walls=payload.enable_walls,
        enable_lighting=payload.enable_lighting,
        bpm_override=payload.bpm,
    )

    stored = record.metadata or {}
    metadata = MapMetadata(
        title=payload.title or str(stored.get("title") or "Untitled"),
        artist=payload.artist or str(stored.get("artist") or "Unknown Artist"),
        mapper=payload.mapper or "SaberMapper AI",
    )

    updated = await state.store.update(
        job_id,
        status=JobStatus.SELECTING_RHYTHM,
        progress=0,
        current_step="Starting generation",
        stage="generation",
        result=None,
        error=None,
    )

    await state.manager.submit(
        job_id,
        lambda: run_generation(
            job_id=job_id,
            store=state.store,
            settings=state.settings,
            options=options,
            metadata=metadata,
        ),
        operation="generation",
    )
    return _to_response(updated or record)


@router.get("/bundle/download")
async def download_bundle(
    ids: str,
    state: AppState = Depends(get_state),
) -> FileResponse:
    """Serve several finished maps as one archive.

    Registered before `/{job_id}/download` so "bundle" is not swallowed as a
    job id. Each map lands in its own folder inside the zip, so the whole
    thing extracts straight into a `CustomLevels` directory.
    """
    job_ids = [value.strip() for value in ids.split(",") if value.strip()]
    if not job_ids:
        raise HTTPException(status_code=400, detail="No maps were selected.")
    if len(job_ids) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Please download at most {MAX_BATCH_FILES} maps at a time.",
        )

    sources: list[tuple[str, Path]] = []
    for job_id in job_ids:
        record = await state.store.get(job_id)
        if record is None or record.status is not JobStatus.COMPLETED or not record.result:
            continue
        package_dir = state.store.job_dir(job_id) / "package"
        if not package_dir.is_dir():
            continue
        package = record.result.get("package") or {}
        name = str(package.get("filename") or f"SaberMapper {job_id[:8]}")
        sources.append((name.removesuffix(".zip"), package_dir))

    if not sources:
        raise HTTPException(
            status_code=404, detail="None of those maps are ready to download."
        )

    bundle_dir = state.settings.storage_path / "bundles"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_dir / f"{uuid4()}.zip"

    try:
        result = await asyncio.to_thread(build_bundle, sources, bundle_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=404, detail="None of those maps are ready to download."
        ) from exc

    logger.info(
        "bundle_downloaded", extra={"requested": len(job_ids), "included": len(sources)}
    )
    return FileResponse(
        path=result.zip_path,
        media_type="application/zip",
        filename=result.filename,
        headers={"Cache-Control": "no-store"},
        # The archive is rebuilt per request; do not leave it behind.
        background=BackgroundTask(bundle_path.unlink, missing_ok=True),
    )


@router.get("/{job_id}/download")
async def download(job_id: str, state: AppState = Depends(get_state)) -> FileResponse:
    """Serve the generated ZIP through a controlled endpoint.

    Storage is never exposed as a static directory; this handler is the only
    way bytes leave the job folder, and it only ever serves one known filename.
    """
    record = await _require_job(state, job_id)
    if record.status is not JobStatus.COMPLETED or not record.result:
        raise HTTPException(status_code=409, detail="No completed map is available yet.")

    zip_path = state.store.job_dir(job_id) / "beatmap.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="The generated map has expired.")

    package = record.result.get("package") or {}
    filename = str(package.get("filename") or "beatmap.zip")
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=filename,
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/{job_id}", status_code=204, response_class=Response)
async def delete_job(job_id: str, state: AppState = Depends(get_state)) -> Response:
    """Let a user discard their own job and its files immediately."""
    await _require_job(state, job_id)
    if state.manager.is_running(job_id):
        raise HTTPException(status_code=409, detail="This job is still running.")
    await state.store.delete(job_id)
    return Response(status_code=204)
