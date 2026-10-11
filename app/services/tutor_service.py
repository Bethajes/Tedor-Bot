"""Tutor service: TDR ID generation, persistence, status and search."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.enums import TutorFilters, TutorStatus
from app.models import (
    IdCounter,
    StudentRequest,
    SupportTicket,
    Tutor,
    TutorAvailability,
    TutorDocument,
    TutorEducation,
    TutorLanguage,
    TutorLevel,
    TutorSubject,
    utcnow,
)

logger = logging.getLogger(__name__)

TDR_PREFIX = "TDR"
TDR_COUNTER = "tutor_tdr"
TDR_PATTERN = re.compile(r"^TDR-\d{6}$")

REQUIRED_FIELDS = (
    "telegram_user_id",
    "name",
    "display_name",
    "phone",
    "email",
    "country",
    "city",
    "teaching_mode",
)


class TutorValidationError(ValueError):
    """Raised when tutor data fails validation."""


def format_tdr_id(sequence: int) -> str:
    return f"{TDR_PREFIX}-{sequence:06d}"


def tdr_sequence(tdr_id: str) -> int:
    match = TDR_PATTERN.match(tdr_id or "")
    if not match:
        raise ValueError(f"Malformed TDR ID: {tdr_id!r}")
    return int(tdr_id.split("-", 1)[1])


# ---------------------------------------------------------------------------
# TDR ID generation (Requirement 3)
# ---------------------------------------------------------------------------


def next_tdr_sequence(session: Session) -> int:
    """Atomically increment and return the TDR counter.

    The whole increment happens inside a single ``UPDATE ... RETURNING``
    statement, which the database serialises, so two concurrent transactions can
    never be handed the same number.  The counter is only ever incremented, which
    guarantees a TDR ID is never re-assigned (Requirements 3.2 and 3.3).
    """
    stmt = (
        update(IdCounter)
        .where(IdCounter.name == TDR_COUNTER)
        .values(value=IdCounter.value + 1, updated_at=utcnow())
        .returning(IdCounter.value)
    )
    value = session.execute(stmt).scalar_one_or_none()
    if value is not None:
        return int(value)

    try:
        with session.begin_nested():
            session.add(IdCounter(name=TDR_COUNTER, value=1))
    except IntegrityError:
        # Another transaction created the counter first: retry the atomic update.
        return int(session.execute(stmt).scalar_one())
    return 1


def generate_tdr_id(session: Session) -> str:
    """Return the next unused TDR ID (``TDR-000001``)."""
    value = next_tdr_sequence(session)
    tdr_id = format_tdr_id(value)
    while session.execute(
        select(Tutor.id).where(Tutor.public_tutor_id == tdr_id)
    ).scalar_one_or_none() is not None:  # pragma: no cover - defensive
        value = next_tdr_sequence(session)
        tdr_id = format_tdr_id(value)
    return tdr_id


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def _replace_collection(
    session: Session, tutor: Tutor, model: type, field: str, values: Sequence[str]
) -> None:
    for item in list(getattr(tutor, field)):
        session.delete(item)
    for value in dict.fromkeys(values):
        session.add(model(tutor_id=tutor.id, **{_column_for(model): value}))


def _column_for(model: type) -> str:
    return {
        TutorSubject: "subject",
        TutorLevel: "level",
        TutorLanguage: "language",
    }[model]


def validate_tutor_data(data: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_FIELDS if not data.get(field)]
    if missing:
        raise TutorValidationError(
            "Missing required tutor fields: " + ", ".join(sorted(missing))
        )


def create_tutor(session: Session, data: dict[str, Any]) -> Tutor:
    """Create and persist a tutor (status defaults to ``PENDING``)."""
    validate_tutor_data(data)

    tutor = Tutor(
        public_tutor_id=generate_tdr_id(session),
        telegram_user_id=int(data["telegram_user_id"]),
        name=str(data["name"]).strip(),
        display_name=str(data["display_name"]).strip(),
        phone=str(data.get("phone") or "").strip(),
        email=str(data.get("email") or "").strip(),
        country=str(data["country"]).strip(),
        city=str(data["city"]).strip(),
        bio=str(data.get("bio") or "").strip(),
        profile_photo_file_id=data.get("profile_photo_file_id"),
        etb_rate=_as_float(data.get("etb_rate")),
        usd_rate=_as_float(data.get("usd_rate")),
        experience_years=int(data.get("experience_years") or 0),
        timezone=str(data.get("timezone") or "UTC").strip(),
        teaching_mode=str(data["teaching_mode"]).upper(),
        status=str(data.get("status") or TutorStatus.PENDING).upper(),
    )
    session.add(tutor)
    session.flush()

    _replace_collection(session, tutor, TutorSubject, "subjects", data.get("subjects") or [])
    _replace_collection(session, tutor, TutorLevel, "levels", data.get("levels") or [])
    _replace_collection(
        session, tutor, TutorLanguage, "languages", data.get("languages") or []
    )

    for record in data.get("education") or []:
        session.add(
            TutorEducation(
                tutor_id=tutor.id,
                institution=str(record.get("institution") or ""),
                degree=str(record.get("degree") or ""),
                field=str(record.get("field") or ""),
                graduation_year=_as_int(record.get("graduation_year")),
            )
        )

    for slot in data.get("availability") or []:
        session.add(
            TutorAvailability(
                tutor_id=tutor.id,
                day=str(slot.get("day") or ""),
                start_time=str(slot.get("start_time") or ""),
                end_time=str(slot.get("end_time") or ""),
                timezone=str(slot.get("timezone") or tutor.timezone or "UTC"),
            )
        )

    for document in data.get("documents") or []:
        session.add(
            TutorDocument(
                tutor_id=tutor.id,
                document_type=str(document.get("document_type") or "SUPPORTING"),
                telegram_file_id=document.get("telegram_file_id"),
                telegram_file_unique_id=document.get("telegram_file_unique_id"),
                telegram_message_id=_as_int(document.get("telegram_message_id")),
                storage_chat_id=_as_int(document.get("storage_chat_id")),
            )
        )

    session.flush()
    session.refresh(tutor)
    logger.info(
        "Tutor application submitted",
        extra={"tdr_id": tutor.public_tutor_id, "country": tutor.country},
    )
    return tutor


def get_tutor_by_telegram_id(session: Session, telegram_user_id: int) -> Tutor | None:
    return session.execute(
        select(Tutor).where(Tutor.telegram_user_id == int(telegram_user_id))
    ).scalar_one_or_none()


def get_tutor_by_tdr_id(session: Session, tdr_id: str) -> Tutor | None:
    return session.execute(
        select(Tutor).where(Tutor.public_tutor_id == str(tdr_id).strip().upper())
    ).scalar_one_or_none()


def get_tutor_by_id(session: Session, tutor_pk: int) -> Tutor | None:
    return session.get(Tutor, tutor_pk)


def set_storage_references(
    session: Session,
    tutor: Tutor,
    chat_id: int | None,
    message_id: int | None,
) -> None:
    tutor.telegram_storage_chat_id = chat_id
    tutor.telegram_storage_message_id = message_id


def update_tutor_status(
    session: Session, tdr_id: str, status: TutorStatus | str
) -> Tutor | None:
    """Change only the status of a tutor (Property 9).

    Returns the updated tutor or ``None`` when the TDR ID is unknown.
    """
    value = str(status).upper()
    if value not in {s.value for s in TutorStatus}:
        raise ValueError(f"Unknown tutor status: {status!r}")
    tutor = get_tutor_by_tdr_id(session, tdr_id)
    if tutor is None:
        logger.warning("Status update for unknown tutor reference")
        return None
    tutor.status = value
    tutor.updated_at = utcnow()
    session.flush()
    logger.info("Tutor status changed to %s", value, extra={"tdr_id": tutor.public_tutor_id})
    return tutor


def update_tutor_fields(session: Session, tdr_id: str, values: dict[str, Any]) -> Tutor | None:
    tutor = get_tutor_by_tdr_id(session, tdr_id)
    if tutor is None:
        return None
    for key, value in values.items():
        if hasattr(tutor, key):
            setattr(tutor, key, value)
    tutor.updated_at = utcnow()
    session.flush()
    return tutor


def record_document_reference(
    session: Session,
    tutor: Tutor,
    document_type: str,
    file_id: str | None = None,
    file_unique_id: str | None = None,
    message_id: int | None = None,
    chat_id: int | None = None,
) -> TutorDocument:
    document = TutorDocument(
        tutor_id=tutor.id,
        document_type=document_type,
        telegram_file_id=file_id,
        telegram_file_unique_id=file_unique_id,
        telegram_message_id=message_id,
        storage_chat_id=chat_id,
    )
    session.add(document)
    session.flush()
    return document


def search_tutors(
    session: Session, filters: TutorFilters | dict[str, Any] | None = None
) -> list[Tutor]:
    """Return tutors matching every supplied filter (Property 6).

    Filters combine with AND semantics; string comparisons are case
    insensitive.
    """
    if filters is None:
        filters = TutorFilters()
    if isinstance(filters, dict):
        filters = TutorFilters(**{k: v for k, v in filters.items() if k in TutorFilters._fields})

    stmt = select(Tutor)
    if filters.status:
        stmt = stmt.where(Tutor.status == str(filters.status).upper())

    if filters.subject:
        stmt = stmt.where(
            Tutor.id.in_(
                select(TutorSubject.tutor_id).where(
                    TutorSubject.subject.ilike(f"%{filters.subject}%")
                )
            )
        )
    if filters.level:
        stmt = stmt.where(
            Tutor.id.in_(
                select(TutorLevel.tutor_id).where(
                    TutorLevel.level.ilike(f"%{filters.level}%")
                )
            )
        )
    if filters.country:
        stmt = stmt.where(Tutor.country.ilike(f"%{filters.country}%"))
    if filters.mode:
        stmt = stmt.where(Tutor.teaching_mode == str(filters.mode).upper())
    if filters.language:
        stmt = stmt.where(
            Tutor.id.in_(
                select(TutorLanguage.tutor_id).where(
                    TutorLanguage.language.ilike(f"%{filters.language}%")
                )
            )
        )
    if filters.min_etb is not None:
        stmt = stmt.where(Tutor.etb_rate >= filters.min_etb)
    if filters.max_etb is not None:
        stmt = stmt.where(Tutor.etb_rate <= filters.max_etb)
    if filters.min_usd is not None:
        stmt = stmt.where(Tutor.usd_rate >= filters.min_usd)
    if filters.max_usd is not None:
        stmt = stmt.where(Tutor.usd_rate <= filters.max_usd)
    if filters.search:
        pattern = f"%{filters.search}%"
        stmt = stmt.where(
            Tutor.name.ilike(pattern)
            | Tutor.display_name.ilike(pattern)
            | Tutor.city.ilike(pattern)
        )

    stmt = stmt.order_by(Tutor.id)
    return list(session.execute(stmt).scalars().all())


def list_tutors(session: Session, status: str | None = None) -> list[Tutor]:
    stmt = select(Tutor).order_by(Tutor.id)
    if status:
        stmt = stmt.where(Tutor.status == str(status).upper())
    return list(session.execute(stmt).scalars().all())


def distinct_values(session: Session, column_name: str) -> list[str]:
    """Distinct values for ``countries``/``subjects``/``levels``/``languages``.

    Only values referenced by VERIFIED tutors are returned (Requirement 10.8).
    """
    if column_name == "countries":
        stmt = select(Tutor.country).where(Tutor.status == "VERIFIED")
        return _clean_strings(row[0] for row in session.execute(stmt).all())

    mapping: dict[str, tuple[Any, Any]] = {
        "subjects": (TutorSubject, TutorSubject.subject),
        "levels": (TutorLevel, TutorLevel.level),
        "languages": (TutorLanguage, TutorLanguage.language),
    }
    model, column = mapping[column_name]
    verified = select(Tutor.id).where(Tutor.status == "VERIFIED")
    stmt = select(column).where(model.tutor_id.in_(verified)).distinct()
    return _clean_strings(row[0] for row in session.execute(stmt).all())


def _clean_strings(values: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        text = str(value).strip()
        if text:
            seen.setdefault(text, None)
    return sorted(seen)


def statistics(session: Session) -> dict[str, int]:
    """Aggregate counters used by the admin statistics view."""
    counts: dict[str, int] = {str(status): 0 for status in TutorStatus}
    for status, total in session.execute(
        select(Tutor.status, func.count()).group_by(Tutor.status)
    ).all():
        counts[str(status).upper()] = int(total)
    counts["total_tutors"] = int(session.execute(_count_from(Tutor)).scalar_one())
    counts["total_student_requests"] = int(
        session.execute(_count_from(StudentRequest)).scalar_one()
    )
    counts["total_support_tickets"] = int(
        session.execute(_count_from(SupportTicket)).scalar_one()
    )
    return counts


def _count_from(model: type):
    return select(func.count()).select_from(model)


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "TDR_PATTERN",
    "TutorValidationError",
    "create_tutor",
    "distinct_values",
    "format_tdr_id",
    "generate_tdr_id",
    "get_tutor_by_telegram_id",
    "get_tutor_by_tdr_id",
    "list_tutors",
    "record_document_reference",
    "search_tutors",
    "set_storage_references",
    "statistics",
    "tdr_sequence",
    "update_tutor_fields",
    "update_tutor_status",
    "validate_tutor_data",
]