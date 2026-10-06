"""Public tutor endpoints (Requirements 10.1 – 10.8)."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import require_api_secret
from app.api.schemas import (
    DistinctValuesResponse,
    DocumentReference,
    TutorListResponse,
    TutorPublicProfileResponse,
    TutorPublicResponse,
    TutorSearchParams,
)
from app.config import settings
from app.database import get_db
from app.enums import TutorFilters, TutorStatus
from app.models import Tutor, TutorLanguage, TutorLevel, TutorSubject
from app.services import tutor_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["tutors"])

DbSession = Annotated[Session, Depends(get_db)]
Secret = Annotated[bool, Depends(require_api_secret)]


def _apply_filters(stmt: Any, filters: TutorFilters) -> Any:
    """Narrow ``stmt`` so every supplied filter holds for the rows (Property 6)."""
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
    return stmt


def _list_page(
    session: Session, filters: TutorFilters, page: int, page_size: int
) -> TutorListResponse:
    """Return one page of VERIFIED tutors matching every filter."""
    stmt = _apply_filters(
        select(Tutor).where(Tutor.status == TutorStatus.VERIFIED), filters
    )
    total = int(
        session.execute(
            select(func.count()).select_from(stmt.order_by(None).subquery())
        ).scalar_one()
    )
    tutors = list(
        session.execute(
            stmt.order_by(Tutor.id).offset((page - 1) * page_size).limit(page_size)
        )
        .scalars()
        .all()
    )
    return TutorListResponse(
        items=[TutorPublicResponse.from_tutor(tutor) for tutor in tutors],
        page=page,
        pageSize=page_size,
        total=total,
        totalPages=(total + page_size - 1) // page_size if page_size else 0,
    )


def search_params(
    subject: str | None = Query(default=None),
    level: str | None = Query(default=None),
    country: str | None = Query(default=None),
    language: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    min_etb: float | None = Query(default=None, ge=0),
    max_etb: float | None = Query(default=None, ge=0),
    min_usd: float | None = Query(default=None, ge=0),
    max_usd: float | None = Query(default=None, ge=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
) -> TutorSearchParams:
    return TutorSearchParams(
        subject=subject,
        level=level,
        country=country,
        language=language,
        mode=mode,
        min_etb=min_etb,
        max_etb=max_etb,
        min_usd=min_usd,
        max_usd=max_usd,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.get("/tutors", response_model=TutorListResponse)
def list_tutors(
    session: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
) -> TutorListResponse:
    """Requirement 10.1: paginated list of VERIFIED tutors only."""
    return _list_page(session, TutorFilters(), page, page_size)


@router.get("/tutors/search", response_model=TutorListResponse)
def search_tutors(
    session: DbSession, params: Annotated[TutorSearchParams, Depends(search_params)]
) -> TutorListResponse:
    """Requirement 10.3: filtered search restricted to VERIFIED tutors."""
    return _list_page(
        session,
        TutorFilters(**params.model_dump(exclude={"page", "page_size"})),
        params.page,
        params.page_size,
    )


@router.get(
    "/tutors/{tutor_id}/profile",
    response_model=TutorPublicProfileResponse,
)
def get_tutor_profile(tutor_id: str, session: DbSession) -> TutorPublicProfileResponse:
    """The structured public profile: qualifications, areas, grades.

    Declared above ``/tutors/{tutor_id}`` deliberately — FastAPI matches routes
    in declaration order, so a more specific path has to come first or the
    one-segment route would swallow it.

    Public by design and safe without a secret: the response carries no phone
    number, email, home address, Telegram identifier or document reference.
    """
    tutor = tutor_service.get_tutor_by_tdr_id(session, tutor_id)
    if tutor is None or tutor.status != TutorStatus.VERIFIED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tutor not found"
        )
    return TutorPublicProfileResponse.from_tutor(tutor)


@router.get("/tutors/{tutor_id}", response_model=TutorPublicResponse)
def get_tutor(tutor_id: str, session: DbSession) -> TutorPublicResponse:
    """Requirement 10.2: full public profile or 404."""
    tutor = tutor_service.get_tutor_by_tdr_id(session, tutor_id)
    if tutor is None or tutor.status != TutorStatus.VERIFIED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tutor not found"
        )
    return TutorPublicResponse.from_tutor(tutor)


@router.get("/tutors/{tutor_id}/photo")
def get_tutor_photo(tutor_id: str, session: DbSession) -> Response:
    """Requirement 10.4: proxy the Telegram file without leaking credentials."""
    tutor = tutor_service.get_tutor_by_tdr_id(session, tutor_id)
    if tutor is None or tutor.status != TutorStatus.VERIFIED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tutor not found"
        )
    file_id = tutor.profile_photo_file_id
    if not file_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No profile photo available"
        )
    payload = _download_telegram_file(file_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Photo temporarily unavailable",
        )
    content, content_type = payload
    return Response(content=content, media_type=content_type)


@router.get("/tutors/{tutor_id}/documents", response_model=list[DocumentReference])
def get_tutor_documents(tutor_id: str, session: DbSession, _: Secret) -> list[DocumentReference]:
    """Requirement 10.6: protected document references."""
    tutor = tutor_service.get_tutor_by_tdr_id(session, tutor_id)
    if tutor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tutor not found"
        )
    return [
        DocumentReference(
            documentType=document.document_type,
            telegramFileId=document.telegram_file_id or "",
            telegramFileUniqueId=document.telegram_file_unique_id,
            telegramMessageId=document.telegram_message_id,
            storageChatId=document.storage_chat_id,
            uploadedAt=document.uploaded_at.isoformat() if document.uploaded_at else None,
        )
        for document in tutor.documents
    ]


@router.get("/subjects", response_model=DistinctValuesResponse)
def list_subjects(session: DbSession) -> DistinctValuesResponse:
    """Requirement 10.8: distinct values from VERIFIED tutors."""
    return DistinctValuesResponse(values=tutor_service.distinct_values(session, "subjects"))


@router.get("/levels", response_model=DistinctValuesResponse)
def list_levels(session: DbSession) -> DistinctValuesResponse:
    return DistinctValuesResponse(values=tutor_service.distinct_values(session, "levels"))


@router.get("/countries", response_model=DistinctValuesResponse)
def list_countries(session: DbSession) -> DistinctValuesResponse:
    return DistinctValuesResponse(values=tutor_service.distinct_values(session, "countries"))


@router.get("/languages", response_model=DistinctValuesResponse)
def list_languages(session: DbSession) -> DistinctValuesResponse:
    return DistinctValuesResponse(values=tutor_service.distinct_values(session, "languages"))


def _download_telegram_file(file_id: str) -> tuple[bytes, str] | None:
    """Fetch a Telegram file by ID using the bot's own credentials."""
    if not settings.bot_token:
        logger.error("BOT_TOKEN is not configured; cannot proxy tutor photo")
        return None

    async def fetch() -> tuple[bytes, str] | None:
        from telegram import Bot

        bot = Bot(settings.bot_token)
        try:
            handle = await bot.get_file(file_id)
            buffer = io.BytesIO()
            await handle.download_to_drive(custom_path=buffer)
            return buffer.getvalue(), "image/jpeg"
        except Exception:
            logger.error("Telegram photo download failed")
            return None
        finally:
            await bot.shutdown()

    try:
        return asyncio.run(fetch())
    except Exception:  # pragma: no cover - defensive
        logger.error("Telegram photo proxy failed")
        return None


__all__ = ["router", "search_params"]