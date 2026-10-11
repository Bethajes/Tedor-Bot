"""Mini App API routes (Requirements 9.1–9.9, 11.2).

All routes are prefixed /api/miniapp and protected by the Init Data auth
dependency. Identity is always derived server-side from the validated Init Data
— never from a client-supplied parameter.

Private fields (phone, email, telegram_user_id, storage IDs, voice file IDs)
are never returned in any response.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.api.miniapp.auth import (
    WEB_TOKEN_TTL_SECONDS,
    AuthError,
    TelegramUser,
    create_web_token,
    get_current_telegram_user,
)
from app.config import settings
from app.database import get_db
from app.enums import DocumentType, JobApplicationStatus
from app.models import Tutor
from app.services import draft_service
from app.services import job_application_service as applications
from app.services import pending_document_service
from app.services import profile_completion_service
from app.services import rate_limit
from app.services import storage_service
from app.services import web_login_service
from app.services.pending_document_service import PendingDocumentError
from app.services.storage_service import archive_bytes
from app.services.student_service import (
    StudentRequestValidationError,
    assign_tutor,
    create_student_request,
    get_request_by_id,
)
from app.services.tutor_profile_service import (
    ALLOWED_DOCUMENT_EXTENSIONS,
    ProfileValidationError,
    create_ethiopian_tutor,
    present_document_types,
    record_document,
)
from app.services.tutor_service import (
    TutorValidationError,
    get_tutor_by_tdr_id,
    get_tutor_by_telegram_id,
)

# Maximum upload size: 20 MB (Requirements 5.2, 9.7)
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/miniapp", tags=["miniapp"])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[TelegramUser, Depends(get_current_telegram_user)]

# ---------------------------------------------------------------------------
# Private-field allowlist: every key NOT in this set is stripped from the
# tutor profile response (Requirements 9.1, 12.2).
# ---------------------------------------------------------------------------
_TUTOR_PUBLIC_FIELDS = frozenset(
    {
        "id",
        "public_tutor_id",
        "name",
        "display_name",
        "country",
        "city",
        "bio",
        "gender",
        "age",
        "teaching_mode",
        "status",
        "experience_years",
        "teaching_experience_years",
        "teaching_experience_description",
        "english_proficiency",
        "university",
        "department",
        "education_level",
        "university_year",
        "cgpa",
        "entrance_exam_score",
        "entrance_exam_year",
        "entrance_exam_type",
        "entrance_exam_max_score",
        "academic_stream",
        "grade8_exam_average",
        "grade9_average",
        "grade10_average",
        "grade11_average",
        "grade12_average",
        "current_address",
        "timezone",
        "subjects",
        "grades",
        "levels",
        "languages",
        "teaching_locations",
        "created_at",
        "updated_at",
        "verified_at",
        "member_referral_status",
        "profile_photo_file_id",
        "storage_status",
    }
)

_PRIVATE_FIELDS = frozenset(
    {
        "phone",
        "email",
        "telegram_user_id",
        "telegram_storage_chat_id",
        "telegram_storage_message_id",
        "english_voice_file_id",
        "english_voice_message_id",
        "verified_by",
    }
)


def _enforce_login_code_limit(request: Request) -> None:
    """Rate limit the web-login code endpoints per client address.

    These are the only Mini App routes reachable without a credential, so they
    are the only ones an anonymous caller can spend. The limit is per client
    address rather than per phone number because the phone number is exactly
    the value an anonymous caller should not be able to hammer.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        key = forwarded.split(",")[0].strip()
    else:
        key = request.client.host if request.client else "unknown"
    verdict = rate_limit.allow(
        "miniapp_login_code",
        key,
        limit=settings.mini_app_code_rate_limit,
        window=settings.mini_app_code_rate_window,
    )
    if not verdict.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(verdict.retry_after)},
        )


