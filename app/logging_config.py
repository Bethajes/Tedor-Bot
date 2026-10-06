"""Structured logging configuration.

Requirement 13.3: tokens, secrets, Telegram user IDs, phone numbers and email
addresses must never reach the log output.  A redaction filter enforces this
for the well known sensitive keys.
"""

from __future__ import annotations

import logging
import re
import sys
import warnings
from collections.abc import MutableMapping
from typing import Any, Iterable, Mapping

SENSITIVE_KEYS = {
    "bot_token",
    "token",
    "api_secret",
    "secret",
    "password",
    "telegram_user_id",
    "telegram_id",
    "user_id",
    "phone",
    "email",
}

REDACTED = "***"

_PATTERNS = (
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),  # bot token
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),  # email address
    re.compile(r"\+?\d[\d\s-]{8,}\d"),  # phone number
)


def _redact_text(value: str) -> str:
    for pattern in _PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


class RedactFilter(logging.Filter):
    """Mask secrets and PII in both messages and ``extra`` values."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = _redact_text(str(record.msg))
        except Exception:  # pragma: no cover - defensive
            pass
        if record.args:
            if isinstance(record.args, Mapping):
                record.args = {k: self._mask(k, v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact_text(str(arg)) for arg in record.args)
        for key in list(record.__dict__):
            if key.lower() in SENSITIVE_KEYS:
                record.__dict__[key] = REDACTED
        return True

    @staticmethod
    def _mask(key: str, value: Any) -> Any:
        if str(key).lower() in SENSITIVE_KEYS:
            return REDACTED
        if isinstance(value, str):
            return _redact_text(value)
        return value


class SafeLoggerAdapter(logging.LoggerAdapter):
    """Convenience adapter so callers can pass structured, already-masked data."""

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        extra = kwargs.get("extra") or {}
        for key in list(extra):
            if key.lower() in SENSITIVE_KEYS:
                extra[key] = REDACTED
        kwargs["extra"] = extra
        return msg, kwargs


def configure_logging(level: str | None = None) -> None:
    """Install the root logging configuration (idempotent)."""
    resolved = (level or "INFO").upper()
    root = logging.getLogger()
    root.setLevel(resolved)
    for handler in root.handlers:
        if getattr(handler, "_tedor_handler", False):
            handler.setLevel(resolved)
            return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(resolved)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.addFilter(RedactFilter())
    handler._tedor_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    # PTB warns about per_message=False; conversations deliberately use the
    # per-chat/per-user conversation key.
    warnings.filterwarnings("ignore", message=".*per_message=False.*", category=Warning)


def get_logger(name: str) -> SafeLoggerAdapter:
    return SafeLoggerAdapter(logging.getLogger(name), {})


def masked_keys(keys: Iterable[str]) -> dict[str, Any]:
    """Return a dict of ``key -> masked value`` pairs safe for logging."""
    return {key: REDACTED for key in keys}