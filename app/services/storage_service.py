"""Telegram storage layer.

Requirement 12.2: every Telegram file/message operation lives in this service so
the archive can be replaced (S3, R2, …) by swapping the implementation only.
"""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.config import settings
from app.database import session_scope
from app.models import StudentRequest, SupportTicket, Tutor, utcnow
from app.services import student_service, tutor_service
from app.services.currency import format_rate, is_ethiopia
from app.enums import Currency, DocumentType, StorageStatus

logger = logging.getLogger(__name__)

TAG_SAFE = re.compile(r"[^A-Za-z0-9]+")

RETRYABLE_ERRORS = (TelegramError, OSError, asyncio.TimeoutError, TimeoutError)


class StorageError(RuntimeError):
    """Raised when an archive operation finally fails."""


@dataclass
class DocumentRef:
    """A document captured from Telegram, ready to be archived."""

    document_type: str
    telegram_file_id: str
    telegram_file_unique_id: str | None = None
    file_name: str | None = None

    @classmethod
    def coerce(cls, value: Any) -> "DocumentRef":
        if isinstance(value, DocumentRef):
            return value
        if isinstance(value, dict):
            return cls(
                document_type=str(value.get("document_type") or DocumentType.SUPPORTING),
                telegram_file_id=str(value.get("telegram_file_id") or ""),
                telegram_file_unique_id=value.get("telegram_file_unique_id"),
                file_name=value.get("file_name"),
            )
        return cls(
            document_type=str(getattr(value, "document_type", DocumentType.SUPPORTING)),
            telegram_file_id=str(getattr(value, "telegram_file_id", "") or ""),
            telegram_file_unique_id=getattr(value, "telegram_file_unique_id", None),
            file_name=getattr(value, "file_name", None),
        )


@dataclass
class StoredDocument:
    document_type: str
    telegram_file_id: str
    telegram_file_unique_id: str | None
    telegram_message_id: int
    storage_chat_id: int


@dataclass
class StorageResult:
    success: bool
    chat_id: int | None = None
    record_message_id: int | None = None
    documents: list[StoredDocument] = field(default_factory=list)
    error: str | None = None


def _tag(value: str) -> str:
    cleaned = TAG_SAFE.sub("", str(value or ""))
    return cleaned or "NA"


def record_hashtags(tutor: Tutor) -> str:
    """``#TDRxxxxxx #Subject #Country #Mode #Status`` (Requirement 4.5)."""
    subjects = tutor.subjects or []
    primary_subject = subjects[0].subject if subjects else "NA"
    tags = [
        f"#{_tag(tutor.public_tutor_id.replace('-', ''))}",
        f"#{_tag(primary_subject.title())}",
        f"#{_tag(tutor.country.title())}",
        f"#{_tag(tutor.teaching_mode.title())}",
        f"#{_tag(tutor.status.title())}",
    ]
    if len(subjects) > 1:
        tags.extend(f"#{_tag(item.subject.title())}" for item in subjects[1:6])
    return " ".join(tags)


def format_tutor_record(tutor: Tutor) -> str:
    """Structured tutor record text stored in the archive channel."""
    currency = Currency.ETB if is_ethiopia(tutor.country) else Currency.USD
    primary_rate = tutor.etb_rate if is_ethiopia(tutor.country) else tutor.usd_rate

    lines = [
        "🎓 NEW TUTOR APPLICATION",
        record_hashtags(tutor),
        "",
        f"TDR ID: {tutor.public_tutor_id}",
        f"Full name: {tutor.name}",
        f"Display name: {tutor.display_name}",
        f"Phone: {tutor.phone}",
        f"Email: {tutor.email}",
        f"Country: {tutor.country}",
        f"City: {tutor.city}",
        f"Status: {tutor.status}",
        f"Teaching mode: {tutor.teaching_mode}",
        f"Experience: {tutor.experience_years} year(s)",
        f"Timezone: {tutor.timezone}",
        "",
        f"Subjects: {', '.join(tutor.subject_list) or '—'}",
        f"Levels: {', '.join(tutor.level_list) or '—'}",
        f"Languages: {', '.join(tutor.language_list) or '—'}",
        "",
        f"Primary rate ({currency.value}): "
        f"{format_rate(primary_rate, currency)}",
        f"ETB rate: {format_rate(tutor.etb_rate, Currency.ETB)}",
        f"USD rate: {format_rate(tutor.usd_rate, Currency.USD)}",
        "",
    ]

    for index, record in enumerate(tutor.education or [], start=1):
        lines.append(
            f"Education {index}: {record.degree} — {record.field}, "
            f"{record.institution}"
            + (f" ({record.graduation_year})" if record.graduation_year else "")
        )
    if not tutor.education:
        lines.append("Education: —")

    if tutor.availability:
        for slot in tutor.availability:
            lines.append(
                f"Availability: {slot.day} {slot.start_time}-{slot.end_time} "
                f"({slot.timezone})"
            )
    else:
        lines.append("Availability: —")

    lines += ["", f"Bio: {tutor.bio or '—'}"]
    return "\n".join(lines)