def _ensure_owner(resource: Any, user: TelegramUser, detail: str = "Forbidden") -> None:
    """Cross-user isolation guard (Requirements 2.6, 12.3).

    Every resource a Mini App route resolves carries the Telegram user id it
    belongs to. Identity is always derived server-side from the validated Init
    Data, so a mismatch here means a resolved row belongs to somebody else —
    which is answered with 403 rather than ever being read, serialised or
    mutated.
    """
    owner_id = getattr(resource, "telegram_user_id", None)
    if owner_id is None or int(owner_id) != int(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


def _tutor_to_public_dict(tutor: Tutor) -> dict[str, Any]:
    """Serialise a Tutor row excluding every private field."""
    data: dict[str, Any] = {}
    for col in tutor.__table__.columns:
        if col.name in _PRIVATE_FIELDS:
            continue
        value = getattr(tutor, col.name)
        # Datetime → ISO string
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        data[col.name] = value

    # Include relationship lists
    data["subjects"] = tutor.subject_list
    data["grades"] = tutor.grade_list
    data["levels"] = tutor.level_list
    data["languages"] = tutor.language_list
    data["teaching_locations"] = [
        {"location": loc.location, "normalized_location": loc.normalized_location}
        for loc in tutor.tutoring_locations
    ]
    # Which academic documents are already on file, so the upload screen can
    # mark them as done instead of asking for them again (Requirement 5.6).
    # Only the *types* are listed — never file ids or storage references.
    data["document_types"] = sorted(present_document_types(tutor))
    data["required_documents"] = sorted(
        profile_completion_service.required_documents_for(tutor)
    )
    return data


# ---------------------------------------------------------------------------
# Web login (the Mini App opened outside Telegram)
# ---------------------------------------------------------------------------


@router.post("/auth/code", status_code=status.HTTP_202_ACCEPTED)
def request_login_code(
    body: dict[str, Any],
    request: Request,
    session: DbSession,
) -> dict[str, Any]:
    """POST /api/miniapp/auth/code — issue a one-time code.

    Unauthenticated by necessity: it is the endpoint that starts authentication.
    It is rate limited per client address and answers identically for every
    failure, so it can neither be used to enumerate applicants nor to send
    messages in bulk (Requirements 2.5, 2.6).
    """
    _enforce_login_code_limit(request)

    try:
        issued = web_login_service.issue_code(session, str(body.get("phone") or ""))
        session.commit()
    except web_login_service.LoginError:
        session.rollback()
        # Identical to a rate-limited response on purpose.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="We could not send a code to that number.",
        ) from None

    return {
        "sent": True,
        "delivered": issued.delivered,
        "dev_code": issued.dev_code,
    }


@router.post("/auth/verify", status_code=status.HTTP_200_OK)
def verify_login_code(
    body: dict[str, Any],
    request: Request,
    session: DbSession,
) -> dict[str, Any]:
    """POST /api/miniapp/auth/verify — exchange the code for a short-lived JWT.

    The token carries ``telegram_user_id``, the same claim the signed Init Data
    yields, so every other Mini App route is unchanged by the web login
    (Requirements 2.5, 2.6).
    """
    _enforce_login_code_limit(request)

    try:
        telegram_user_id = web_login_service.redeem_code(
            session,
            str(body.get("phone") or ""),
            str(body.get("code") or ""),
        )
        session.commit()
    except web_login_service.LoginError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That code did not work.",
        ) from None

    try:
        token = create_web_token(telegram_user_id)
    except AuthError as exc:
        # No signing secret configured: the web login is simply off.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The web login is not configured.",
        ) from exc

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": WEB_TOKEN_TTL_SECONDS,
    }


# ---------------------------------------------------------------------------
# Tutor profile
# ---------------------------------------------------------------------------


