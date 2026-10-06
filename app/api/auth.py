"""API authentication helpers (Requirement 10.6, 13.2)."""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)

SECRET_HEADER = "X-API-Secret"

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing API secret",
    headers={"WWW-Authenticate": SECRET_HEADER},
)


def verify_api_secret(provided: str | None) -> bool:
    """Constant-time comparison; the supplied value is never logged."""
    expected = settings.api_secret
    if not expected:
        logger.error("API_SECRET is not configured")
        return False
    if not provided:
        return False
    return secrets.compare_digest(str(provided), str(expected))


def require_api_secret(
    x_api_secret: Annotated[str | None, Header(alias=SECRET_HEADER)] = None,
) -> bool:
    """FastAPI dependency guarding the admin endpoints."""
    if not verify_api_secret(x_api_secret):
        logger.warning("Rejected API request with an invalid secret")
        raise _UNAUTHORIZED
    return True


__all__ = ["SECRET_HEADER", "require_api_secret", "verify_api_secret"]