def format_request_record(request: StudentRequest) -> str:
    tags = " ".join(
        [
            f"#{_tag(request.public_request_id.replace('-', ''))}",
            f"#{_tag(request.subject.title())}",
            f"#{_tag(request.country.title())}",
            f"#{_tag(request.mode.title())}",
            f"#{_tag(request.currency)}",
        ]
    )
    body = student_service.format_request_summary(request)
    return f"🔎 NEW TUTOR REQUEST\n{tags}\n\n{body}"


def format_ticket_record(ticket: SupportTicket) -> str:
    tags = f"#{_tag(ticket.public_ticket_id.replace('-', ''))} #{_tag(ticket.category)}"
    return (
        "🛠 SUPPORT TICKET\n"
        f"{tags}\n\n"
        f"Ticket: {ticket.public_ticket_id}\n"
        f"Category: {ticket.category}\n"
        f"From: {ticket.username or ticket.telegram_user_fullname or 'unknown'}\n"
        f"Status: {ticket.status}\n\n"
        f"{ticket.message}"
    )


def _extract_file_reference(message: Any) -> tuple[str | None, str | None]:
    """Pull the ``(file_id, file_unique_id)`` pair Telegram assigned."""
    photo = getattr(message, "photo", None)
    if photo:
        return photo[-1].file_id, photo[-1].file_unique_id
    document = getattr(message, "document", None)
    if document is not None:
        return document.file_id, document.file_unique_id
    voice = getattr(message, "voice", None)
    if voice is not None:
        return voice.file_id, voice.file_unique_id
    video = getattr(message, "video", None)
    if video is not None:
        return video.file_id, video.file_unique_id
    return None, None


def _persist_document_refs(
    session: Any, tutor: Tutor, stored: Sequence[StoredDocument]
) -> None:
    """Requirement 4.3: persist message/file/chat references into SQLite."""
    for item in stored:
        existing = next(
            (
                doc
                for doc in tutor.documents
                if doc.document_type == item.document_type
            ),
            None,
        )
        if existing is None:
            tutor_service.record_document_reference(
                session,
                tutor,
                document_type=item.document_type,
                file_id=item.telegram_file_id,
                file_unique_id=item.telegram_file_unique_id,
                message_id=item.telegram_message_id,
                chat_id=item.storage_chat_id,
            )
        else:
            existing.telegram_file_id = item.telegram_file_id
            existing.telegram_file_unique_id = item.telegram_file_unique_id
            existing.telegram_message_id = item.telegram_message_id
            existing.storage_chat_id = item.storage_chat_id
            existing.uploaded_at = utcnow()


class AbstractStorageService(ABC):
    """Archive interface (Requirement 12.2)."""

    @abstractmethod
    async def store_tutor_record(
        self, tdr_id: str, documents: Sequence[Any] | None = None
    ) -> StorageResult:
        """Archive a tutor record plus each document as separate messages."""

    @abstractmethod
    async def update_tutor_record_status(self, tdr_id: str) -> StorageResult:
        """Refresh the archived record after a status change."""

    @abstractmethod
    async def store_student_request(self, request_id: str) -> StorageResult:
        """Archive a student/parent tutor request."""

    @abstractmethod
    async def store_support_ticket(self, ticket_id: str) -> StorageResult:
        """Archive a support ticket."""


