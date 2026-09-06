"""Job records and their persistence.

State lives in memory for speed and is mirrored to ``job.json`` inside the job
directory so a restart does not orphan finished work. The store is the only
thing that touches job state, which keeps the swap to Redis a single-class
change rather than a refactor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.models.enums import JobStatus, SourceType

logger = logging.getLogger(__name__)

JOB_FILENAME = "job.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class JobRecord:
    """Everything known about one job."""

    id: str
    status: JobStatus = JobStatus.CREATED
    progress: int = 0
    current_step: str = "Created"
    stage: str = "analysis"
    source_type: SourceType | None = None
    error: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    #: Song metadata (title, artist, duration, thumbnail...).
    metadata: dict[str, Any] = field(default_factory=dict)
    #: Compact analysis summary safe to send to the client.
    analysis: dict[str, Any] | None = None
    #: Result of the most recent generation.
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["source_type"] = self.source_type.value if self.source_type else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRecord:
        source_type = data.get("source_type")
        return cls(
            id=data["id"],
            status=JobStatus(data.get("status", JobStatus.CREATED.value)),
            progress=int(data.get("progress", 0)),
            current_step=data.get("current_step", ""),
            stage=data.get("stage", "analysis"),
            source_type=SourceType(source_type) if source_type else None,
            error=data.get("error"),
            created_at=data.get("created_at", _now()),
            updated_at=data.get("updated_at", _now()),
            metadata=data.get("metadata") or {},
            analysis=data.get("analysis"),
            result=data.get("result"),
        )


def is_valid_job_id(candidate: str) -> bool:
    """Only well-formed UUIDs may address a job directory.

    This is the single check that makes path traversal impossible: a job id can
    never contain a separator or a dot segment.
    """
    try:
        return str(UUID(candidate)) == candidate.lower()
    except (ValueError, AttributeError, TypeError):
        return False


class JobStore:
    """In-memory job state with a disk mirror."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._jobs: dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    # -- paths -------------------------------------------------------------

    def job_dir(self, job_id: str) -> Path:
        """Resolve a job's directory, refusing anything that escapes the root."""
        if not is_valid_job_id(job_id):
            raise ValueError("Invalid job id")
        path = (self._root / job_id).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise ValueError("Invalid job id")
        return path

    # -- lifecycle ---------------------------------------------------------

    async def create(self, source_type: SourceType) -> JobRecord:
        job_id = str(uuid4())
        record = JobRecord(id=job_id, source_type=source_type)
        directory = self.job_dir(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            self._jobs[job_id] = record
        await self._persist(record)
        logger.info("job_created", extra={"job_id": job_id, "source": source_type.value})
        return record

    async def get(self, job_id: str) -> JobRecord | None:
        if not is_valid_job_id(job_id):
            return None
        async with self._lock:
            record = self._jobs.get(job_id)
        if record is not None:
            return record
        return await self._load(job_id)

    async def update(self, job_id: str, **changes: Any) -> JobRecord | None:
        """Apply changes and mirror them to disk."""
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                record = await self._load_unlocked(job_id)
                if record is None:
                    return None
                self._jobs[job_id] = record
            for key, value in changes.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            record.updated_at = _now()
            snapshot = record
        await self._persist(snapshot)
        return snapshot

    async def fail(self, job_id: str, message: str) -> JobRecord | None:
        return await self.update(
            job_id,
            status=JobStatus.FAILED,
            error=message,
            current_step="Failed",
            progress=100,
        )

    async def list_ids(self) -> list[str]:
        async with self._lock:
            memory = set(self._jobs)
        if self._root.exists():
            memory |= {
                path.name for path in self._root.iterdir() if path.is_dir() and is_valid_job_id(path.name)
            }
        return sorted(memory)

    async def delete(self, job_id: str) -> None:
        async with self._lock:
            self._jobs.pop(job_id, None)
        try:
            directory = self.job_dir(job_id)
        except ValueError:
            return
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
        logger.info("job_deleted", extra={"job_id": job_id})

    # -- persistence -------------------------------------------------------

    async def _persist(self, record: JobRecord) -> None:
        try:
            directory = self.job_dir(record.id)
            directory.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(record.to_dict(), indent=2)
            await asyncio.to_thread(
                (directory / JOB_FILENAME).write_text, payload, encoding="utf-8"
            )
        except (OSError, ValueError):
            logger.warning("job_persist_failed", extra={"job_id": record.id}, exc_info=True)

    async def _load(self, job_id: str) -> JobRecord | None:
        async with self._lock:
            return await self._load_unlocked(job_id)

    async def _load_unlocked(self, job_id: str) -> JobRecord | None:
        try:
            path = self.job_dir(job_id) / JOB_FILENAME
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            record = JobRecord.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError):
            logger.warning("job_load_failed", extra={"job_id": job_id}, exc_info=True)
            return None
        self._jobs[job_id] = record
        return record
