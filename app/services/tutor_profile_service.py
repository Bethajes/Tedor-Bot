"""Structured tutor profile service for the Ethiopian onboarding flow.

The legacy 26-step flow asks for hourly rates, availability grids and a
currency, because it also serves tutors outside Ethiopia. The Ethiopian flow
does not: pricing belongs to the job side, so those columns stay ``NULL``.

That creates a second problem this module solves. ``tutors.email`` and
``tutors.city`` are ``NOT NULL`` with no default, but the new flow never asks
for an email, and a tutor may name tutoring areas without naming a city. Rather
than inventing values, everything unavailable is stored as the empty string —
the honest representation of *not provided* — and
:func:`missing_profile_fields` reports it so the admin sees an incomplete
profile instead of a fabricated one.

Three principles run through this module:

* **Nothing is invented.** No default for a gender, an age, a CGPA or a city.
  :func:`missing_profile_fields` exists precisely because these gaps are real
  and must stay visible.
* **Completion is not quality.** :func:`profile_completion_percentage`
  measures how much of the form was filled in. It is never fed into the matching
  score — a tutor who skipped the optional voice recording is not a weaker tutor.
* **The 50-member rule stays separate.** ``member_referral_status`` is set once
  at creation and is never part of profile completeness or matching.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Final, Iterable, Sequence

from sqlalchemy.orm import Session

from app.config import settings
from app.enums import (
    ACADEMIC_DOCUMENT_TYPES,
    DocumentType,
    EducationLevel,
    EntranceExamType,
    MemberReferralStatus,
)
from app.models import (
    Tutor,
    TutorDocument,
    TutorEducation,
    TutorExperience,
    TutorGrade,
    TutorLanguage,
    TutorLocation,
    TutorSubject,
    utcnow,
)
from app.services import location_service
from app.services.tutor_service import TutorValidationError, generate_tdr_id

logger = logging.getLogger(__name__)

#: Ethiopia is implied by the flow itself, so it is stored rather than asked.
ETHIOPIA: Final[str] = "Ethiopia"

#: Same rule the bot's phone validator applies, re-declared here so the service
#: layer stays importable without pulling in ``telegram``.
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-()]{6,19}$")

#: Accepted document uploads: PDF plus the image formats Telegram delivers.
ALLOWED_DOCUMENT_EXTENSIONS: Final[tuple[str, ...]] = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
)

ALLOWED_DOCUMENT_MIME_PREFIXES: Final[tuple[str, ...]] = ("application/pdf", "image/")

#: Document types this service will store. Anything else is a programming error
#: rather than bad user input.
_KNOWN_DOCUMENT_TYPES: Final[frozenset[str]] = frozenset(
    [str(item) for item in DocumentType]
)


class ProfileValidationError(TutorValidationError):
    """A tutor profile value failed validation.

    Subclasses :class:`app.services.tutor_service.TutorValidationError` so the
    existing handler ``except`` clauses catch it unchanged.
    """


# ---------------------------------------------------------------------------
# field validators (specification section 23)
# ---------------------------------------------------------------------------


def _as_number(value: Any) -> float | None:
    """Coerce user input to a float, or ``None`` when there is no value."""
    if value in (None, "", "—"):
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def validate_age(value: Any) -> int | None:
    """Age must be a whole number inside the configured adult range."""
    if value in (None, ""):
        return None
    number = _as_number(value)
    if number is None or number != int(number):
        raise ProfileValidationError("Please enter your age as a whole number.")
    age = int(number)
    if not settings.tutor_min_age <= age <= settings.tutor_max_age:
        raise ProfileValidationError(
            f"Age must be between {settings.tutor_min_age} and {settings.tutor_max_age}."
        )
    return age


def validate_english_proficiency(value: Any) -> int | None:
    """English proficiency is a bounded 1-10 scale, never free text."""
    if value in (None, ""):
        return None
    number = _as_number(value)
    if number is None or number != int(number):
        raise ProfileValidationError("English level must be a whole number from 1 to 10.")
    level = int(number)
    if not (
        settings.english_proficiency_min
        <= level
        <= settings.english_proficiency_max
    ):
        raise ProfileValidationError(
            "English level must be between "
            f"{settings.english_proficiency_min} and {settings.english_proficiency_max}."
        )
    return level


def validate_cgpa(value: Any) -> float | None:
    """CGPA must fit the configured grading system."""
    if value in (None, ""):
        return None
    number = _as_number(value)
    if number is None:
        raise ProfileValidationError("Please enter your CGPA as a number.")
    if not 0.0 <= number <= settings.cgpa_max:
        raise ProfileValidationError(
            f"Your CGPA must be between 0 and {settings.cgpa_max:g}."
        )
    return round(number, 2)


def validate_entrance_exam_score(value: Any, max_score: Any = None) -> float | None:
    """Entrance score must be numeric and within its own maximum.

    Scores from different examination systems are never compared raw, so the
    maximum is validated here and stored alongside the score.
    """
    if value in (None, ""):
        return None
    number = _as_number(value)
    if number is None:
        raise ProfileValidationError("Please enter your entrance exam score as a number.")
    if number < 0:
        raise ProfileValidationError("Your entrance exam score cannot be negative.")
    ceiling = _as_number(max_score)
    if ceiling and number > ceiling:
        raise ProfileValidationError(
            f"Your score cannot be higher than the maximum of {ceiling:g}."
        )
    return number


def validate_entrance_exam_max_score(value: Any) -> float | None:
    if value in (None, ""):
        return None
    number = _as_number(value)
    if number is None or number <= 0:
        raise ProfileValidationError("The maximum score must be a number above zero.")
    return number


def validate_phone(value: Any) -> str:
    """Phone numbers are validated, not merely stored."""
    text = str(value or "").strip()
    if not text:
        raise ProfileValidationError("A phone number is required.")
    if not PHONE_RE.match(text):
        raise ProfileValidationError(
            "Please enter a valid phone number, for example +251911234567."
        )
    return text


def validate_entrance_exam_type(value: Any) -> str | None:
    """Known examination systems are enforced; ``OTHER`` passes through."""
    if value in (None, ""):
        return None
    text = str(value).strip().upper().replace(" ", "_").replace("-", "_")
    known = {str(item) for item in EntranceExamType}
    if text not in known:
        raise ProfileValidationError(
            "Unknown entrance examination type. Choose "
            + ", ".join(sorted(known))
            + "."
        )
    return text


def validate_education_level(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().upper().replace(" ", "_").replace("-", "_")
    known = {str(item) for item in EducationLevel}
    if text not in known:
        raise ProfileValidationError(
            "Unknown education level. Choose " + ", ".join(sorted(known)) + "."
        )
    return text


def validate_gender(value: Any) -> str | None:
    """Gender is optional and never inferred; stored as typed."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if len(text) > 20:
        raise ProfileValidationError("Please enter a shorter value for gender.")
    return text