class TelegramStorageService(AbstractStorageService):
    """Telegram implementation of :class:`AbstractStorageService`."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int | None = None,
        request_chat_id: int | None = None,
        support_chat_id: int | None = None,
        max_attempts: int | None = None,
        backoff_seconds: float | None = None,
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id if chat_id is not None else settings.tutor_storage_chat_id
        self.request_chat_id = (
            request_chat_id
            if request_chat_id is not None
            else settings.student_request_chat_id
        )
        self.support_chat_id = (
            support_chat_id if support_chat_id is not None else settings.support_chat_id
        )
        self.max_attempts = max(1, max_attempts or settings.storage_max_attempts)
        self.backoff_seconds = (
            backoff_seconds if backoff_seconds is not None else settings.storage_backoff_seconds
        )

    # -- retry helper ---------------------------------------------------
    async def _with_retry(
        self, operation: Callable[[], Awaitable[Any]], description: str
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await operation()
            except RETRYABLE_ERRORS as exc:  # pragma: no branch
                last_error = exc
                logger.warning(
                    "Telegram storage attempt %s/%s failed for %s: %s",
                    attempt,
                    self.max_attempts,
                    description,
                    type(exc).__name__,
                )
                if attempt < self.max_attempts:
                    await asyncio.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
        raise StorageError(f"{description} failed after {self.max_attempts} attempts") from last_error

    # -- tutor records --------------------------------------------------
    async def store_tutor_record(
        self, tdr_id: str, documents: Sequence[Any] | None = None
    ) -> StorageResult:
        with session_scope() as session:
            tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
            if tutor is None:
                logger.warning("Storage requested for unknown tutor")
                return StorageResult(success=False, error="tutor not found")
            text = format_tutor_record(tutor)
            refs = [DocumentRef.coerce(item) for item in (documents or [])]

        try:
            message = await self._with_retry(
                lambda: self.bot.send_message(
                    chat_id=self.chat_id, text=text, parse_mode=ParseMode.HTML
                ),
                f"tutor record {tdr_id}",
            )
        except StorageError as exc:
            await self._mark_failed(tdr_id, str(exc))
            return StorageResult(success=False, error=str(exc), chat_id=self.chat_id)

        stored: list[StoredDocument] = []
        for ref in refs:
            try:
                sent = await self._with_retry(
                    lambda ref=ref: self._send_document(ref, tdr_id),
                    f"document {ref.document_type} for {tdr_id}",
                )
            except StorageError as exc:
                logger.error("Telegram storage failure for %s", tdr_id)
                await self._mark_failed(tdr_id, str(exc))
                return StorageResult(
                    success=False,
                    error=str(exc),
                    chat_id=self.chat_id,
                    record_message_id=getattr(message, "message_id", None),
                )
            stored.append(sent)

        with session_scope() as session:
            tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
            if tutor is not None:
                tutor_service.set_storage_references(
                    session, tutor, self.chat_id, getattr(message, "message_id", None)
                )
                tutor.storage_status = str(StorageStatus.STORED)
                photo = next(
                    (
                        item
                        for item in stored
                        if item.document_type == str(DocumentType.PROFILE_PHOTO)
                    ),
                    None,
                )
                if photo is not None:
                    tutor.profile_photo_file_id = photo.telegram_file_id
                _persist_document_refs(session, tutor, stored)
                session.flush()
                logger.info(
                    "Tutor record archived",
                    extra={"tdr_id": tdr_id, "documents": len(stored)},
                )

        return StorageResult(
            success=True,
            chat_id=self.chat_id,
            record_message_id=getattr(message, "message_id", None),
            documents=stored,
        )

    async def update_tutor_record_status(self, tdr_id: str) -> StorageResult:
        """Post a status update message and refresh the archive reference."""
        with session_scope() as session:
            tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
            if tutor is None:
                return StorageResult(success=False, error="tutor not found")
            text = (
                f"🔄 STATUS UPDATE {record_hashtags(tutor)}\n"
                f"TDR ID: {tutor.public_tutor_id}\n"
                f"New status: {tutor.status}\n"
                f"Display name: {tutor.display_name}\n"
                f"Updated: {tutor.updated_at:%Y-%m-%d %H:%M UTC}"
            )
        try:
            message = await self._with_retry(
                lambda: self.bot.send_message(
                    chat_id=self.chat_id, text=text, parse_mode=ParseMode.HTML
                ),
                f"status update for {tdr_id}",
            )
        except StorageError as exc:
            logger.error("Telegram status update failure for %s", tdr_id)
            return StorageResult(success=False, error=str(exc), chat_id=self.chat_id)
        return StorageResult(
            success=True,
            chat_id=self.chat_id,
            record_message_id=getattr(message, "message_id", None),
        )

    async def _send_document(self, ref: DocumentRef, tdr_id: str) -> StoredDocument:
        caption = f"{ref.document_type} • {tdr_id}"
        if ref.document_type == str(DocumentType.PROFILE_PHOTO):
            message = await self.bot.send_photo(
                chat_id=self.chat_id,
                photo=ref.telegram_file_id,
                caption=caption,
            )
        else:
            message = await self.bot.send_document(
                chat_id=self.chat_id,
                document=ref.telegram_file_id,
                caption=caption,
            )
        file_id, file_unique_id = _extract_file_reference(message)
        return StoredDocument(
            document_type=ref.document_type,
            telegram_file_id=file_id or ref.telegram_file_id,
            telegram_file_unique_id=file_unique_id or ref.telegram_file_unique_id,
            telegram_message_id=getattr(message, "message_id", 0) or 0,
            storage_chat_id=self.chat_id,
        )

    async def _mark_failed(self, tdr_id: str, error: str) -> None:
        """Requirement 4.4: keep the SQLite record, mark it, alert the admin."""
        logger.error("Telegram storage failure: %s", error)
        with session_scope() as session:
            tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
            if tutor is not None:
                tutor.storage_status = str(StorageStatus.STORAGE_FAILED)
                session.flush()
        await self._notify_admin_storage_failure(tdr_id, error)

    async def _notify_admin_storage_failure(self, tdr_id: str, error: str) -> None:
        admin_chat = settings.tutor_storage_chat_id
        if not admin_chat:
            return
        try:
            await self.bot.send_message(
                chat_id=admin_chat,
                text=(
                    "⚠️ <b>Storage failure</b>\n"
                    f"TDR ID: {tdr_id}\n"
                    "The SQLite record was kept but the Telegram archive failed."
                ),
                parse_mode=ParseMode.HTML,
            )
        except RETRYABLE_ERRORS:  # pragma: no cover - best effort only
            logger.error("Could not notify admin about storage failure for %s", tdr_id)

    # -- student requests ----------------------------------------------
    async def store_student_request(self, request_id: str) -> StorageResult:
        with session_scope() as session:
            request = student_service.get_request_by_id(session, request_id)
            if request is None:
                return StorageResult(success=False, error="request not found")
            text = format_request_record(request)

        try:
            message = await self._with_retry(
                lambda: self.bot.send_message(
                    chat_id=self.request_chat_id, text=text, parse_mode=ParseMode.HTML
                ),
                f"student request {request_id}",
            )
        except StorageError as exc:
            logger.error("Telegram storage failure for student request")
            with session_scope() as session:
                request = student_service.get_request_by_id(session, request_id)
                if request is not None:
                    request.storage_status = str(StorageStatus.STORAGE_FAILED)
                    session.flush()
            return StorageResult(success=False, error=str(exc))

        message_id = getattr(message, "message_id", None)
        with session_scope() as session:
            request = student_service.get_request_by_id(session, request_id)
            if request is not None:
                student_service.set_request_storage_reference(
                    session, request, self.request_chat_id, message_id
                )
        return StorageResult(
            success=True, chat_id=self.request_chat_id, record_message_id=message_id
        )

    # -- support tickets ------------------------------------------------
    async def store_support_ticket(self, ticket_id: str) -> StorageResult:
        from sqlalchemy import select

        with session_scope() as session:
            ticket = session.execute(
                select(SupportTicket).where(
                    SupportTicket.public_ticket_id == str(ticket_id).upper()
                )
            ).scalar_one_or_none()
            if ticket is None:
                return StorageResult(success=False, error="ticket not found")
            text = format_ticket_record(ticket)

        try:
            message = await self._with_retry(
                lambda: self.bot.send_message(
                    chat_id=self.support_chat_id, text=text, parse_mode=ParseMode.HTML
                ),
                f"support ticket {ticket_id}",
            )
        except StorageError as exc:
            logger.error("Telegram storage failure for support ticket")
            return StorageResult(success=False, error=str(exc))

        message_id = getattr(message, "message_id", None)
        with session_scope() as session:
            ticket = session.execute(
                select(SupportTicket).where(
                    SupportTicket.public_ticket_id == str(ticket_id).upper()
                )
            ).scalar_one_or_none()
            if ticket is not None:
                ticket.admin_chat_id = self.support_chat_id
                ticket.admin_message_id = message_id
                session.flush()
        return StorageResult(
            success=True, chat_id=self.support_chat_id, record_message_id=message_id
        )


def get_storage_service(bot: Bot) -> TelegramStorageService:
    """Factory used by handlers (Requirement 12.2 swap point)."""
    return TelegramStorageService(bot)


__all__ = [
    "AbstractStorageService",
    "DocumentRef",
    "StorageError",
    "StorageResult",
    "StoredDocument",
    "TelegramStorageService",
    "format_request_record",
    "format_ticket_record",
    "format_tutor_record",
    "get_storage_service",
    "record_hashtags",
]