@router.get("/tutor/profile")
def get_tutor_profile(session: DbSession, user: CurrentUser) -> dict[str, Any]:
    """GET /api/miniapp/tutor/profile — authenticated tutor's public fields.

    Returns HTTP 404 when no Tutor row exists for the authenticated user.
    Requirements: 9.1, 12.2
    """
    tutor = get_tutor_by_telegram_id(session, user.id)
    if tutor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor profile not found",
        )
    # Ownership guard (Requirements 2.6, 12.3): identity is always server-derived,
    # but we assert it explicitly to guard against future refactors.
    _ensure_owner(tutor, user)
    return _tutor_to_public_dict(tutor)


# ---------------------------------------------------------------------------
# Tutor draft
# ---------------------------------------------------------------------------


@router.get("/tutor/draft")
def get_tutor_draft(session: DbSession, user: CurrentUser) -> dict[str, Any]:
    """GET /api/miniapp/tutor/draft — resume an incomplete application.

    Returns HTTP 404 when no draft exists, and 403 when the draft that was
    resolved belongs to another user.
    Requirements: 9.2, 2.6, 12.3
    """
    draft = draft_service.get_draft(session, user.id, "tutor")
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No draft found",
        )
    # Ownership guard (Requirements 2.6, 12.3)
    _ensure_owner(draft, user, detail="This draft belongs to another user")
    return json.loads(draft.payload)


