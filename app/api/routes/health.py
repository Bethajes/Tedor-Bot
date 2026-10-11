"""Health endpoint (Requirement 10.5)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy import text

from app import __version__
from app.api.schemas import HealthResponse
from app.database import get_db
from app.models import Tutor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/health", response_model=HealthResponse)
def health(session: DbSession) -> HealthResponse:
    """Returns 200 with the status of the API and the database."""
    try:
        session.execute(text("SELECT 1"))
        database = "ok"
        tutors = int(
            session.execute(select(func.count()).select_from(Tutor)).scalar_one()
        )
    except Exception:
        logger.error("Health check could not reach the database")
        database = "unavailable"
        tutors = None
    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        database=database,
        version=__version__,
        tutors=tutors,
    )


__all__ = ["router"]