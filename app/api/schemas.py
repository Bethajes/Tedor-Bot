"""Pydantic contracts for the public REST API (Requirement 12.3).

None of these models exposes private data (Requirement 10.7): Telegram IDs,
phone numbers, emails, chat IDs, message IDs and documents are intentionally
absent from the public schemas.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import Tutor


class TutorPublicResponse(BaseModel):
    """Public profile of a verified tutor."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="TDR identifier, e.g. TDR-000001")
    displayName: str
    country: str
    city: str
    bio: str
    subjects: list[str]
    levels: list[str]
    languages: list[str]
    teachingMode: str
    experienceYears: int
    etbRate: float | None = None
    usdRate: float | None = None
    verified: bool = True

    @classmethod
    def from_tutor(cls, tutor: Tutor) -> "TutorPublicResponse":
        return cls(
            id=tutor.public_tutor_id,
            displayName=tutor.display_name,
            country=tutor.country,
            city=tutor.city,
            bio=tutor.bio or "",
            subjects=sorted(tutor.subject_list),
            levels=sorted(tutor.level_list),
            languages=sorted(tutor.language_list),
            teachingMode=tutor.teaching_mode,
            experienceYears=tutor.experience_years,
            etbRate=tutor.etb_rate,
            usdRate=tutor.usd_rate,
            verified=tutor.status == "VERIFIED",
        )


class EntranceExamPublic(BaseModel):
    """Public view of the university entrance examination result.

    The raw score is published alongside its own maximum and the percentage of
    that scale, because scores from different examination systems are only
    comparable once normalised.
    """

    score: float
    maxScore: float | None = None
    examType: str | None = None
    year: int | None = None
    normalisedPercentage: float | None = None


class TutorPublicProfileResponse(BaseModel):
    """Structured public profile returned by ``GET /api/tutors/{id}/profile``.

    Deliberately richer than :class:`TutorPublicResponse` — it carries the
    structured onboarding fields so the website can show qualifications,
    teaching areas and grades — but strictly narrower on privacy. Nothing here
    is a contact detail, an identifier, an address or a file reference, so the
    endpoint needs no secret and exposes nothing that section 24 protects.

    Excluded on purpose: phone, email, ``currentAddress``, Telegram IDs,
    document references, the English voice file reference and
    ``memberReferralStatus`` (the 50-member rule is an onboarding condition,
    not a quality signal, so publishing it would be misleading).
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="TDR identifier, e.g. TDR-000001")
    displayName: str
    country: str
    city: str
    bio: str

    subjects: list[str]
    grades: list[str] = Field(default_factory=list)
    levels: list[str] = Field(default_factory=list)
    languages: list[str]
    teachingLocations: list[str] = Field(default_factory=list)
    teachingMode: str

    experienceYears: int
    teachingExperienceYears: int | None = None
    teachingExperienceDescription: str | None = None

    #: Self-rated 1-10. The voice recording itself is never published.
    englishProficiency: int | None = None
    hasEnglishVoice: bool = False

    university: str | None = None
    department: str | None = None
    educationLevel: str | None = None
    universityYear: int | None = None
    cgpa: float | None = None

    entranceExam: EntranceExamPublic | None = None

    verified: bool = True
    verifiedAt: str | None = None

    @classmethod
    def from_tutor(cls, tutor: Tutor) -> "TutorPublicProfileResponse":
        from app.services.matching_service import normalised_entrance_score

        exam = None
        if tutor.entrance_exam_score is not None:
            normalised = normalised_entrance_score(tutor)
            exam = EntranceExamPublic(
                score=tutor.entrance_exam_score,
                maxScore=tutor.entrance_exam_max_score,
                examType=tutor.entrance_exam_type,
                year=tutor.entrance_exam_year,
                # Percentage of the exam's own scale, so two tutors on
                # different systems are comparable without exposing raw marks.
                normalisedPercentage=(
                    round(normalised, 1) if normalised is not None else None
                ),
            )

        return cls(
            id=tutor.public_tutor_id,
            displayName=tutor.display_name,
            country=tutor.country,
            city=tutor.city,
            bio=tutor.bio or "",
            subjects=sorted(tutor.subject_list),
            grades=sorted(tutor.grade_list),
            levels=sorted(tutor.level_list),
            languages=sorted(tutor.language_list),
            teachingLocations=sorted({item.location for item in tutor.tutoring_locations}),
            teachingMode=tutor.teaching_mode,
            experienceYears=int(tutor.experience_years or 0),
            teachingExperienceYears=tutor.teaching_experience_years,
            teachingExperienceDescription=tutor.teaching_experience_description,
            englishProficiency=tutor.english_proficiency,
            hasEnglishVoice=bool(tutor.has_english_voice),
            university=tutor.university,
            department=tutor.department,
            educationLevel=tutor.education_level,
            universityYear=tutor.university_year,
            cgpa=tutor.cgpa,
            entranceExam=exam,
            verified=tutor.status == "VERIFIED",
            verifiedAt=tutor.verified_at.isoformat() if tutor.verified_at else None,
        )


class TutorListResponse(BaseModel):
    items: list[TutorPublicResponse]
    page: int
    pageSize: int
    total: int
    totalPages: int


class TutorSearchParams(BaseModel):
    """Query parameters accepted by ``GET /api/tutors/search``."""

    subject: str | None = None
    level: str | None = None
    country: str | None = None
    language: str | None = None
    mode: str | None = None
    min_etb: float | None = None
    max_etb: float | None = None
    min_usd: float | None = None
    max_usd: float | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @classmethod
    def from_query(cls, params: dict[str, Any]) -> "TutorSearchParams":
        return cls(**{key: value for key, value in params.items() if value not in (None, "")})


class HealthResponse(BaseModel):
    status: str
    database: str
    version: str
    tutors: int | None = None


class DocumentReference(BaseModel):
    """Document metadata — internal reference only, no file contents."""

    model_config = ConfigDict(from_attributes=True)

    documentType: str
    telegramFileId: str
    telegramFileUniqueId: str | None = None
    telegramMessageId: int | None = None
    storageChatId: int | None = None
    uploadedAt: str | None = None


class DistinctValuesResponse(BaseModel):
    values: list[str]


__all__ = [
    "DistinctValuesResponse",
    "DocumentReference",
    "EntranceExamPublic",
    "HealthResponse",
    "TutorListResponse",
    "TutorPublicProfileResponse",
    "TutorPublicResponse",
    "TutorSearchParams",
]