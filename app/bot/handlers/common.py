"""Shared helpers for the conversation handlers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

from app.logging_config import get_logger

logger = get_logger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-()]{6,19}$")
NAME_RE = re.compile(r"^[\w' .\-`]{2,80}$", re.UNICODE)

APP_KEY = "tutor_application"
FLOW_KEY = "active_flow"
DOC_KEY = "tutor_documents"
STEP_KEY = "tutor_step"
SUMMARY_EDIT_KEY = "tutor_edit_field"


class ValidationError(ValueError):
    """Friendly, user-facing validation problem."""


# ---------------------------------------------------------------------------
# Step model
# ---------------------------------------------------------------------------


@dataclass
class Step:
    """Declarative description of one conversation step."""

    state: int
    field: str
    prompt: str
    kind: str = "text"  # text|int|float|email|phone|name|photo|document|choice|multiselect
    options: tuple[str, ...] | tuple[tuple[str, str], ...] = ()
    optional: bool = False
    #: May the user press Continue without answering? Skippable steps are the
    #: ones where an empty value is a legitimate answer.
    skippable: bool = False
    keyboard_factory: Any = None
    skip_if: str | None = None


@dataclass
class NavSpec:
    keyboard: InlineKeyboardMarkup | None = None
    back: bool = True
    edit: bool = True
    continue_: bool = True
    fields: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# application data
# ---------------------------------------------------------------------------


def get_application(context: Any) -> dict[str, Any]:
    data = context.user_data.get(APP_KEY)
    if data is None:
        data = {"documents": []}
        context.user_data[APP_KEY] = data
    data.setdefault("documents", [])
    return data


def reset_application(context: Any) -> None:
    for key in (APP_KEY, DOC_KEY, STEP_KEY, SUMMARY_EDIT_KEY):
        context.user_data.pop(key, None)


def start_flow(context: Any, name: str) -> None:
    """Mark the user as being inside a conversation.

    The global fallback handler stays silent while a flow is active so the user
    never receives "I did not recognise that" in the middle of a form.
    """
    context.user_data[FLOW_KEY] = name


def active_flow(context: Any) -> str | None:
    return context.user_data.get(FLOW_KEY)


def end_flow(context: Any) -> None:
    context.user_data.pop(FLOW_KEY, None)


def current_step(context: Any, key: str = STEP_KEY) -> int | None:
    """The conversation step the user is currently on."""
    value = context.user_data.get(key)
    return int(value) if value is not None else None


def set_step(context: Any, state: int | None, key: str = STEP_KEY) -> None:
    if state is None:
        context.user_data.pop(key, None)
    else:
        context.user_data[key] = int(state)


# ---------------------------------------------------------------------------
# messaging helpers
# ---------------------------------------------------------------------------


async def safe_edit(update: Update, text: str, keyboard: Any = None, **kwargs: Any) -> None:
    """Edit the triggering message, falling back to a new message."""
    keyboard = keyboard if keyboard is not None else InlineKeyboardMarkup([])
    try:
        if update.callback_query is not None:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, **kwargs)
        elif update.message is not None:
            await update.message.edit_text(text, reply_markup=keyboard, **kwargs)
        else:  # pragma: no cover - defensive
            return
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        if update.callback_query is not None:
            await update.callback_query.answer()
        elif update.message is not None:
            await update.message.reply_text(text, reply_markup=keyboard, **kwargs)
    except TelegramError:  # pragma: no cover - network issues
        logger.error("Could not edit message")
        if update.effective_message is not None:
            await update.effective_message.reply_text(
                text, reply_markup=keyboard, **kwargs
            )


async def safe_reply(update: Update, text: str, keyboard: Any = None, **kwargs: Any) -> None:
    keyboard = keyboard if keyboard is not None else InlineKeyboardMarkup([])
    try:
        if update.effective_message is not None:
            await update.effective_message.reply_text(
                text, reply_markup=keyboard, **kwargs
            )
    except TelegramError:  # pragma: no cover - network issues
        logger.error("Could not send message")


async def answer(update: Update, text: str | None = None, show_alert: bool = False) -> None:
    query = getattr(update, "callback_query", None)
    if query is not None:
        try:
            await query.answer(text or "", show_alert=show_alert)
        except TelegramError:  # pragma: no cover - already answered
            pass


# ---------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------


def validate_name(value: str) -> str:
    text = value.strip()
    if not NAME_RE.match(text):
        raise ValidationError("Please enter a valid name (2–80 letters).")
    return text


def validate_phone(value: str) -> str:
    text = value.strip()
    if not PHONE_RE.match(text):
        raise ValidationError("Please enter a valid phone number, e.g. +251911234567.")
    return text


def validate_email(value: str) -> str:
    text = value.strip()
    if not EMAIL_RE.match(text):
        raise ValidationError("That email address looks invalid. Example: name@example.com")
    return text


def validate_text(value: str, label: str, minimum: int = 2, maximum: int = 500) -> str:
    text = " ".join(value.strip().split())
    if len(text) < minimum:
        raise ValidationError(f"Please provide {label} (at least {minimum} characters).")
    if len(text) > maximum:
        raise ValidationError(f"{label} is too long — keep it under {maximum} characters.")
    return text


def validate_int(value: str, label: str, minimum: int = 0, maximum: int = 60) -> int:
    try:
        number = int(re.sub(r"[^0-9\-]", "", value.strip()))
    except ValueError:
        raise ValidationError(f"{label} must be a whole number.") from None
    if not minimum <= number <= maximum:
        raise ValidationError(f"{label} must be between {minimum} and {maximum}.")
    return number


def validate_float(value: str, label: str, minimum: float = 1.0, maximum: float = 100000.0) -> float:
    cleaned = value.strip().replace(",", "")
    try:
        number = float(re.sub(r"[^0-9.]", "", cleaned))
    except ValueError:
        raise ValidationError(f"{label} must be a number.") from None
    if not minimum <= number <= maximum:
        raise ValidationError(f"{label} must be between {minimum:g} and {maximum:g}.")
    return round(number, 2)


def validate_year(value: str) -> int:
    """Graduation years are four digit numbers close to today."""
    return validate_int(value, "Graduation year", 1900, datetime.now(timezone.utc).year + 5)


def split_list(value: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[,\n;]", value)]
    return [part for part in dict.fromkeys(parts) if part]


VALIDATORS = {
    "year": validate_year,
    "name": validate_name,
    "phone": validate_phone,
    "email": validate_email,
    "text": lambda v: validate_text(v, "this field"),
    "int": lambda v: validate_int(v, "This value"),
    "float": lambda v: validate_float(v, "This value"),
}


def run_validator(kind: str, value: str) -> Any:
    validator = VALIDATORS.get(kind)
    return validator(value) if validator else value.strip()


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------


def html_escape(value: Any) -> str:
    text = str(value if value not in (None, "") else "—")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def summary_lines(application: dict[str, Any], labels: dict[str, str]) -> list[str]:
    """Build the application summary lines from collected data."""
    lines = ["<b>📋 Application Summary</b>", ""]
    for field_name, label in labels.items():
        value = application.get(field_name)
        if isinstance(value, list):
            if value and isinstance(value[0], dict):
                value = ", ".join(str(item.get("document_type")) for item in value)
            else:
                value = ", ".join(str(item) for item in value)
        lines.append(f"<b>{html_escape(label)}:</b> {html_escape(value)}")
    return lines


def send_lines(update: Update, lines: list[str], keyboard: Any = None) -> None:
    safe_reply(update, "\n".join(lines), keyboard, parse_mode=ParseMode.HTML)


__all__ = [
    "APP_KEY",
    "FLOW_KEY",
    "DOC_KEY",
    "EMAIL_RE",
    "NAME_RE",
    "PHONE_RE",
    "STEP_KEY",
    "SUMMARY_EDIT_KEY",
    "NavSpec",
    "Step",
    "ValidationError",
    "active_flow",
    "answer",
    "current_step",
    "end_flow",
    "get_application",
    "html_escape",
    "reset_application",
    "run_validator",
    "safe_edit",
    "safe_reply",
    "send_lines",
    "set_step",
    "split_list",
    "start_flow",
    "summary_lines",
    "validate_email",
    "validate_float",
    "validate_int",
    "validate_name",
    "validate_phone",
    "validate_text",
    "validate_year",
]