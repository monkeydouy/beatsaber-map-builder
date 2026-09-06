"""Request and response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.enums import Difficulty, MappingStyle

MAX_SEED = 2**31 - 1


class YouTubeRequest(BaseModel):
    url: str = Field(min_length=5, max_length=2048)
    confirmed: bool = Field(
        default=False,
        description="User confirmation that they may process this audio.",
    )


class YouTubePreviewResponse(BaseModel):
    video_id: str
    title: str
    artist: str
    channel: str
    duration: float
    thumbnail: str | None = None
    url: str


class BatchItem(BaseModel):
    """One file's outcome in a multi-file upload."""

    filename: str
    job_id: str | None = None
    status: str | None = None
    error: str | None = None


class BatchUploadResponse(BaseModel):
    """Per-file results. Partial success is normal and expected."""

    accepted: int
    rejected: int
    items: list[BatchItem]


class YouTubeBatchRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=16)
    confirmed: bool = Field(
        default=False,
        description="User confirmation that they may process all of this audio.",
    )


class YouTubeBatchItem(BaseModel):
    """One link's outcome. Carries the video details when they resolved."""

    url: str
    job_id: str | None = None
    title: str | None = None
    channel: str | None = None
    duration: float | None = None
    thumbnail: str | None = None
    error: str | None = None


class YouTubeBatchResponse(BaseModel):
    accepted: int
    rejected: int
    items: list[YouTubeBatchItem]


class JobCreatedResponse(BaseModel):
    job_id: str
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class GenerationRequest(BaseModel):
    """Options for one map generation."""

    title: str | None = Field(default=None, max_length=140)
    artist: str | None = Field(default=None, max_length=140)
    mapper: str | None = Field(default=None, max_length=140)
    difficulty: str = Field(default=Difficulty.EXPERT.value)
    style: str = Field(default=MappingStyle.BALANCED.value)
    intensity: float = Field(default=0.6, ge=0.0, le=1.0)
    seed: int | None = Field(default=None, ge=0, le=MAX_SEED)
    #: Correct a mis-detected tempo without re-analysing the audio. When the
    #: grid is wrong every note is placed against music that is not there, and
    #: no amount of pattern work can rescue it.
    bpm: float | None = Field(default=None, ge=40.0, le=300.0)
    enable_bombs: bool = False
    enable_walls: bool = True
    enable_lighting: bool = True

    @field_validator("difficulty")
    @classmethod
    def _validate_difficulty(cls, value: str) -> str:
        return Difficulty.parse(value).value

    @field_validator("style")
    @classmethod
    def _validate_style(cls, value: str) -> str:
        key = (value or "").strip().lower()
        try:
            return MappingStyle(key).value
        except ValueError as exc:
            allowed = ", ".join(member.value for member in MappingStyle)
            raise ValueError(f"Unsupported style. Choose one of: {allowed}") from exc

    @field_validator("title", "artist", "mapper")
    @classmethod
    def _clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @property
    def parsed_difficulty(self) -> Difficulty:
        return Difficulty(self.difficulty)

    @property
    def parsed_style(self) -> MappingStyle:
        return MappingStyle(self.style)


class JobResponse(BaseModel):
    id: str
    status: str
    progress: int
    current_step: str
    stage: str
    source_type: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class DifficultyInfo(BaseModel):
    value: str
    label: str
    description: str
    target_nps_min: float
    target_nps_max: float
    peak_nps: float


class HealthResponse(BaseModel):
    status: str
    version: str
    ffmpeg: bool
    ytdlp: bool
    youtube_enabled: bool
    active_jobs: int
    max_concurrent_jobs: int
