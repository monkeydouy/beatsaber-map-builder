"""Shared application state and FastAPI dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.config import Settings
from app.services.jobs.manager import JobManager
from app.services.jobs.store import JobStore


@dataclass(slots=True)
class AppState:
    settings: Settings
    store: JobStore
    manager: JobManager


def get_state(request: Request) -> AppState:
    return request.app.state.app_state  # type: ignore[no-any-return]


def get_settings_dep(request: Request) -> Settings:
    return get_state(request).settings


def get_store(request: Request) -> JobStore:
    return get_state(request).store


def get_manager(request: Request) -> JobManager:
    return get_state(request).manager
