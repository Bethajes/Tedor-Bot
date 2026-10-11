"""A Bot instance the REST layer can archive with.

The bot handlers reach Telegram through the polling ``Application``. The REST
API has no such object, which is why Mini App uploads used to be accepted and
then dropped: the bytes were read, never sent, and stored with a ``None`` file
ID. Only long-polling conflicts with a second client — plain API calls do not,
so the API can hold its own ``Bot`` for the same token.

Everything here is best-effort. Archiving must never be able to fail a tutor's
submission, so callers treat a missing bot or a Telegram error as "not
archived" rather than propagating it.
"""

from __future__ import annotations

import logging

from telegram import Bot

from app.config import settings

logger = logging.getLogger(__name__)

_bot: Bot | None = None


def get_bot() -> Bot | None:
    """Return the shared archive ``Bot``, or ``None`` if unconfigured.

    The instance is created once and reused; python-telegram-bot initialises
    its HTTP client lazily on first use, so it is safe to obtain from inside a
    request handler.
    """
    global _bot
    if _bot is not None:
        return _bot
    if not settings.bot_token:
        logger.error("Cannot archive: BOT_TOKEN is not configured")
        return None
    _bot = Bot(settings.bot_token)
    return _bot


def reset_bot() -> None:
    """Drop the cached client (configuration changed, or after a fork)."""
    global _bot
    _bot = None


__all__ = ["get_bot", "reset_bot"]