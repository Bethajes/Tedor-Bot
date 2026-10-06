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
    "HealthResponse",
    "TutorListResponse",
    "TutorPublicResponse",
    "TutorSearchParams",
]