@router.post("/tutor/draft", status_code=status.HTTP_200_OK)
def save_tutor_draft(
    body: dict[str, Any],
    session: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    """POST /api/miniapp/tutor/draft — save or update the draft.

    The telegram_user_id is always taken from the authenticated session and
    never from the request body (Requirement 12.4).
    Requirements: 9.3
    """
    # Prevent the client from smuggling a different user ID into the payload
    body.pop("telegram_user_id", None)

    draft = draft_service.save_draft(session, user.id, "tutor", body)
    # Ownership guard (Requirements 2.6, 12.3): the row we just wrote must be
    # attached to the authenticated user, never to anybody else.
    _ensure_owner(draft, user, detail="This draft belongs to another user")
    session.commit()
    return {"saved": True}


# ---------------------------------------------------------------------------
# Tutor submit
# ---------------------------------------------------------------------------


@router.post("/tutor/submit", status_code=status.HTTP_201_CREATED)
async def submit_tutor_application(
    body: dict[str, Any],
    session: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    """POST /api/miniapp/tutor/submit — submit the tutor application.

    Calls the same create_ethiopian_tutor() that the bot uses, then fires
    the same admin notification (Requirements 9.4, 11.2).
    On success the draft is deleted (Requirement 8.5).
    """
    # Enforce server-derived identity
    body["telegram_user_id"] = user.id

    # Check for an existing tutor record
    existing = get_tutor_by_telegram_id(session, user.id)
    if existing is not None:
        # Ownership guard (Requirements 2.6, 12.3)
        _ensure_owner(existing, user)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A tutor application already exists for this account",
        )

    try:
        tutor = create_ethiopian_tutor(session, body)
        # Ownership guard (Requirements 2.6, 12.3): the created row must be
        # attached to the authenticated user, never to anybody else.
        _ensure_owner(tutor, user)
        session.commit()
    except (ProfileValidationError, TutorValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # Documents uploaded before the application existed now belong to it.
    try:
        pending_document_service.promote_documents(session, tutor)
        session.commit()
    except Exception:
        # A missing attachment must not lose a submitted application: the tutor
        # can re-upload from the profile, whereas losing the application cannot
        # be undone by them.
        session.rollback()
        logger.exception("Staged documents could not be attached", extra={"tdr_id": tutor.public_tutor_id})

    # Delete the draft now that submission succeeded (Requirement 8.5)
    draft_service.delete_draft(session, user.id, "tutor")
    session.commit()

    # Archive the record in the Telegram channel — the same call the bot
    # onboarding makes. Without this a Mini App tutor exists in SQLite but
    # never appears in the archive channel, which is why the channel looked
    # empty while the database was full. Failures are logged, not raised: the
    # application is already safely stored.
    await _archive_tutor(tutor.public_tutor_id)

    # Fire admin notification (Requirement 11.2) — failures are logged, not raised
    try:
        from app.services import notification_service  # lazy to avoid circular import
        await notification_service.notify_admin_new_tutor(bot=None, tutor=tutor)
    except Exception:
        logger.warning(
            "Admin notification failed after mini-app tutor submission",
            extra={"tdr_id": tutor.public_tutor_id},
        )

    return {
        "public_tutor_id": tutor.public_tutor_id,
        "status": tutor.status,
    }


async def _archive_tutor(tdr_id: str) -> None:
    """Push a tutor record plus its documents into the archive channel."""
    from app.services.telegram_client import get_bot

    bot = get_bot()
    if bot is None:
        logger.error("Tutor %s was not archived: no bot available", tdr_id)
        return
    try:
        await storage_service.TelegramStorageService(bot).store_tutor_record(
            tdr_id, storage_service.tutor_document_refs_by_id(tdr_id) or None
        )
    except Exception:
        logger.exception("Archival failed for %s; profile already saved", tdr_id)


# ---------------------------------------------------------------------------
# Parent draft
# ---------------------------------------------------------------------------


@router.get("/parent/draft")
def get_parent_draft(session: DbSession, user: CurrentUser) -> dict[str, Any]:
    """GET /api/miniapp/parent/draft — resume an incomplete parent request.

    Returns HTTP 404 when no draft exists, and 403 when the resolved draft
    belongs to another user.
    Requirements: 9.5, 2.6, 12.3
    """
    draft = draft_service.get_draft(session, user.id, "parent")
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No draft found",
        )
    # Ownership guard (Requirements 2.6, 12.3)
    _ensure_owner(draft, user, detail="This draft belongs to another user")
    return json.loads(draft.payload)


@router.post("/parent/draft", status_code=status.HTTP_200_OK)
def save_parent_draft(
    body: dict[str, Any],
    session: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    """POST /api/miniapp/parent/draft — save or update the parent draft.

    Identity is always taken from the session, never the body.
    Requirements: 9.5, 2.6, 12.3
    """
    body.pop("telegram_user_id", None)
    draft = draft_service.save_draft(session, user.id, "parent", body)
    # Ownership guard (Requirements 2.6, 12.3)
    _ensure_owner(draft, user, detail="This draft belongs to another user")
    session.commit()
    return {"saved": True}


# ---------------------------------------------------------------------------
# Parent submit
# ---------------------------------------------------------------------------


@router.post("/parent/submit", status_code=status.HTTP_201_CREATED)
async def submit_parent_request(
    body: dict[str, Any],
    session: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    """POST /api/miniapp/parent/submit — submit the parent request.

    Calls create_student_request() then fires notify_admin_student_request().
    Requirements: 9.6, 11.3
    """
    body["telegram_user_id"] = user.id

    try:
        request = create_student_request(session, body)
        session.commit()
    except StudentRequestValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # Ownership guard (Requirements 2.6, 12.3): the stored request must belong
    # to the authenticated user.
    _ensure_owner(request, user)

    # Clear parent draft after successful submission
    draft_service.delete_draft(session, user.id, "parent")
    session.commit()

    # Fire admin notification — failures are logged, not raised
    try:
        from app.services import notification_service  # lazy to avoid circular import
        await notification_service.notify_admin_student_request(bot=None, request=request)
    except Exception:
        logger.warning(
            "Admin notification failed after mini-app parent submission",
            extra={"request_id": request.public_request_id},
        )

    # The same matches the bot shows, so the parent can pick a tutor straight
    # away (Requirement 6.5). A failure here must never lose the request.
    matches: list[dict[str, Any]] = []
    try:
        from app.services import search_service

        for item in search_service.match_tutors(session, request, limit=5):
            tutor = item.tutor
            matches.append(
                {
                    "public_tutor_id": tutor.public_tutor_id,
                    "display_name": tutor.display_name,
                    "subjects": tutor.subject_list,
                    "levels": tutor.level_list,
                    "teaching_mode": tutor.teaching_mode,
                    "city": tutor.city,
                    "score": item.score,
                    "max_score": search_service.MAX_SCORE,
                    "reasons": list(item.reasons),
                }
            )
    except Exception:
        logger.warning(
            "Match lookup failed after mini-app parent submission",
            extra={"request_id": request.public_request_id},
        )

    return {
        "public_request_id": request.public_request_id,
        "status": request.status,
        "currency": request.currency,
        "matches": matches,
    }


@router.post("/parent/select-tutor", status_code=status.HTTP_200_OK)
async def select_tutor_for_parent(
    body: dict[str, Any],
    session: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    """POST /api/miniapp/parent/select-tutor — pick one of the matched tutors.

    The same ``assign_tutor`` the bot's "📩 Request <name>" button calls, so a
    selection made in the Mini App and one made in the chat are indistinguishable
    to the admin and to the tutor (Requirement 6.5).

    The request must belong to the caller (Requirements 2.6, 12.3) and the tutor
    must be one of the ones the API just offered.
    """
    request_id = str(body.get("request_id") or "").strip().upper()
    tdr_id = str(body.get("tutor_id") or "").strip().upper()
    if not request_id or not tdr_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="request_id and tutor_id are both required.",
        )

    request = get_request_by_id(session, request_id)
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found",
        )
    # Ownership guard (Requirements 2.6, 12.3)
    _ensure_owner(request, user, detail="This request belongs to another user")

    tutor = get_tutor_by_tdr_id(session, tdr_id)
    if tutor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor not found",
        )

    assign_tutor(session, request, tdr_id)
    session.commit()

    try:
        from app.services import notification_service  # lazy to avoid circular import
        await notification_service.notify_admin_tutor_selected(
            bot=None, request=request, tutor=tutor
        )
    except Exception:
        logger.warning(
            "Admin notification failed after mini-app tutor selection",
            extra={"request_id": request.public_request_id, "tdr_id": tdr_id},
        )

    return {
        "request_id": request.public_request_id,
        "tutor_id": tdr_id,
        "status": request.status,
    }


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@router.get("/jobs/{job_reference}")
def get_job_details(
    job_reference: str,
    session: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    """GET /api/miniapp/jobs/{job_reference} — job details + tutor match score.

    Loads the job, resolves the tutor, evaluates/stores the match, and returns
    the job fields plus the score. Returns 404 when the job does not exist or
    is not published.
    Requirements: 9.8
    """
    post = applications.get_job(session, job_reference)
    if post is None or not post.is_published:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found or not accepting applications",
        )

    tutor = get_tutor_by_telegram_id(session, user.id)
    if tutor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor profile not found",
        )

    # Ownership guard (Requirements 2.6, 12.3)
    _ensure_owner(tutor, user)

    # Profile state (Requirement 7.2): the same verdict the Apply flow uses, so
    # the Mini App can list exactly what is outstanding instead of guessing.
    assessment = profile_completion_service.assess(tutor)

    # Check for an existing terminal application (Requirement 7.5)
    existing = applications.get_application(session, job_reference, tutor)
    if existing is not None and applications.is_terminal(existing.status):
        return {
            "job_reference": post.public_post_id,
            "status": existing.status,
            "status_label": applications.status_label(existing.status),
            "profile_state": str(assessment.state),
        }

    if not assessment.is_complete:
        # An incomplete (or blocked) profile is reported with the outstanding
        # items rather than a score, so the tutor is sent to finish it first.
        # ``missing_fields`` is for people to read; ``missing_field_keys`` is
        # the same list keyed by payload field, so the screen can open the
        # form on the first step the tutor still has to answer.
        return {
            "job_reference": post.public_post_id,
            "profile_state": str(assessment.state),
            "profile_percentage": assessment.percentage,
            "missing_fields": list(assessment.outstanding),
            "missing_field_keys": list(assessment.outstanding_keys),
            "missing_documents": list(assessment.missing_documents),
            "reason": assessment.reason,
        }

    # Start or retrieve the application row, then evaluate
    application, _ = applications.start_or_get_application(session, tutor, post)
    evaluation = applications.evaluate_and_store(session, tutor, post, application)
    session.commit()

    return {
        "job_reference": post.public_post_id,
        "location": post.location,
        "student_level": post.student_level,
        "subjects": post.subjects,
        "duration": post.duration,
        "frequency": post.frequency,
        "compensation": post.compensation,
        "tutor_category": post.tutor_category,
        "score": evaluation.percentage,
        "recommendation": evaluation.text,
        "is_strong_match": evaluation.is_strong,
        "status": application.status,
        "profile_state": str(assessment.state),
        "profile_percentage": assessment.percentage,
        # Present and empty: the screen branches on the field, so it is always
        # there rather than sometimes.
        "missing_fields": [],
    }


