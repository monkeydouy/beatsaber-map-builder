"""Centralised application configuration, sourced from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every value is overridable via the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    storage_path: Path = Field(default=Path("storage"), alias="STORAGE_PATH")

    max_upload_mb: int = Field(default=50, alias="MAX_UPLOAD_MB")
    max_audio_duration_seconds: int = Field(default=600, alias="MAX_AUDIO_DURATION_SECONDS")
    max_concurrent_jobs: int = Field(default=2, alias="MAX_CONCURRENT_JOBS")
    job_retention_hours: int = Field(default=6, alias="JOB_RETENTION_HOURS")
    cleanup_interval_seconds: int = Field(default=900, alias="CLEANUP_INTERVAL_SECONDS")

    ffmpeg_binary: str = Field(default="ffmpeg", alias="FFMPEG_BINARY")
    ffprobe_binary: str = Field(default="ffprobe", alias="FFPROBE_BINARY")
    ytdlp_binary: str = Field(default="yt-dlp", alias="YTDLP_BINARY")

    ffmpeg_timeout_seconds: int = Field(default=120, alias="FFMPEG_TIMEOUT_SECONDS")
    youtube_download_timeout_seconds: int = Field(
        default=120, alias="YOUTUBE_DOWNLOAD_TIMEOUT_SECONDS"
    )
    youtube_metadata_timeout_seconds: int = Field(
        default=30, alias="YOUTUBE_METADATA_TIMEOUT_SECONDS"
    )
    enable_youtube: bool = Field(default=True, alias="ENABLE_YOUTUBE")

    allowed_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000", alias="ALLOWED_ORIGINS"
    )

    debug_mapping: bool = Field(default=False, alias="DEBUG_MAPPING")

    # Analysis tuning
    analysis_sample_rate: int = Field(default=44100, alias="ANALYSIS_SAMPLE_RATE")
    analysis_hop_length: int = Field(default=512, alias="ANALYSIS_HOP_LENGTH")
    #: Allow a piecewise tempo map (v3 `bpmEvents`) when a song drifts.
    #: Turn off to force a single constant BPM for maximum tool compatibility.
    enable_variable_tempo: bool = Field(default=True, alias="ENABLE_VARIABLE_TEMPO")

    @field_validator("storage_path")
    @classmethod
    def _resolve_storage(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def jobs_path(self) -> Path:
        return self.storage_path / "jobs"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.jobs_path.mkdir(parents=True, exist_ok=True)
    return settings
