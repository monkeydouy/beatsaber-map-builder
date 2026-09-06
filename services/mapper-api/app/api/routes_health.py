"""Health and capability endpoints."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends

from app.api.deps import AppState, get_state
from app.models.enums import DIFFICULTY_ORDER, MappingStyle
from app.schemas.jobs import DifficultyInfo, HealthResponse
from app.services.mapping.difficulty_profiles import DIFFICULTY_DESCRIPTIONS, PROFILES

router = APIRouter(tags=["system"])

VERSION = "1.0.0"


@router.get("/health", response_model=HealthResponse)
async def health(state: AppState = Depends(get_state)) -> HealthResponse:
    """Report service health and which optional tooling is available."""
    settings = state.settings
    return HealthResponse(
        status="ok",
        version=VERSION,
        ffmpeg=shutil.which(settings.ffmpeg_binary) is not None,
        ytdlp=shutil.which(settings.ytdlp_binary) is not None,
        youtube_enabled=settings.enable_youtube
        and shutil.which(settings.ytdlp_binary) is not None,
        active_jobs=state.manager.active_count,
        max_concurrent_jobs=settings.max_concurrent_jobs,
    )


@router.get("/difficulties", response_model=list[DifficultyInfo])
async def difficulties() -> list[DifficultyInfo]:
    """Describe the difficulty options so the UI stays in sync with the engine."""
    return [
        DifficultyInfo(
            value=difficulty.value,
            label=difficulty.label,
            description=DIFFICULTY_DESCRIPTIONS[difficulty],
            target_nps_min=PROFILES[difficulty].target_nps_min,
            target_nps_max=PROFILES[difficulty].target_nps_max,
            peak_nps=PROFILES[difficulty].peak_nps,
        )
        for difficulty in DIFFICULTY_ORDER
    ]


@router.get("/styles")
async def styles() -> list[dict[str, str]]:
    return [
        {
            "value": MappingStyle.BALANCED.value,
            "label": "Balanced",
            "description": "Natural flow and faithful musical representation.",
        },
        {
            "value": MappingStyle.DANCE.value,
            "label": "Dance",
            "description": "Wide, sweeping patterns that keep your body moving.",
        },
        {
            "value": MappingStyle.TECHNICAL.value,
            "label": "Technical",
            "description": "Denser direction changes and more demanding transitions.",
        },
    ]
