"""Domain enums and value objects shared across services."""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple


class TeachingMode(StrEnum):
    ONLINE = "ONLINE"
    IN_PERSON = "IN_PERSON"
    BOTH = "BOTH"


class TutorStatus(StrEnum):
    PENDING = "PENDING"
    UNDER_REVIEW = "UNDER_REVIEW"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"


class StorageStatus(StrEnum):
    PENDING = "PENDING"
    STORED = "STORED"
    STORAGE_FAILED = "STORAGE_FAILED"


class RequestStatus(StrEnum):
    NEW = "NEW"
    MATCHED = "MATCHED"
    CONTACTED = "CONTACTED"
    CLOSED = "CLOSED"


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    ANSWERED = "ANSWERED"
    CLOSED = "CLOSED"


class JobPostStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CLOSED = "CLOSED"


class Currency(StrEnum):
    ETB = "ETB"
    USD = "USD"


class EthiopianTutorStatus(StrEnum):
    """Verification lifecycle of the Ethiopian onboarding flow.

    A superset of :class:`TutorStatus`: the legacy 26-step flow has no document
    submission state, so ``DOCUMENTS_SUBMITTED`` exists only for tutors who walk
    the new flow. :data:`app.models.TUTOR_STATUSES` is the union of both enums.
    """

    PENDING = "PENDING"
    DOCUMENTS_SUBMITTED = "DOCUMENTS_SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"


class MemberReferralStatus(StrEnum):
    """State of the separate 50-member group requirement.

    Deliberately kept out of the tutor matching score: adding members is an
    onboarding condition, never evidence of teaching quality.
    """

    NOT_COMPLETED = "NOT_COMPLETED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    COMPLETED = "COMPLETED"
    VERIFIED = "VERIFIED"


class EducationLevel(StrEnum):
    """Highest / current education level offered during onboarding."""

    UNIVERSITY_STUDENT = "UNIVERSITY_STUDENT"
    UNIVERSITY_GRADUATE = "UNIVERSITY_GRADUATE"
    MASTERS_STUDENT = "MASTERS_STUDENT"
    MASTERS_GRADUATE = "MASTERS_GRADUATE"
    OTHER = "OTHER"


class EntranceExamType(StrEnum):
    """University entrance examination systems.

    Scores from different systems are not comparable, so the exam type is
    stored alongside the maximum score and normalisation happens before any
    comparison (see :mod:`app.services.matching_service`).
    """

    EHE = "EHE"
    ETHIOPIAN_ENTRANCE = "ETHIOPIAN_ENTRANCE"
    OTHER = "OTHER"


class CandidateStatus(StrEnum):
    """Admin-controlled lifecycle of a recommended candidate for a job."""

    RECOMMENDED = "RECOMMENDED"
    SHORTLISTED = "SHORTLISTED"
    CONTACTED = "CONTACTED"
    SELECTED = "SELECTED"
    REJECTED = "REJECTED"


class EnglishRequirement(StrEnum):
    """How much English a job needs; drives the English weight."""

    NONE = "NONE"
    BASIC = "BASIC"
    STANDARD = "STANDARD"
    HIGH = "HIGH"


class DocumentType(StrEnum):
    PROFILE_PHOTO = "PROFILE_PHOTO"
    CV = "CV"
    DEGREE = "DEGREE"
    CERTIFICATE = "CERTIFICATE"
    SUPPORTING = "SUPPORTING"
    # -- academic documents requested by the Ethiopian onboarding flow --------
    PRIMARY_LEAVING_RESULT = "PRIMARY_LEAVING_RESULT"
    GRADE_9_12_TRANSCRIPT = "GRADE_9_12_TRANSCRIPT"
    SECONDARY_LEAVING_RESULT = "SECONDARY_LEAVING_RESULT"
    UNIVERSITY_ENTRANCE_RESULT = "UNIVERSITY_ENTRANCE_RESULT"
    UNIVERSITY_TRANSCRIPT = "UNIVERSITY_TRANSCRIPT"
    CURRENT_UNIVERSITY_DOCUMENT = "CURRENT_UNIVERSITY_DOCUMENT"
    EXIT_EXAMINATION_RESULT = "EXIT_EXAMINATION_RESULT"
    BACHELORS_DEGREE = "BACHELORS_DEGREE"
    MSC_DEGREE = "MSC_DEGREE"
    OTHER_CERTIFICATE = "OTHER_CERTIFICATE"
    #: 30-60 second spoken English introduction; supporting evidence only.
    ENGLISH_VOICE = "ENGLISH_VOICE"


