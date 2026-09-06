"""Background job execution.

A deliberately small worker abstraction: an asyncio task per job, gated by a
semaphore that caps concurrent CPU-heavy work. `JobManager.submit` is the only
entry point, and it takes a coroutine factory — swapping this for RQ or Celery
means reimplementing one class, not rewriting the API layer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from app.services.jobs.store import JobStore

logger = logging.getLogger(__name__)

JobCoroutine = Callable[[], Awaitable[None]]


class JobManager:
    """Runs job coroutines with bounded concurrency."""

    def __init__(self, store: JobStore, *, max_concurrent: int = 2) -> None:
        self._store = store
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._max_concurrent = max(1, max_concurrent)

    @property
    def active_count(self) -> int:
        return sum(1 for task in self._tasks.values() if not task.done())

    def is_running(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        return task is not None and not task.done()

    async def submit(self, job_id: str, factory: JobCoroutine, *, operation: str) -> None:
        """Queue work for a job. Raises if that job is already running."""
        if self.is_running(job_id):
            raise RuntimeError("This job is already running")

        task = asyncio.create_task(self._run(job_id, factory, operation))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(job_id, None))

    async def _run(self, job_id: str, factory: JobCoroutine, operation: str) -> None:
        started = time.perf_counter()
        async with self._semaphore:
            logger.info("job_started", extra={"job_id": job_id, "operation": operation})
            try:
                await factory()
            except asyncio.CancelledError:
                logger.info("job_cancelled", extra={"job_id": job_id, "operation": operation})
                await self._store.fail(job_id, "The job was cancelled.")
                raise
            except Exception:
                # The handler that raised is responsible for setting a
                # user-safe message; this is the last-resort net.
                logger.exception(
                    "job_failed", extra={"job_id": job_id, "operation": operation}
                )
                record = await self._store.get(job_id)
                if record is None or record.error is None:
                    await self._store.fail(job_id, "Something went wrong while processing.")
            else:
                logger.info(
                    "job_completed",
                    extra={
                        "job_id": job_id,
                        "operation": operation,
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                    },
                )

    async def shutdown(self) -> None:
        """Cancel outstanding work on application shutdown."""
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