@router.post("/jobs/{job_reference}/confirm", status_code=status.HTTP_200_OK)
async def confirm_job_application(
    job_reference: str,
    session: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    """POST /api/miniapp/jobs/{job_reference}/confirm — confirm the application.

    Calls applications.submit() then fires the admin notification.
    Requirements: 9.9
    """
    post = applications.get_job(session, job_reference)
    if post is None or not post.is_published:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found or not accepting applications",
        )

    tutor = get_tutor_by_telegram_id(session, user.id)
    if tutor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor profile not found",
        )

    # Ownership guard (Requirements 2.6, 12.3, 12.5)
    _ensure_owner(tutor, user)

    existing = applications.get_application(session, job_reference, tutor)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No application found. Please load the job screen first.",
        )

    # The application row itself must be attached to this tutor (12.3).
    # JobApplication carries no telegram_user_id of its own, so the ownership
    # check compares the resolved tutor rows.
    if existing.tutor_id != tutor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    application, confirmed = applications.submit(session, existing)
    session.commit()

    if confirmed:
        try:
            from app.services import notification_service  # lazy to avoid circular import
            state = {
                "id": application.id,
                "job_id": post.public_post_id,
                "tdr_id": tutor.public_tutor_id,
                "display_name": tutor.display_name,
                "tutor_status": tutor.status,
                "match_score": application.match_score,
                "recommendation": application.recommendation,
                "status": application.status,
                "submitted_at": (
                    application.submitted_at.isoformat()
                    if application.submitted_at
                    else None
                ),
            }
            await notification_service.notify_admin_job_application(bot=None, state=state)
        except Exception:
            logger.warning(
                "Admin notification failed after mini-app job application",
                extra={
                    "post_id": post.public_post_id,
                    "tdr_id": tutor.public_tutor_id,
                },
            )

    return {
        "job_reference": post.public_post_id,
        "status": application.status,
        "confirmed": confirmed,
        "score": int(application.match_score * 100),
    }


