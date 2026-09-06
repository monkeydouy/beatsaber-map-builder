"""Media acquisition abstraction.

Every input path (upload, YouTube, anything added later) resolves to a
`NormalizedMedia`. Downstream stages never learn where the audio came from.
"""

from __future__ import annotations

import abc
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.models.enums import SourceType


class MediaAcquisitionError(Exception):
    """Raised when a source cannot be turned into usable audio.

    `message` is safe to show a user; it must never contain paths, commands or
    stack traces.
    """

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or message


@dataclass(slots=True)
class NormalizedMedia:
    """A raw media file plus whatever metadata the source could supply."""

    source_type: SourceType
    source_path: Path
    title: str
    artist: str
    duration: float
    thumbnail: str | None = None
    original_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_type"] = self.source_type.value
        data["source_path"] = str(self.source_path)
        return data


class AudioSource(abc.ABC):
    """Interface implemented by every input adapter."""

    @abc.abstractmethod
    async def acquire(self) -> NormalizedMedia:
        """Fetch the media into job storage and describe it."""
