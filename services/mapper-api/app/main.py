"""SaberMapper AI — FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import AppState
from app.api.routes_health import router as health_router
from app.api.routes_jobs import router as jobs_router
from app.config import get_settings
from app.logging_config import configure_logging
from app.services.jobs.cleanup import cleanup_loop
from app.services.jobs.manager import JobManager
from app.services.jobs.store import JobStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    store = JobStore(settings.jobs_path)
    manager = JobManager(store, max_concurrent=settings.max_concurrent_jobs)
    app.state.app_state = AppState(settings=settings, store=store, manager=manager)

    cleanup_task = asyncio.create_task(
        cleanup_loop(
            store,
            manager,
            retention_hours=settings.job_retention_hours,
            interval_seconds=settings.cleanup_interval_seconds,
        )
    )
    logger.info(
        "service_started",
        extra={
            "env": settings.app_env,
            "storage": str(settings.storage_path),
            "max_concurrent_jobs": settings.max_concurrent_jobs,
        },
    )

    try:
        yield
    finally:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
        await manager.shutdown()
        logger.info("service_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="SaberMapper AI",
        description="Turn your music into a playable Beat Saber map.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Return the first readable validation message, never the raw payload."""
        detail = "The request was not valid."
        for error in exc.errors():
            message = error.get("msg", "")
            if message:
                detail = message.replace("Value error, ", "")
                break
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """Log the detail, tell the user nothing about our internals."""
        logger.exception(
            "unhandled_exception",
            extra={"path": request.url.path, "method": request.method},
        )
        del exc
        return JSONResponse(
            status_code=500, content={"detail": "Something went wrong. Please try again."}
        )

    return app


app = create_app()