def validate_text(value: Any, label: str, *, maximum: int = 500) -> str | None:
    """Trimmed free text, length-capped; ``None`` when nothing was supplied."""
    if value in (None, ""):
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if len(text) > maximum:
        raise ProfileValidationError(f"{label} is too long — keep it under {maximum} characters.")
    return text


def validate_year(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    number = _as_number(value)
    if number is None or number != int(number):
        raise ProfileValidationError(f"{label} must be a whole number, e.g. 2021.")
    year = int(number)
    if not 1950 <= year <= utcnow().year + 6:
        raise ProfileValidationError(f"{label} looks out of range — please check it.")
    return year


def validate_document_file(file_name: Any, mime_type: Any = None) -> str | None:
    """Accept PDFs and images only (specification section 23).

    Returns the lower-cased extension, or ``None`` when no usable name was
    given. Raises for a disallowed type so the tutor is told rather than
    silently losing their upload.
    """
    name = str(file_name or "").strip()
    mime = str(mime_type or "").strip().lower()
    if name:
        extension = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
            raise ProfileValidationError(
                "Please upload a PDF or an image (JPG, PNG or WEBP)."
            )
        return extension
    if mime and not mime.startswith(ALLOWED_DOCUMENT_MIME_PREFIXES):
        raise ProfileValidationError(
            "Please upload a PDF or an image (JPG, PNG or WEBP)."
        )
    return None


# ---------------------------------------------------------------------------
# collection validators
# ---------------------------------------------------------------------------


def _clean_list(values: Any) -> list[str]:
    if values in (None, ""):
        return []
    if isinstance(values, str):
        values = [part for part in re.split(r"[,\n;/]", values)]
    cleaned: list[str] = []
    for item in values:
        if item is None:
            continue
        text = " ".join(str(item).split())
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def validate_locations(values: Any, *, teaching_mode: str = "IN_PERSON") -> list[str]:
    """At least one tutoring area for in-person teaching (section 23)."""
    locations = _clean_list(values)
    if not locations and str(teaching_mode).upper() != "ONLINE":
        raise ProfileValidationError(
            "Please choose at least one area you can tutor in."
        )
    return locations


def validate_subjects(values: Any) -> list[str]:
    subjects = _clean_list(values)
    if not subjects:
        raise ProfileValidationError("Please choose at least one subject you can teach.")
    return subjects


def validate_grades(values: Any) -> list[str]:
    grades = _clean_list(values)
    if not grades:
        raise ProfileValidationError("Please choose at least one grade level.")
    return grades


# ---------------------------------------------------------------------------
# city derivation
# ---------------------------------------------------------------------------


def infer_city(locations: Sequence[str], fallback: Any = None) -> str:
    """Derive the city from the tutoring areas the tutor actually named.

    Returns ``""`` when nothing can be derived — an empty city is honest, a
    guessed one is not. This is the only place a value is inferred, and only
    from data the tutor supplied.
    """
    if fallback:
        return " ".join(str(fallback).split())
    for location in locations:
        spec = location_service.LOCATIONS.get(location_service.normalize_location(location) or "")
        if spec is None:
            continue
        if spec.zone == location_service.LocationZone.CITY_WIDE:
            return spec.name
        if spec.zone != location_service.LocationZone.OUTSIDE:
            # Any Addis sub-area implies Addis Ababa.
            return "Addis Ababa"
    return ""


def derive_teaching_mode(locations: Sequence[str], explicit: Any = None) -> str:
    """Teaching mode, derived from the tutoring areas when not supplied.

    A tutor who lists areas is willing to teach in person there; one with no
    areas can only take online students. Callers may always state it outright.
    """
    if explicit:
        return str(explicit).strip().upper()
    return "IN_PERSON" if locations else "ONLINE"


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------


def create_ethiopian_tutor(session: Session, data: dict[str, Any]) -> Tutor:
    """Create a tutor from the Ethiopian onboarding payload.

    Deliberately separate from :func:`app.services.tutor_service.create_tutor`:
    the legacy path requires email, city, teaching mode and rates, and
    ``tests/test_tdr_id.py`` pins that behaviour. This path requires only what
    the new flow actually collects, and never asks for a price.

    Values absent from ``data`` are stored as ``NULL`` or ``""`` — nothing is
    invented, and :func:`missing_profile_fields` reports the gap.
    """
    telegram_user_id = data.get("telegram_user_id")
    if not telegram_user_id:
        raise ProfileValidationError("Missing required tutor fields: telegram_user_id")

    full_name = validate_text(data.get("full_name"), "Full name", maximum=160)
    if not full_name:
        raise ProfileValidationError("Missing required tutor fields: full_name")

    phone = validate_phone(data.get("phone"))
    locations = validate_locations(
        data.get("locations") or data.get("tutoring_locations"),
        teaching_mode=str(data.get("teaching_mode") or "IN_PERSON"),
    )
    subjects = validate_subjects(data.get("subjects"))
    grades = validate_grades(data.get("grades"))

    entrance_max = validate_entrance_exam_max_score(
        data.get("entrance_exam_max_score") or data.get("entrance_exam_max")
    )
    entrance_score = validate_entrance_exam_score(
        data.get("entrance_exam_score") or data.get("entrance_score"), entrance_max
    )
    teaching_mode = derive_teaching_mode(locations, data.get("teaching_mode"))

    experience_years = _optional_number(data.get("teaching_experience_years"))
    tutor = Tutor(
        public_tutor_id=generate_tdr_id(session),
        telegram_user_id=int(telegram_user_id),
        name=full_name,
        # The new flow asks for one name only; the public display name falls
        # back to it rather than being asked twice.
        display_name=validate_text(data.get("display_name"), "Display name", maximum=120)
        or full_name,
        phone=phone,
        # Not collected by this flow. Empty, not invented.
        email=validate_text(data.get("email"), "Email", maximum=160) or "",
        country=ETHIOPIA,
        city=infer_city(locations, data.get("city")),
        bio=validate_text(data.get("bio"), "Biography") or "",
        # Pricing belongs to the job side, so both rate columns stay NULL.
        etb_rate=None,
        usd_rate=None,
        experience_years=int(experience_years or 0),
        timezone=str(data.get("timezone") or "UTC").strip() or "UTC",
        teaching_mode=teaching_mode,
        status=str(data.get("status") or "PENDING").upper(),
        storage_status=str(data.get("storage_status") or "PENDING").upper(),
        # -- personal --------------------------------------------------
        gender=validate_gender(data.get("gender")),
        age=validate_age(data.get("age")),
        current_address=validate_text(
            data.get("current_address") or data.get("address"),
            "Current address",
            maximum=240,
        ),
        # -- teaching capability ---------------------------------------
        teaching_experience_years=(
            int(experience_years) if experience_years is not None else None
        ),
        teaching_experience_description=validate_text(
            data.get("teaching_experience_description"),
            "Experience description",
        ),
        # -- English ---------------------------------------------------
        english_proficiency=validate_english_proficiency(
            data.get("english_proficiency")
        ),
        english_voice_file_id=_clean_or_none(data.get("english_voice_file_id")),
        english_voice_message_id=_optional_int(data.get("english_voice_message_id")),
        # -- academic --------------------------------------------------
        university=validate_text(data.get("university"), "University", maximum=200),
        department=validate_text(data.get("department"), "Department", maximum=160),
        education_level=validate_education_level(data.get("education_level")),
        university_year=validate_year(data.get("university_year"), "University year"),
        cgpa=validate_cgpa(data.get("cgpa")),
        entrance_exam_score=entrance_score,
        entrance_exam_year=validate_year(data.get("entrance_exam_year"), "Exam year"),
        entrance_exam_type=validate_entrance_exam_type(data.get("entrance_exam_type")),
        entrance_exam_max_score=entrance_max,
        # -- verification ---------------------------------------------
        member_referral_status=str(MemberReferralStatus.NOT_COMPLETED),
    )
    session.add(tutor)
    session.flush()

    _replace_subjects(session, tutor, subjects)
    _replace_grades(session, tutor, grades)
    _replace_locations(session, tutor, locations)
    _replace_languages(session, tutor, _clean_list(data.get("languages")))

    institution = tutor.university
    if institution or data.get("degree") or data.get("field"):
        session.add(
            TutorEducation(
                tutor_id=tutor.id,
                institution=institution or "",
                degree=validate_text(data.get("degree"), "Degree", maximum=120) or "",
                field=tutor.department or "",
                graduation_year=(
                    tutor.university_year
                    if data.get("degree") and tutor.university_year
                    else None
                ),
            )
        )

    for record in data.get("experiences") or []:
        _add_experience_row(session, tutor.id, record)

    for document in data.get("documents") or []:
        record_document(
            session,
            tutor,
            document_type=document.get("document_type"),
            file_id=document.get("telegram_file_id"),
            file_unique_id=document.get("telegram_file_unique_id"),
            message_id=document.get("telegram_message_id"),
            file_name=document.get("file_name"),
            mime_type=document.get("mime_type"),
        )

    session.flush()
    session.refresh(tutor)
    logger.info(
        "Ethiopian tutor profile created",
        extra={
            "tdr_id": tutor.public_tutor_id,
            "locations": len(locations),
            "grades": len(grades),
        },
    )
    return tutor


# ---------------------------------------------------------------------------
# collection helpers
# ---------------------------------------------------------------------------


def _flush_and_refresh(session: Session, tutor: Tutor, *attributes: str) -> None:
    """Flush, then drop the given loaded collections so they reload.

    Every relationship on :class:`~app.models.Tutor` uses
    ``lazy="selectin"``, which loads it once per session. Rows inserted or
    deleted here would otherwise leave the in-memory collection stale, so a
    caller reading ``tutor.documents`` immediately afterwards would see the
    pre-change state. Expiring just these attributes keeps the freshly written
    rows visible without reloading the whole object.
    """
    session.flush()
    for attribute in attributes:
        session.expire(tutor, [attribute])


def _replace_subjects(session: Session, tutor: Tutor, values: Sequence[str]) -> None:
    """Subjects share the existing ``TutorSubject`` table with the legacy flow."""
    _sync_collection(session, tutor, tutor.subjects, values, "subject", TutorSubject)
    _flush_and_refresh(session, tutor, "subjects")


def _replace_grades(session: Session, tutor: Tutor, values: Sequence[str]) -> None:
    _sync_collection(session, tutor, tutor.grades, values, "grade", TutorGrade)
    _flush_and_refresh(session, tutor, "grades")


def _replace_languages(session: Session, tutor: Tutor, values: Sequence[str]) -> None:
    _sync_collection(session, tutor, tutor.languages, values, "language", TutorLanguage)
    _flush_and_refresh(session, tutor, "languages")


def _sync_collection(
    session: Session,
    tutor: Tutor,
    existing: list,
    values: Sequence[str],
    column: str,
    model: type,
) -> None:
    """Bring a simple string collection in line with ``values``.

    Applies the difference rather than deleting everything and re-inserting.
    A blanket delete/re-insert breaks on ``UNIQUE(tutor_id, <column>)`` when a
    value is unchanged, because the insert is flushed before the delete and the
    constraint is violated — which is exactly what happens when a tutor
    re-submits the subjects they already had.

    Rows that survive keep their primary keys, so nothing downstream has to
    notice that a "replace" happened at all.
    """
    wanted = list(dict.fromkeys(values))
    wanted_set = set(wanted)
    present = {getattr(item, column) for item in existing}

    for item in list(existing):
        if getattr(item, column) not in wanted_set:
            session.delete(item)

    for value in wanted:
        if value not in present:
            session.add(model(tutor_id=tutor.id, **{column: value}))


def _replace_locations(session: Session, tutor: Tutor, values: Sequence[str]) -> None:
    """Store each area with its canonical key so matching can compare them.

    De-duplicated on the *canonical key*, not the raw spelling: a tutor who
    answers with "Bole", "bole" and "Bole." has named one area, and three rows
    for it would double-count their reach.
    """
    wanted: dict[str, str] = {}
    for value in values:
        key = location_service.normalize_location(value) or ""
        wanted.setdefault(key, value)

    existing = list(tutor.tutoring_locations)
    present = {item.normalized_location for item in existing}

    for item in existing:
        if item.normalized_location not in wanted:
            session.delete(item)

    for key, value in wanted.items():
        if key not in present:
            session.add(
                TutorLocation(
                    tutor_id=tutor.id,
                    location=value,
                    normalized_location=key,
                )
            )
    _flush_and_refresh(session, tutor, "tutoring_locations")


def set_locations(session: Session, tutor: Tutor, values: Any) -> list[str]:
    """Replace the tutoring areas; used by the ``/complete_profile`` flow."""
    locations = validate_locations(values, teaching_mode=tutor.teaching_mode or "IN_PERSON")
    _replace_locations(session, tutor, locations)
    if not tutor.city:
        tutor.city = infer_city(locations)
    tutor.updated_at = utcnow()
    session.flush()
    return locations


def set_grades(session: Session, tutor: Tutor, values: Any) -> list[str]:
    grades = validate_grades(values)
    _replace_grades(session, tutor, grades)
    tutor.updated_at = utcnow()
    session.flush()
    return grades


def set_subjects(session: Session, tutor: Tutor, values: Any) -> list[str]:
    subjects = validate_subjects(values)
    _replace_subjects(session, tutor, subjects)
    tutor.updated_at = utcnow()
    session.flush()
    return subjects


def set_english_proficiency(session: Session, tutor: Tutor, value: Any) -> int | None:
    """Store the self-rated 1-10 level after validation."""
    tutor.english_proficiency = validate_english_proficiency(value)
    tutor.updated_at = utcnow()
    session.flush()
    return tutor.english_proficiency


def record_english_voice(
    session: Session,
    tutor: Tutor,
    file_id: str,
    message_id: int | None = None,
    *,
    duration: int | None = None,
) -> TutorDocument:
    """Store the optional 30-60 second English introduction.

    The recording is *supporting evidence for admin review*. Storing it never
    asserts a CEFR level, and it is never read by the matching engine — a tutor
    who sends nothing here is not penalised.
    """
    if not file_id:
        raise ProfileValidationError("The voice recording could not be read. Please retry.")

    tutor.english_voice_file_id = str(file_id)
    tutor.english_voice_message_id = _optional_int(message_id)
    tutor.updated_at = utcnow()

    existing = (
        session.query(TutorDocument)
        .filter(
            TutorDocument.tutor_id == tutor.id,
            TutorDocument.document_type == str(DocumentType.ENGLISH_VOICE),
        )
        .one_or_none()
    )
    if existing is not None:
        existing.telegram_file_id = str(file_id)
        existing.telegram_message_id = tutor.english_voice_message_id
        existing.uploaded_at = utcnow()
        _flush_and_refresh(session, tutor, "documents")
        return existing

    document = TutorDocument(
        tutor_id=tutor.id,
        document_type=str(DocumentType.ENGLISH_VOICE),
        telegram_file_id=str(file_id),
        telegram_message_id=tutor.english_voice_message_id,
    )
    session.add(document)
    _flush_and_refresh(session, tutor, "documents")
    logger.info(
        "English voice introduction stored",
        extra={
            "tdr_id": tutor.public_tutor_id,
            "duration_seconds": duration,
        },
    )
    return document


def record_document(
    session: Session,
    tutor: Tutor,
    document_type: Any,
    file_id: str | None = None,
    file_unique_id: str | None = None,
    message_id: int | None = None,
    *,
    file_name: str | None = None,
    mime_type: str | None = None,
    storage_chat_id: int | None = None,
) -> TutorDocument:
    """Store one academic document, validating its file type first.

    Re-uploading the same document type replaces the previous reference, which
    mirrors how :mod:`app.services.storage_service` archives tutor records.
    """
    kind = str(document_type or "").strip().upper()
    if kind not in _KNOWN_DOCUMENT_TYPES:
        raise ProfileValidationError(f"Unknown document type: {document_type!r}")

    validate_document_file(file_name, mime_type)

    # Queried rather than read off ``tutor.documents``: that relationship is
    # only loaded once per session, so two uploads of the same type inside one
    # session would otherwise both insert and leave a duplicate row.
    existing = (
        session.query(TutorDocument)
        .filter(TutorDocument.tutor_id == tutor.id, TutorDocument.document_type == kind)
        .one_or_none()
    )
    if existing is not None:
        existing.telegram_file_id = file_id or existing.telegram_file_id
        existing.telegram_file_unique_id = file_unique_id or existing.telegram_file_unique_id
        existing.telegram_message_id = _optional_int(message_id) or existing.telegram_message_id
        existing.storage_chat_id = storage_chat_id or existing.storage_chat_id
        existing.uploaded_at = utcnow()
        _flush_and_refresh(session, tutor, "documents")
        return existing

    document = TutorDocument(
        tutor_id=tutor.id,
        document_type=kind,
        telegram_file_id=file_id,
        telegram_file_unique_id=file_unique_id,
        telegram_message_id=_optional_int(message_id),
        storage_chat_id=_optional_int(storage_chat_id),
    )
    session.add(document)
    _flush_and_refresh(session, tutor, "documents")
    return document


def _add_experience_row(session: Session, tutor_id: int, record: dict[str, Any]) -> None:
    years = _optional_number(record.get("years")) or 0.0
    session.add(
        TutorExperience(
            tutor_id=tutor_id,
            years=float(years),
            description=validate_text(record.get("description"), "Description") or "",
            subjects=", ".join(_clean_list(record.get("subjects"))),
            grades=", ".join(_clean_list(record.get("grades"))),
            institutions=", ".join(_clean_list(record.get("institutions"))),
        )
    )


def add_experience(session: Session, tutor: Tutor, record: dict[str, Any]) -> TutorExperience:
    """Append one prior teaching role used for relevance scoring."""
    _add_experience_row(session, tutor.id, record)
    years = _optional_number(record.get("years"))
    if years is not None:
        total = float(tutor.teaching_experience_years or 0.0) + years
        tutor.teaching_experience_years = int(total)
        tutor.experience_years = int(total)
    tutor.updated_at = utcnow()
    _flush_and_refresh(session, tutor, "experiences")
    # Ordered newest-first, so the row just written comes back rather than the
    # first of several.
    return (
        session.query(TutorExperience)
        .filter(TutorExperience.tutor_id == tutor.id)
        .order_by(TutorExperience.id.desc())
        .first()
    )


def update_profile_fields(session: Session, tutor: Tutor, values: dict[str, Any]) -> Tutor:
    """Apply validated scalar fields during ``/complete_profile``.

    Only the academic, English and personal scalars are accepted here;
    collections go through :func:`set_locations`, :func:`set_grades` and
    :func:`set_subjects` so their validation always runs.
    """
    validators = {
        "gender": validate_gender,
        "age": validate_age,
        "current_address": lambda v: validate_text(v, "Current address", maximum=240),
        "university": lambda v: validate_text(v, "University", maximum=200),
        "department": lambda v: validate_text(v, "Department", maximum=160),
        "education_level": validate_education_level,
        "university_year": lambda v: validate_year(v, "University year"),
        "cgpa": validate_cgpa,
        "entrance_exam_year": lambda v: validate_year(v, "Exam year"),
        "entrance_exam_type": validate_entrance_exam_type,
        "english_proficiency": validate_english_proficiency,
        "teaching_experience_years": _optional_int,
        "teaching_experience_description": lambda v: validate_text(
            v, "Experience description"
        ),
    }

    for key, value in values.items():
        if key == "entrance_exam_max_score":
            tutor.entrance_exam_max_score = validate_entrance_exam_max_score(value)
            continue
        if key == "entrance_exam_score":
            tutor.entrance_exam_score = validate_entrance_exam_score(
                value, tutor.entrance_exam_max_score
            )
            continue
        validator = validators.get(key)
        if validator is None:
            continue
        setattr(tutor, key, validator(value))

    tutor.updated_at = utcnow()
    session.flush()
    return tutor


def backfill_education_from_legacy(session: Session, tutor: Tutor) -> dict[str, Any]:
    """Copy an imported tutor's legacy education record into the new columns.

    Tutors imported before the structured fields existed hold their institution,
    course and graduation year in the education table while the matching columns
    stay empty. Reusing that record keeps ``/complete_profile`` from asking for
    an answer the tutor already gave, and makes the completeness report agree
    with the conversation. Only empty columns are filled, so an explicit
    structured answer always wins.
    """
    record = tutor.education[0] if tutor.education else None
    if record is None:
        return {}

    candidates = {
        "university": record.institution,
        "department": record.field or record.degree,
        "university_year": record.graduation_year,
    }
    filled = {
        key: value
        for key, value in candidates.items()
        if value and not getattr(tutor, key, None)
    }
    if not filled:
        return {}

    update_profile_fields(session, tutor, filled)
    return filled


def set_member_referral_status(
    session: Session, tutor: Tutor, status: Any, verified_by: int | None = None
) -> Tutor:
    """Move the separate 50-member requirement forward.

    Kept apart from teaching qualifications on purpose: it is an onboarding
    condition the admin confirms, never a matching signal.
    """
    try:
        value = MemberReferralStatus(str(status).strip().upper())
    except ValueError:
        raise ProfileValidationError(f"Unknown member referral status: {status!r}") from None

    tutor.member_referral_status = str(value)
    if value == MemberReferralStatus.VERIFIED:
        tutor.verified_by = verified_by
    tutor.updated_at = utcnow()
    session.flush()
    return tutor


# ---------------------------------------------------------------------------
# completeness (specification sections 26 and 27)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileField:
    """One field of the structured profile, for reporting only."""

    key: str
    label: str
    #: Share of the completeness score, in percent.
    weight: int
    #: Does the field hold a collection rather than a scalar?
    collection: bool = False
    #: The mapped attribute, when it differs from :attr:`key`.
    attribute: str = ""


#: Ordered by the priority the enrichment flow asks in (section 26). The
#: weights are how much of the *completeness* score each field is worth; they
#: have nothing to do with tutor quality.
PROFILE_FIELDS: Final[tuple[ProfileField, ...]] = (
    # ``attribute`` differs from ``key`` for the collection: the relationship
    # is ``tutoring_locations`` while the flow calls the step "locations".
    ProfileField(
        "locations", "Tutoring locations", 18, collection=True, attribute="tutoring_locations"
    ),
    ProfileField("grades", "Grades", 14, collection=True, attribute="grades"),
    ProfileField("subjects", "Subjects", 14, collection=True, attribute="subjects"),
    ProfileField("english_proficiency", "English proficiency", 12),
    ProfileField("entrance_exam_score", "Entrance exam result", 10),
    ProfileField("university", "University", 8),
    ProfileField("experience", "Tutoring experience", 8),
    ProfileField("documents", "Academic documents", 8),
    ProfileField("age", "Age", 4),
    ProfileField("current_address", "Current address", 4),
)


def _field_present(tutor: Tutor, field: ProfileField) -> bool:
    return bool(getattr(tutor, field.attribute or field.key, None))


def _has_experience(tutor: Tutor) -> bool:
    return bool(tutor.experiences) or bool(tutor.teaching_experience_years)


def _has_academic_documents(tutor: Tutor) -> bool:
    """Any academic document counts; the set differs by education level."""
    academic = set(ACADEMIC_DOCUMENT_TYPES)
    return any(document.document_type in academic for document in tutor.documents)


#: ``experience`` and ``documents`` are composite, so they need their own rules.
_COMPOSITE_PRESENT: Final[dict[str, Any]] = {
    "experience": _has_experience,
    "documents": _has_academic_documents,
}


def _is_present(tutor: Tutor, field: ProfileField) -> bool:
    checker = _COMPOSITE_PRESENT.get(field.key)
    if checker is not None:
        return bool(checker(tutor))
    return _field_present(tutor, field)


def missing_profile_fields(tutor: Tutor) -> list[str]:
    """Field keys still missing, in the order the enrichment flow asks them."""
    return [
        field.key
        for field in PROFILE_FIELDS
        if not _is_present(tutor, field)
    ]


def missing_profile_labels(tutor: Tutor) -> list[str]:
    """Human readable version of :func:`missing_profile_fields`."""
    missing = set(missing_profile_fields(tutor))
    return [field.label for field in PROFILE_FIELDS if field.key in missing]


def profile_completion_percentage(tutor: Tutor) -> int:
    """How much of the structured profile the tutor has filled in, 0-100.

    A completeness measure only. It is never a quality score: an optional
    English voice recording is not counted, so two tutors with identical
    teaching capability and qualifications always score the same.
    """
    total = sum(field.weight for field in PROFILE_FIELDS)
    if total <= 0:  # pragma: no cover - defensive
        return 0
    earned = sum(field.weight for field in PROFILE_FIELDS if _is_present(tutor, field))
    return round(100 * earned / total)


def completion_report(tutor: Tutor) -> dict[str, Any]:
    """Percentage plus the outstanding fields, for the admin detail view."""
    missing = missing_profile_fields(tutor)
    return {
        "tdr_id": tutor.public_tutor_id,
        "percentage": profile_completion_percentage(tutor),
        "missing": missing,
        "missing_labels": missing_profile_labels(tutor),
        "complete": not missing,
    }


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _clean_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def academic_document_types_for(education_level: Any) -> tuple[str, ...]:
    """Which academic documents to ask for, given the education level.

    Conditional on purpose (section 4): an undergraduate is asked for a current
    university document, a master's graduate for their MSc. Never everything.
    """
    level = str(education_level or "").strip().upper()
    undergraduate = {
        str(DocumentType.CURRENT_UNIVERSITY_DOCUMENT),
        str(DocumentType.UNIVERSITY_TRANSCRIPT),
    }
    graduates = {
        str(DocumentType.BACHELORS_DEGREE),
        str(DocumentType.UNIVERSITY_TRANSCRIPT),
    }
    masters_graduates = {
        str(DocumentType.MSC_DEGREE),
        str(DocumentType.UNIVERSITY_TRANSCRIPT),
    }
    if level == str(EducationLevel.UNIVERSITY_STUDENT):
        return tuple(sorted(undergraduate))
    if level == str(EducationLevel.UNIVERSITY_GRADUATE):
        return tuple(sorted(graduates))
    if level == str(EducationLevel.MASTERS_STUDENT):
        return tuple(sorted(undergraduate | {str(DocumentType.MSC_DEGREE)}))
    if level == str(EducationLevel.MASTERS_GRADUATE):
        return tuple(sorted(masters_graduates))
    if level == str(EducationLevel.OTHER):
        return (str(DocumentType.OTHER_CERTIFICATE),)
    # No education level yet: only the universally applicable documents.
    return (
        str(DocumentType.SECONDARY_LEAVING_RESULT),
        str(DocumentType.UNIVERSITY_ENTRANCE_RESULT),
    )


def english_voice_duration_ok(duration_seconds: Any) -> bool:
    """Whether a voice clip is within the advertised 30-60 second window.

    Advisory only — the recording is stored either way, because refusing it
    would lose real evidence the admin asked to see.
    """
    seconds = _optional_number(duration_seconds)
    if seconds is None:
        return True
    return 30 <= seconds <= 60


def clean_values(values: Iterable[Any]) -> list[str]:
    """Public re-export of the shared list cleaner."""
    return _clean_list(values)


__all__ = [
    "ALLOWED_DOCUMENT_EXTENSIONS",
    "ETHIOPIA",
    "PROFILE_FIELDS",
    "ProfileField",
    "ProfileValidationError",
    "academic_document_types_for",
    "add_experience",
    "clean_values",
    "completion_report",
    "create_ethiopian_tutor",
    "derive_teaching_mode",
    "english_voice_duration_ok",
    "infer_city",
    "missing_profile_fields",
    "missing_profile_labels",
    "profile_completion_percentage",
    "record_document",
    "record_english_voice",
    "set_english_proficiency",
    "set_grades",
    "set_locations",
    "set_member_referral_status",
    "set_subjects",
    "update_profile_fields",
    "validate_age",
    "validate_cgpa",
    "validate_document_file",
    "validate_education_level",
    "validate_entrance_exam_max_score",
    "validate_entrance_exam_score",
    "validate_entrance_exam_type",
    "validate_gender",
    "validate_grades",
    "validate_locations",
    "validate_phone",
    "validate_subjects",
    "validate_year",
]