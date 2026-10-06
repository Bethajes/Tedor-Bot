"""Telegram storage service tests (Requirement 4.x).

Includes the property derived from Requirement 4.3: after
``store_tutor_record()`` every document row must carry the Telegram message and
file references in SQLite.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from hypothesis import given, settings as hypothesis_settings, strategies as st
from telegram.error import TimedOut

from app.database import session_scope
from app.enums import DocumentType, StorageStatus, TutorStatus
from app.services import tutor_service
from app.services.storage_service import (
    StorageResult,
    TelegramStorageService,
    format_tutor_record,
    record_hashtags,
)
from tests.factories import create_tutor

PROFILE = hypothesis_settings(max_examples=100, deadline=None)


def _file_message(message_id: int, file_id: str, unique_id: str):
    return SimpleNamespace(
        message_id=message_id,
        document=SimpleNamespace(file_id=file_id, file_unique_id=unique_id),
        photo=None,
        voice=None,
        video=None,
    )


def _photo_message(message_id: int, file_id: str, unique_id: str):
    size = SimpleNamespace(file_id=file_id, file_unique_id=unique_id)
    return SimpleNamespace(
        message_id=message_id, document=None, photo=[size], voice=None, video=None
    )


class FakeBot:
    """Minimal ``Bot`` stand-in capturing every call."""

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.attempts = 0
        self.messages: list[tuple[int, str]] = []
        self.documents: list[tuple[int, str]] = []
        self.photos: list[tuple[int, str]] = []
        self._next_id = 1000

    def _maybe_fail(self) -> None:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise TimedOut("simulated timeout")

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self._maybe_fail()
        self.messages.append((chat_id, text))
        return SimpleNamespace(message_id=self._id())

    async def send_document(self, chat_id: int, document: str, **kwargs):
        self._maybe_fail()
        self.documents.append((chat_id, document))
        message_id = self._id()
        return _file_message(message_id, f"stored-{document}", f"uniq-{document}")

    async def send_photo(self, chat_id: int, photo: str, **kwargs):
        self._maybe_fail()
        self.photos.append((chat_id, photo))
        message_id = self._id()
        return _photo_message(message_id, f"stored-{photo}", f"uniq-photo-{photo}")


def _documents(count: int) -> list[dict[str, str]]:
    types = [
        DocumentType.PROFILE_PHOTO,
        DocumentType.CV,
        DocumentType.DEGREE,
        DocumentType.SUPPORTING,
    ]
    documents = []
    for index in range(count):
        document_type = str(types[index % len(types)])
        documents.append(
            {
                "document_type": document_type,
                "telegram_file_id": f"file-{index}",
                "telegram_file_unique_id": f"uniq-{index}",
            }
        )
    return documents


# ---------------------------------------------------------------------------
# property from Requirement 4.3
# ---------------------------------------------------------------------------


@given(
    document_count=st.integers(min_value=0, max_value=4),
    seed=st.integers(min_value=1, max_value=10**6),
)
@PROFILE
@pytest.mark.asyncio
async def test_property_telegram_references_saved_to_sqlite(
    fresh_db, document_count: int, seed: int
) -> None:
    """Feature: tedor-telegram-bot, Property (Requirement 4.3).

    For any tutor with N documents, every ``TutorDocument`` row must carry a non
    null ``telegram_file_id`` and ``telegram_message_id`` after archiving.
    """
    session = fresh_db()
    tutor = create_tutor(session, -seed - 1)
    session.commit()
    tdr_id = tutor.public_tutor_id

    bot = FakeBot()
    service = TelegramStorageService(bot, chat_id=-5001, backoff_seconds=0)
    result = await service.store_tutor_record(tdr_id, _documents(document_count))

    assert result.success, result.error
    with session_scope() as check:
        stored = tutor_service.get_tutor_by_tdr_id(check, tdr_id)
        rows = stored.documents
        assert len(rows) == document_count
        for row in rows:
            assert row.telegram_file_id
            assert row.telegram_message_id
            assert row.storage_chat_id == -5001
        assert stored.storage_status == StorageStatus.STORED
        assert stored.telegram_storage_message_id == result.record_message_id
        assert stored.telegram_storage_chat_id == -5001


# ---------------------------------------------------------------------------
# record formatting and hashtags (Requirements 4.1, 4.5)
# ---------------------------------------------------------------------------


def test_record_contains_hashtags_in_required_format(fresh_db) -> None:
    session = fresh_db()
    tutor = create_tutor(session, -4242)
    text = format_tutor_record(tutor)
    assert re.search(r"#TDR\d{6}\b", text)
    assert "#Mathematics" in text
    assert "#Ethiopia" in text
    assert "#Online" in text
    assert "#Pending" in text
    assert tutor.public_tutor_id in text
    assert "Addis Ababa University" in text


def test_hashtags_helper(fresh_db) -> None:
    session = fresh_db()
    tutor = create_tutor(session, -4243)
    session.commit()
    tags = record_hashtags(tutor).split()
    assert tags[0] == f"#{tutor.public_tutor_id.replace('-', '')}"
    assert len(tags) >= 5


# ---------------------------------------------------------------------------
# retry and failure behaviour (Requirement 4.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_retries_then_succeeds(fresh_db) -> None:
    session = fresh_db()
    tutor = create_tutor(session, -5150)
    session.commit()
    tdr_id = tutor.public_tutor_id

    bot = FakeBot(fail_times=2)
    service = TelegramStorageService(bot, chat_id=-5002, max_attempts=3, backoff_seconds=0)
    result = await service.store_tutor_record(tdr_id, [])

    assert isinstance(result, StorageResult)
    assert result.success
    assert bot.attempts == 3


@pytest.mark.asyncio
async def test_storage_failure_marks_record_and_keeps_row(fresh_db) -> None:
    session = fresh_db()
    tutor = create_tutor(session, -6260)
    session.commit()
    tdr_id = tutor.public_tutor_id

    bot = FakeBot(fail_times=99)
    service = TelegramStorageService(
        bot, chat_id=-5003, max_attempts=3, backoff_seconds=0, support_chat_id=None
    )
    result = await service.store_tutor_record(tdr_id, [])

    assert not result.success
    # three record attempts plus the best effort admin alert
    assert bot.attempts >= 3
    with session_scope() as check:
        stored = tutor_service.get_tutor_by_tdr_id(check, tdr_id)
        assert stored is not None, "the SQLite record must not be deleted"
        assert stored.storage_status == StorageStatus.STORAGE_FAILED
        assert stored.status == TutorStatus.PENDING


@pytest.mark.asyncio
async def test_status_update_posts_archive_message(fresh_db) -> None:
    session = fresh_db()
    tutor = create_tutor(session, -7370)
    session.commit()
    tdr_id = tutor.public_tutor_id
    tutor_service.update_tutor_status(session, tdr_id, TutorStatus.VERIFIED)
    session.commit()

    bot = FakeBot()
    service = TelegramStorageService(bot, chat_id=-5004, backoff_seconds=0)
    result = await service.update_tutor_record_status(tdr_id)
    assert result.success
    assert "VERIFIED" in bot.messages[-1][1]


@pytest.mark.asyncio
async def test_storage_for_unknown_tutor_reports_failure(fresh_db) -> None:
    fresh_db()
    service = TelegramStorageService(FakeBot(), chat_id=-5005, backoff_seconds=0)
    result = await service.store_tutor_record("TDR-000999", [])
    assert result.success is False
    assert result.error == "tutor not found"