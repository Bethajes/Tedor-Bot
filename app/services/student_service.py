"""Student / parent request service."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import Currency, RequestStatus
from app.models import StudentRequest, utcnow
from app.services.currency import currency_for_country, is_ethiopia, normalise_country

logger = logging.getLogger(__name__)

REQUEST_PREFIX = "REQ"
REQUEST_PATTERN = re.compile(r"^REQ-\d{6}$")
REQUEST_COUNTER = "student_request"

REQUIRED_FIELDS = (
    "telegram_user_id",
    "parent_name",
    "student_name",
    "country",
    "education_level",
    "subject",
    "mode",
)

# A Telegram username is 5–32 chars, starts with a letter, and may contain
# digits and underscores.
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
CONTACT_SPLIT_RE = re.compile(r"[\s,;/]+")


def contact_fields(value: Any) -> tuple[str, str]:
    """Split free-text contact details into ``(phone, contact_handle)``.

    The parent may type a phone number, a ``@username``, or both in one
    message ("+251911234567 @abebe"). Anything is stored exactly as typed --
    nothing is invented, so a handle is never silently turned into a link.
    """
    text = " ".join(str(value or "").split())
    if not text:
        return "", ""

    phone = ""
    handle = ""
    for token in [item for item in CONTACT_SPLIT_RE.split(text) if item]:
        bare = token[1:] if token.startswith("@") else token
        if token.startswith("@") or USERNAME_RE.match(bare):
            handle = handle or token
        elif re.match(r"^\+?[0-9][0-9\s\-()]{5,19}$", bare):
            phone = phone or bare
        elif not phone and not handle:
            phone = token
    return phone, handle


def resolve_contact(data: dict[str, Any]) -> tuple[str, str]:
    """Merge the explicit ``phone``/``contact_handle`` keys with ``contact``.

    ``contact`` is the key the Telegram flow collects on its last step; the
    import path and the API pass ``phone``/``contact_handle`` directly.
    """
    phone = str(data.get("phone") or "").strip()
    handle = str(data.get("contact_handle") or "").strip()
    if phone and handle:
        return phone, handle
    extra_phone, extra_handle = contact_fields(data.get("contact"))
    return phone or extra_phone, handle or extra_handle


class StudentRequestValidationError(ValueError):
    """Raised when a student request fails validation."""


def _next_request_sequence(session: Session) -> int:
    from sqlalchemy.exc import IntegrityError

    from app.models import IdCounter

    stmt = (
        select(StudentRequest.id).order_by(StudentRequest.id.desc()).limit(1)
    )
    highest = session.execute(stmt).scalar_one_or_none() or 0
    counter_row = session.execute(
        select(IdCounter).where(IdCounter.name == REQUEST_COUNTER)
    ).scalar_one_or_none()
    if counter_row is None:
        try:
            with session.begin_nested():
                session.add(IdCounter(name=REQUEST_COUNTER, value=highest + 1))
            return highest + 1
        except IntegrityError:  # pragma: no cover - concurrent first insert
            session.expire_all()
            row = session.execute(
                select(IdCounter).where(IdCounter.name == REQUEST_COUNTER)
            ).scalar_one()
            row.value += 1
            session.flush()
            return int(row.value)
    counter_row.value = max(int(counter_row.value), highest) + 1
    session.flush()
    return int(counter_row.value)


def generate_request_id(session: Session) -> str:
    """Return the next unique public request ID (``REQ-000001``)."""
    sequence = _next_request_sequence(session)
    request_id = f"{REQUEST_PREFIX}-{sequence:06d}"
    while (
        session.execute(
            select(StudentRequest.id).where(
                StudentRequest.public_request_id == request_id
            )
        ).scalar_one_or_none()
        is not None
    ):  # pragma: no cover - defensive
        sequence = _next_request_sequence(session)
        request_id = f"{REQUEST_PREFIX}-{sequence:06d}"
    return request_id


def budget_fields(country: str | None, budget: Any) -> tuple[float | None, float | None]:
    """Route the budget to the currency implied by ``country`` (Property 4).

    Returns ``(etb_budget, usd_budget)`` where exactly one side is populated for
    a supplied budget.
    """
    value = _as_float(budget)
    if value is None:
        return None, None
    if is_ethiopia(country):
        return value, None
    return None, value


def validate_request_data(data: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_FIELDS if not data.get(field)]
    if missing:
        raise StudentRequestValidationError(
            "Missing required request fields: " + ", ".join(sorted(missing))
        )


def create_student_request(session: Session, data: dict[str, Any]) -> StudentRequest:
    """Create a student request with ETB/USD routing based on country."""
    validate_request_data(data)

    country = normalise_country(data["country"])
    budget = _as_float(data.get("budget"))
    etb_budget, usd_budget = budget_fields(country, budget)
    currency = currency_for_country(country)
    phone, contact_handle = resolve_contact(data)

    request = StudentRequest(
        public_request_id=generate_request_id(session),
        telegram_user_id=int(data["telegram_user_id"]),
        parent_name=str(data["parent_name"]).strip(),
        student_name=str(data["student_name"]).strip(),
        phone=phone,
        contact_handle=contact_handle,
        country=country,
        education_level=str(data["education_level"]).strip(),
        subject=str(data["subject"]).strip(),
        preferred_language=str(data.get("preferred_language") or "").strip(),
        mode=str(data["mode"]).upper(),
        schedule=str(data.get("schedule") or "").strip(),
        additional_requirements=str(data.get("additional_requirements") or "").strip(),
        budget=budget,
        currency=str(currency),
        etb_budget=etb_budget,
        usd_budget=usd_budget,
        status=str(data.get("status") or RequestStatus.NEW).upper(),
    )
    session.add(request)
    session.flush()
    logger.info(
        "Student request created",
        extra={
            "request_id": request.public_request_id,
            "currency": request.currency,
        },
    )
    return request


def get_request_by_id(session: Session, request_id: str) -> StudentRequest | None:
    return session.execute(
        select(StudentRequest).where(
            StudentRequest.public_request_id == str(request_id).strip().upper()
        )
    ).scalar_one_or_none()


def get_request_by_telegram_id(
    session: Session, telegram_user_id: int
) -> StudentRequest | None:
    return session.execute(
        select(StudentRequest)
        .where(StudentRequest.telegram_user_id == int(telegram_user_id))
        .order_by(StudentRequest.id.desc())
    ).scalars().first()


def set_request_storage_reference(
    session: Session, request: StudentRequest, chat_id: int | None, message_id: int | None
) -> None:
    request.telegram_request_chat_id = chat_id
    request.telegram_request_message_id = message_id
    request.storage_status = "STORED" if message_id else "STORAGE_FAILED"
    request.updated_at = utcnow()
    session.flush()


def assign_tutor(
    session: Session, request: StudentRequest, tdr_id: str
) -> StudentRequest:
    request.matched_tutor_id = str(tdr_id).strip().upper()
    request.status = str(RequestStatus.MATCHED)
    request.updated_at = utcnow()
    session.flush()
    logger.info(
        "Tutor selected for student request",
        extra={
            "request_id": request.public_request_id,
            "tdr_id": request.matched_tutor_id,
        },
    )
    return request


def contact_display(request: StudentRequest) -> str:
    """Every contact detail on one line, or an em dash when there is none."""
    parts = [item for item in (request.contact_handle, request.phone) if item]
    return " / ".join(parts) if parts else "—"


def format_request_summary(request: StudentRequest) -> str:
    budget = "—"
    if request.budget is not None:
        budget = f"{request.budget:g} {request.currency}"
    lines = [
        f"Request ID: {request.public_request_id}",
        f"Parent: {request.parent_name}",
        f"Student: {request.student_name}",
        f"Country: {request.country}",
        f"Level: {request.education_level}",
        f"Subject: {request.subject}",
        f"Language: {request.preferred_language or '—'}",
        f"Mode: {request.mode}",
        f"Budget: {budget}",
        f"Schedule: {request.schedule or '—'}",
        f"Requirements: {request.additional_requirements or '—'}",
        f"Contact: {contact_display(request)}",
    ]
    return "\n".join(lines)


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CONTACT_SPLIT_RE",
    "Currency",
    "REQUEST_PATTERN",
    "USERNAME_RE",
    "StudentRequestValidationError",
    "assign_tutor",
    "budget_fields",
    "contact_display",
    "contact_fields",
    "create_student_request",
    "format_request_summary",
    "generate_request_id",
    "get_request_by_id",
    "get_request_by_telegram_id",
    "resolve_contact",
    "set_request_storage_reference",
    "validate_request_data",
]