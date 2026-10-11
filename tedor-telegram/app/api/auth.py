"""API authentication helpers (Requirement 10.6, 13.2)."""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.api.miniapp.auth import AuthError, validate_init_data
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


def verify_telegram_init_data(init_data: str | None) -> int | None:
    """The Telegram user id a valid Init Data header names, or ``None``.

    Uses the same HMAC validation as the Mini App endpoints, so an accepted
    Init Data is exactly as trustworthy as an accepted API secret: it was
    signed by Telegram with the bot token.
    """
    if not init_data:
        return None
    try:
        user = validate_init_data(init_data, settings.bot_token)
    except AuthError:
        return None
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
        return None


def require_api_secret_or_telegram_user(
    x_api_secret: Annotated[str | None, Header(alias=SECRET_HEADER)] = None,
    x_telegram_init_data: Annotated[
        str | None, Header(alias="X-Telegram-Init-Data")
    ] = None,
) -> bool:
    """Accept either the API secret or a signed Mini App Init Data.

    The location endpoints spend the geocoding quota, which is why they are
    not public — but the Mini App's location search is a first-class caller of
    them, and it authenticates with Init Data rather than the API secret. Both
    credentials are verified the same way, and the per-client rate limits still
    apply on top.
    """
    if verify_api_secret(x_api_secret):
        return True
    if verify_telegram_init_data(x_telegram_init_data) is not None:
        return True
    logger.warning("Rejected location request without a valid credential")
    raise _UNAUTHORIZED


__all__ = [
    "SECRET_HEADER",
    "require_api_secret",
    "require_api_secret_or_telegram_user",
    "verify_api_secret",
    "verify_telegram_init_data",
]