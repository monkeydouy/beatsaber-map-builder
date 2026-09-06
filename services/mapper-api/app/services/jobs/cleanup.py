"""Periodic removal of expired job data."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.services.jobs.manager import JobManager
from app.services.jobs.store import JobStore

logger = logging.getLogger(__name__)


async def purge_expired(
    store: JobStore, manager: JobManager, *, retention_hours: int
) -> int:
    """Delete jobs older than the retention window. Active jobs are spared."""
    cutoff = datetime.now(UTC) - timedelta(hours=retention_hours)
    removed = 0

    for job_id in await store.list_ids():
        if manager.is_running(job_id):
            continue
        record = await store.get(job_id)
        if record is None:
            continue
        try:
            updated = datetime.fromisoformat(record.updated_at)
        except ValueError:
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        if updated < cutoff:
            await store.delete(job_id)
            removed += 1

    if removed:
        logger.info("cleanup_completed", extra={"removed_jobs": removed})
    return removed


async def cleanup_loop(
    store: JobStore,
    manager: JobManager,
    *,
    retention_hours: int,
    interval_seconds: int,
) -> None:
    """Run `purge_expired` forever, surviving individual failures."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await purge_expired(store, manager, retention_hours=retention_hours)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("cleanup_failed")