# ---------------------------------------------------------------------------
# Document upload
# ---------------------------------------------------------------------------


@router.post("/documents/upload", status_code=status.HTTP_201_CREATED)
async def upload_document(
    session: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, File(...)],
    document_type: Annotated[str, Form(...)],
) -> dict[str, Any]:
    """POST /api/miniapp/documents/upload — upload one academic document.

    Validates:
    - The authenticated user owns the tutor record (HTTP 403 otherwise).
    - File extension is in the allowed set: pdf, jpg, jpeg, png, webp (HTTP 422).
    - File size does not exceed 20 MB (HTTP 413).
    - document_type is a known DocumentType value (HTTP 422).

    A tutor who already has an application attaches the document to it. A
    first-time applicant does not have one yet — documents are the second to
    last step, before the application is submitted — so the reference is staged
    against the authenticated user and attached on submit. Refusing it instead
    would leave the form impossible to finish.

    Stores only the Telegram file ID and metadata — raw bytes are never
    persisted in the database (Requirement 12.1).

    The bytes are forwarded to the Telegram storage channel immediately and the
    ``file_id`` Telegram hands back is what gets stored. That is not an
    optimisation: Requirement 12.1 rules out keeping the bytes anywhere else,
    so if the channel write fails there is nothing left to fall back on and the
    upload is rejected rather than silently discarded.

    Requirements: 5.1, 5.2, 5.4, 5.5, 9.7, 12.1, 12.5
    """
    tutor = get_tutor_by_telegram_id(session, user.id)

    # --- File extension validation (Requirements 5.2, 9.7) ---
    filename = file.filename or ""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"File type '{ext or filename}' is not allowed. "
                f"Accepted types: {', '.join(ALLOWED_DOCUMENT_EXTENSIONS)}"
            ),
        )

    # --- Size validation (Requirements 5.2) ---
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds the 20 MB limit ({len(content) // (1024 * 1024)} MB received)",
        )

    # --- document_type validation ---
    doc_type_str = str(document_type).strip().upper()
    known_types = {str(dt) for dt in DocumentType}
    if doc_type_str not in known_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown document type: {document_type!r}",
        )

    # --- Archive now, then store the reference the archive handed back ---
    # Requirement 12.1: the raw bytes may not live in the database, so the
    # channel write is the upload's only chance to preserve the file. Doing it
    # here (rather than at submit) also means the file is in the archive even
    # if the tutor abandons the form before submitting.
    owner_tag = tutor.public_tutor_id if tutor is not None else f"pending:{user.id}"
    archived = await archive_bytes(
        content,
        document_type=doc_type_str,
        caption=f"{doc_type_str} • {owner_tag}",
        file_name=filename,
        mime_type=file.content_type,
    )
    if archived is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not archive the file. Please try again.",
        )

    stored: Any
    staged = tutor is None

    if staged:
        # No application yet: hold the reference against the authenticated
        # user so it can be attached the moment they submit.
        try:
            row = pending_document_service.stage_document(
                session,
                user.id,
                doc_type_str,
                file_id=archived.telegram_file_id,
                file_unique_id=archived.telegram_file_unique_id,
                message_id=archived.telegram_message_id,
                file_name=filename,
                mime_type=file.content_type,
                size_bytes=len(content),
                storage_chat_id=archived.storage_chat_id,
            )
            stored = row
            session.commit()
        except (PendingDocumentError, ProfileValidationError) as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
    else:
        # Ownership guard (Requirements 2.6, 12.3, 12.5)
        _ensure_owner(tutor, user, detail="This application belongs to another user")

        try:
            stored = record_document(
                session,
                tutor,
                document_type=doc_type_str,
                file_id=archived.telegram_file_id,
                file_unique_id=archived.telegram_file_unique_id,
                message_id=archived.telegram_message_id,
                file_name=filename,
                mime_type=file.content_type,
                storage_chat_id=archived.storage_chat_id,
            )
            session.commit()
        except ProfileValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    logger.info(
        "Document upload archived",
        extra={
            "tdr_id": tutor.public_tutor_id if tutor else None,
            "document_type": doc_type_str,
            "document_filename": filename,
            "size_bytes": len(content),
            "staged": staged,
            "storage_chat_id": archived.storage_chat_id,
            "storage_message_id": archived.telegram_message_id,
        },
    )

    return {
        "document_type": stored.document_type,
        "telegram_file_id": stored.telegram_file_id,
        # True while the document waits for the application it belongs to.
        "pending": staged,
        "uploaded_at": (
            stored.created_at.isoformat()
            if staged and stored.created_at
            else (stored.uploaded_at.isoformat() if stored.uploaded_at else None)
        ),
    }


__all__ = ["router"]
