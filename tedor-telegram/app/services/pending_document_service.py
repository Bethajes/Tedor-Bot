"""Documents uploaded before the tutor application exists.

The Mini App collects academic documents as the second-to-last step of the
application, which means they arrive *before* the ``Tutor`` row that will own
them. Uploading straight to :class:`~app.models.TutorDocument` is therefore
impossible for a first-time applicant, and refusing the upload would leave the
form impossible to finish — the one thing a form must never do.

So an upload is staged here against the authenticated Telegram user and
promoted to a real document the moment the application is submitted. Nothing is
stored twice: promotion moves the reference and deletes the staged row.

Isolation is unchanged. Every function is scoped to a ``telegram_user_id``
that comes from the validated credentials, never from the request body, and a
staged document can only ever be promoted onto that same user's application.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import DocumentType
from app.models import PendingDocument, Tutor, utcnow
from app.services import tutor_profile_service as profile_service

logger = logging.getLogger(__name__)

#: Document types this service will stage. One source of truth, the same list
#: ``record_document`` accepts, so a staged file can never be of a type the
#: real path would reject.
_KNOWN_DOCUMENT_TYPES = frozenset(str(item) for item in DocumentType)


class PendingDocumentError(Exception):
    """A staged document could not be stored."""


def stage_document(
    session: Session,
    telegram_user_id: int,
    document_type: str,
    *,
    file_id: str | None = None,
    file_unique_id: str | None = None,
    message_id: int | None = None,
    file_name: str | None = None,
    mime_type: str | None = None,
    size_bytes: int | None = None,
    storage_chat_id: int | None = None,
) -> PendingDocument:
    """Record one uploaded document against a not-yet-created application.

    Re-uploading the same type replaces the staged reference, so a tutor who
    picked the wrong photo can fix it without two rows competing for review.
    """
    kind = str(document_type or "").strip().upper()
    if kind not in _KNOWN_DOCUMENT_TYPES:
        raise PendingDocumentError(f"Unknown document type: {document_type!r}")

    # Validated with the same rule the real document path uses, so a staged
    # file is exactly as acceptable as one uploaded after submitting.
    profile_service.validate_document_file(file_name, mime_type)

    existing = (
        session.query(PendingDocument)
        .filter(
            PendingDocument.telegram_user_id == int(telegram_user_id),
            PendingDocument.document_type == kind,
        )
        .one_or_none()
    )
    if existing is not None:
        existing.telegram_file_id = file_id or existing.telegram_file_id
        existing.telegram_file_unique_id = file_unique_id or existing.telegram_file_unique_id
        existing.telegram_message_id = message_id or existing.telegram_message_id
        existing.storage_chat_id = storage_chat_id or existing.storage_chat_id
        existing.file_name = file_name or existing.file_name
        existing.mime_type = mime_type or existing.mime_type
        existing.size_bytes = size_bytes
        existing.created_at = utcnow()
        session.flush()
        return existing

    staged = PendingDocument(
        telegram_user_id=int(telegram_user_id),
        document_type=kind,
        telegram_file_id=file_id,
        telegram_file_unique_id=file_unique_id,
        telegram_message_id=message_id,
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        storage_chat_id=storage_chat_id,
    )
    session.add(staged)
    session.flush()
    return staged


def staged_document_types(session: Session, telegram_user_id: int) -> list[str]:
    """Document types this user has uploaded but not yet submitted."""
    return sorted(
        session.execute(
            select(PendingDocument.document_type).where(
                PendingDocument.telegram_user_id == int(telegram_user_id)
            )
        ).scalars()
    )


def promote_documents(session: Session, tutor: Tutor) -> int:
    """Move this user's staged documents onto their new application.

    Called immediately after the tutor row is created. Returns how many were
    attached, so the caller can log it; the documents themselves are ordinary
    :class:`TutorDocument` rows afterwards, with no separate lifecycle.
    """
    rows = (
        session.execute(
            select(PendingDocument).where(
                PendingDocument.telegram_user_id == int(tutor.telegram_user_id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0

    for staged in rows:
        # An application payload may already carry a document of this type
        # (uploaded through the bot); record_document replaces it in place.
        profile_service.record_document(
            session,
            tutor,
            staged.document_type,
            staged.telegram_file_id,
            staged.telegram_file_unique_id,
            staged.telegram_message_id,
            file_name=staged.file_name,
            mime_type=staged.mime_type,
            storage_chat_id=staged.storage_chat_id,
        )
        session.delete(staged)

    session.flush()
    logger.info(
        "Staged documents attached to the new tutor application",
        extra={
            "tdr_id": tutor.public_tutor_id,
            "documents": len(rows),
        },
    )
    return len(rows)


def staged_payload(session: Session, telegram_user_id: int) -> list[dict[str, Any]]:
    """Staged documents shaped like the ``documents`` list a submit payload carries."""
    return [
        {
            "document_type": staged.document_type,
            "telegram_file_id": staged.telegram_file_id,
            "telegram_file_unique_id": staged.telegram_file_unique_id,
            "telegram_message_id": staged.telegram_message_id,
            "file_name": staged.file_name,
            "mime_type": staged.mime_type,
        }
        for staged in session.execute(
            select(PendingDocument)
            .where(PendingDocument.telegram_user_id == int(telegram_user_id))
            .order_by(PendingDocument.document_type)
        ).scalars()
    ]


__all__ = [
    "PendingDocumentError",
    "promote_documents",
    "stage_document",
    "staged_document_types",
    "staged_payload",
]