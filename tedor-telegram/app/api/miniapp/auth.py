"""Telegram Mini App authentication (Requirements 2.1–2.4, 2.5, 2.6).

Two ways to prove who the caller is:

**Inside Telegram** — ``window.Telegram.WebApp.initData`` arrives in the
``X-Telegram-Init-Data`` header and is verified against the bot token with
HMAC-SHA256, per the Telegram Bot API specification:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

**Outside Telegram** — a web login exchanges a one-time code for a short-lived
JWT carrying the same ``telegram_user_id``. It is deliberately the same claim the
Init Data yields, so every route handler above the auth dependency works
unchanged and no handler has to know which of the two was used.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Header, HTTPException, status

from app.config import settings


class AuthError(Exception):
    """Raised when Init Data validation fails."""


@dataclass(frozen=True)
class TelegramUser:
    """Minimal representation of the authenticated Telegram user."""

    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""


def validate_init_data(init_data: str, bot_token: str) -> dict:
    """Validate Telegram WebApp initData and return the parsed user dict.

    Algorithm (per Telegram docs):
    1. Parse the init_data query string into key-value pairs.
    2. Extract and remove the ``hash`` field.
    3. Sort remaining pairs alphabetically by key, join as ``key=value\\n...``.
    4. Compute HMAC-SHA256 of that string using
       ``HMAC-SHA256("WebAppData", bot_token)`` as the secret key.
    5. Compare the computed hex digest with the extracted ``hash`` value.

    Returns the parsed ``user`` JSON dict on success.
    Raises ``AuthError`` on any failure.
    """
    if not init_data:
        raise AuthError("Init Data is empty")

    # Parse as URL query string
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    except Exception as exc:
        raise AuthError(f"Init Data is malformed: {exc}") from exc

    received_hash = params.pop("hash", None)
    if received_hash is None:
        raise AuthError("Init Data is missing 'hash' field")

    # Build the data-check string: sorted key=value pairs joined by newlines
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(params.items())
    )

    # Secret key = HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode(),
        digestmod=hashlib.sha256,
    ).digest()

    # Compute expected hash
    expected_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    try:
        hashes_match = hmac.compare_digest(expected_hash, received_hash)
    except TypeError:
        # compare_digest requires both strings to be ASCII; a non-hex hash is always invalid
        raise AuthError("Init Data hash is not a valid hex string")
    if not hashes_match:
        raise AuthError("Init Data hash mismatch")

    # Extract and parse the user JSON
    user_json = params.get("user")
    if not user_json:
        raise AuthError("Init Data is missing 'user' field")

    try:
        user_dict = json.loads(user_json)
    except json.JSONDecodeError as exc:
        raise AuthError(f"Init Data 'user' field is not valid JSON: {exc}") from exc

    if "id" not in user_dict:
        raise AuthError("Init Data user dict is missing 'id'")

    return user_dict


def get_current_telegram_user(
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
    x_telegram_token: Annotated[str | None, Header(alias="X-Telegram-Token")] = None,
) -> TelegramUser:
    """FastAPI dependency: identify the caller, or answer 401.

    Two accepted credentials, checked in that order:

    1. signed Init Data from Telegram (the Mini App opened from a chat);
    2. a web-login JWT from the non-Telegram browser (Requirement 2.5).

    Both produce the same :class:`TelegramUser`, so nothing downstream has to
    know which one authenticated the request.
    """
    if x_telegram_init_data:
        try:
            user_dict = validate_init_data(x_telegram_init_data, settings.bot_token)
        except AuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            ) from exc
        return TelegramUser(
            id=int(user_dict["id"]),
            first_name=user_dict.get("first_name", ""),
            last_name=user_dict.get("last_name", ""),
            username=user_dict.get("username", ""),
        )

    if x_telegram_token:
        user = user_from_web_token(x_telegram_token)
        if user is not None:
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


# ---------------------------------------------------------------------------
# Web login tokens (the non-Telegram browser)
# ---------------------------------------------------------------------------

#: How long a web-login token stays usable. Deliberately short: the browser
#: fallback is for filling in a form, not for a session that lives for days.
WEB_TOKEN_TTL_SECONDS = 3600


def _web_token_secret() -> str:
    """The signing secret for web-login tokens.

    Falls back through the deployment's other secrets rather than defaulting to
    a constant: a shared default would mean anybody reading this module could
    mint tokens for any Telegram user id.
    """
    for candidate in (
        settings.mini_app_jwt_secret,
        settings.api_secret,
        settings.bot_token,
    ):
        if candidate:
            return str(candidate)
    return ""


def create_web_token(telegram_user_id: int, ttl_seconds: int | None = None) -> str:
    """Mint a short-lived JWT carrying the caller's ``telegram_user_id``.

    The claim name matches the one Init Data yields, which is the whole point:
    the route handlers read ``user.id`` and never learn where it came from.
    """
    secret = _web_token_secret()
    if not secret:
        raise AuthError("No signing secret is configured for web login tokens")
    now = int(time.time())
    payload = {
        "telegram_user_id": int(telegram_user_id),
        "iat": now,
        "exp": now + int(ttl_seconds if ttl_seconds is not None else WEB_TOKEN_TTL_SECONDS),
        "iss": "tedor-mini-app",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def user_from_web_token(token: str) -> TelegramUser | None:
    """The user a web-login token names, or ``None`` when it is not usable.

    An expired, tampered or unsigned token is never an error here — the caller
    turns ``None`` into a plain 401, so a bad token cannot be distinguished
    from no token.
    """
    secret = _web_token_secret()
    if not secret or not token:
        return None
    try:
        claims = jwt.decode(
            token, secret, algorithms=["HS256"], options={"require": ["telegram_user_id", "exp"]}
        )
        return TelegramUser(id=int(claims["telegram_user_id"]))
    except Exception:
        return None


__all__ = [
    "AuthError",
    "TelegramUser",
    "WEB_TOKEN_TTL_SECONDS",
    "create_web_token",
    "get_current_telegram_user",
    "user_from_web_token",
    "validate_init_data",
]