#: Academic documents only, in the order they are requested during onboarding.
ACADEMIC_DOCUMENT_TYPES: tuple[str, ...] = (
    DocumentType.PRIMARY_LEAVING_RESULT,
    DocumentType.GRADE_9_12_TRANSCRIPT,
    DocumentType.SECONDARY_LEAVING_RESULT,
    DocumentType.UNIVERSITY_ENTRANCE_RESULT,
    DocumentType.UNIVERSITY_TRANSCRIPT,
    DocumentType.CURRENT_UNIVERSITY_DOCUMENT,
    DocumentType.EXIT_EXAMINATION_RESULT,
    DocumentType.BACHELORS_DEGREE,
    DocumentType.MSC_DEGREE,
    DocumentType.OTHER_CERTIFICATE,
)


class SupportCategory(StrEnum):
    GENERAL_QUESTION = "GENERAL_QUESTION"
    TUTOR_APPLICATION = "TUTOR_APPLICATION"
    FINDING_A_TUTOR = "FINDING_A_TUTOR"
    PAYMENT = "PAYMENT"
    TECHNICAL_PROBLEM = "TECHNICAL_PROBLEM"
    TALK_TO_ADMIN = "TALK_TO_ADMIN"


class TutorFilters(NamedTuple):
    """Filter set accepted by ``tutor_service.search_tutors``."""

    subject: str | None = None
    level: str | None = None
    country: str | None = None
    language: str | None = None
    mode: str | None = None
    min_etb: float | None = None
    max_etb: float | None = None
    min_usd: float | None = None
    max_usd: float | None = None
    status: str | None = None
    search: str | None = None


# ---------------------------------------------------------------------------
# Matching engine vocabulary
# ---------------------------------------------------------------------------

#: The seven matching criteria, in their canonical order. Every weight profile
#: is expressed over exactly these keys.
MATCH_CRITERIA: tuple[str, ...] = (
    "location",
    "grade",
    "subject",
    "english",
    "academic",
    "experience",
    "category",
)

#: Default criterion weights (Requirement: 25/20/15/15/10/10/5 = 100%).
DEFAULT_MATCH_WEIGHTS: dict[str, float] = {
    "location": 0.25,
    "grade": 0.20,
    "subject": 0.15,
    "english": 0.15,
    "academic": 0.10,
    "experience": 0.10,
    "category": 0.05,
}

#: Grade 4 English: English is the point of the job.
ENGLISH_FOCUSED_MATCH_WEIGHTS: dict[str, float] = {
    "location": 0.15,
    "grade": 0.20,
    "subject": 0.15,
    "english": 0.25,
    "academic": 0.05,
    "experience": 0.15,
    "category": 0.05,
}

#: Grade 12 Mathematics: subject mastery and academics matter most.
STEM_ADVANCED_MATCH_WEIGHTS: dict[str, float] = {
    "location": 0.05,
    "grade": 0.20,
    "subject": 0.25,
    "english": 0.10,
    "academic": 0.20,
    "experience": 0.15,
    "category": 0.05,
}


def normalise_weights(weights: dict[str, float]) -> dict[str, float]:
    """Return ``weights`` as a complete, 1.0-total profile over the criteria.

    Missing criteria default to zero and the total is rescaled, so a partial
    profile is always safe to use. Raises when every weight is zero, which
    would make the match score meaningless.
    """
    unknown = set(weights) - set(MATCH_CRITERIA)
    if unknown:
        raise ValueError(f"Unknown match criteria: {sorted(unknown)}")

    values = {name: max(0.0, float(weights.get(name, 0.0))) for name in MATCH_CRITERIA}
    total = sum(values.values())
    if total <= 0:
        raise ValueError("Match weights must not all be zero")
    return {name: value / total for name, value in values.items()}


#: Weighted score bands used to pick a job-aware profile. A job with no
#: location (or an online one) drops the location weight entirely rather than
#: scoring it as a mismatch.
ONLINE_MATCH_WEIGHTS: dict[str, float] = {
    "location": 0.0,
    "grade": 0.25,
    "subject": 0.20,
    "english": 0.20,
    "academic": 0.10,
    "experience": 0.20,
    "category": 0.05,
}

IN_PERSON_MATCH_WEIGHTS: dict[str, float] = {
    "location": 0.35,
    "grade": 0.20,
    "subject": 0.15,
    "english": 0.10,
    "academic": 0.05,
    "experience": 0.10,
    "category": 0.05,
}

__all__ = [
    "ACADEMIC_DOCUMENT_TYPES",
    "DEFAULT_MATCH_WEIGHTS",
    "ENGLISH_FOCUSED_MATCH_WEIGHTS",
    "IN_PERSON_MATCH_WEIGHTS",
    "MATCH_CRITERIA",
    "ONLINE_MATCH_WEIGHTS",
    "STEM_ADVANCED_MATCH_WEIGHTS",
    "CandidateStatus",
    "Currency",
    "DocumentType",
    "EducationLevel",
    "EnglishRequirement",
    "EntranceExamType",
    "EthiopianTutorStatus",
    "JobPostStatus",
    "MemberReferralStatus",
    "RequestStatus",
    "StorageStatus",
    "SupportCategory",
    "TeachingMode",
    "TutorFilters",
    "TutorStatus",
    "normalise_weights",
]
