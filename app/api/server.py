"""FastAPI application factory (Requirements 10.6, 12.3, 13.x)."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import health, jobs, tutors
from app.config import settings
from app.database import init_db
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Read-only REST API for verified Tedor Tutors profiles. "
    "Private data (Telegram IDs, phone numbers, emails, chat IDs) is never exposed."
)


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    configure_logging(settings.log_level)

    application = FastAPI(
        title="Tedor Tutors API",
        description=DESCRIPTION,
        version=__version__,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    origins = settings.api_cors_origins or ["*"]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    application.include_router(health.router)
    application.include_router(tutors.router)
    application.include_router(jobs.router)

    @application.on_event("startup")
    def _startup() -> None:
        init_db()
        logger.info("REST API ready")

    return application


app = create_app()


__all__ = ["app", "create_app"]