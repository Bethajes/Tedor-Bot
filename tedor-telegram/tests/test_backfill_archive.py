"""Backfilling tutors that were stored but never archived.

Validates: Requirements 4.3, 12.2
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.backfill_archive import (
    _MIN_FILE_ID_LENGTH,
    find_pending,
    run_backfill,
    usable_refs,
)
from app.enums import DocumentType, StorageStatus
from app.services.storage_service import DocumentRef

_A_REAL_FILE_ID = "BQACAgQAAxkBAAMuasJ-9iY--iyIebOKwiTfrQ6o"


def _payload(user_id: int) -> dict[str, Any]:
    return {
        "telegram_user_id": user_id,
        "full_name": "Barkot Wondwosen",
        "phone": "+251911000099",
        "locations": ["Ayat Tsebel"],
        "grades": ["Grade 4"],
        "subjects": ["Mathematics"],
    }


def _make_tutor(db: Session, user_id: int, status: str = StorageStatus.PENDING) -> Any:
    from app.services.tutor_profile_service import create_ethiopian_tutor

    tutor = create_ethiopian_tutor(db, _payload(user_id))
    tutor.storage_status = str(status)
    db.commit()
    return tutor


def test_only_unarchived_tutors_are_picked_up(db: Session) -> None:
    _make_tutor(db, 8301, status=StorageStatus.PENDING)
    _make_tutor(db, 8302, status=StorageStatus.STORED)

    pending = find_pending(db)

    assert [t.telegram_user_id for t in pending] == [8301]


def test_a_real_reference_is_postable() -> None:
    postable, damaged = usable_refs(
        [DocumentRef(str(DocumentType.CV), _A_REAL_FILE_ID)]
    )
    assert len(postable) == 1
    assert damaged == []


def test_a_placeholder_reference_is_reported_not_posted() -> None:
    """The old Mini App wrote ``'f'`` as a file ID; Telegram rejects those."""
    postable, damaged = usable_refs(
        [DocumentRef(str(DocumentType.UNIVERSITY_TRANSCRIPT), "f")]
    )
    assert postable == []
    assert "UNIVERSITY_TRANSCRIPT" in damaged[0]


def test_a_file_already_in_the_channel_is_still_postable() -> None:
    """Already-archived documents are skipped by the send, not by this filter."""
    postable, damaged = usable_refs(
        [
            DocumentRef(
                str(DocumentType.CV),
                _A_REAL_FILE_ID,
                telegram_message_id=7,
                storage_chat_id=-100,
            )
        ]
    )
    assert len(postable) == 1
    assert damaged == []


def test_the_placeholder_threshold_is_never_truncating_a_real_id() -> None:
    assert len(_A_REAL_FILE_ID) > _MIN_FILE_ID_LENGTH


async def test_a_dry_run_sends_nothing(db: Session, fake_telegram_bot: Any) -> None:
    from app.models import Tutor

    _make_tutor(db, 8303)

    report = await run_backfill(dry_run=True)

    assert report.archived == []
    assert fake_telegram_bot.sent_messages == []
    assert fake_telegram_bot.sent_documents == []
    stored = db.query(Tutor).filter_by(telegram_user_id=8303).one()
    assert stored.storage_status == str(StorageStatus.PENDING)


async def test_a_backfill_archives_the_record_and_marks_it_stored(
    db: Session, fake_telegram_bot: Any
) -> None:
    _make_tutor(db, 8304)

    report = await run_backfill()

    assert len(report.archived) == 1, report.failed
    assert fake_telegram_bot.sent_messages, "nothing was posted to the channel"

    from app.models import Tutor

    stored = db.query(Tutor).filter_by(telegram_user_id=8304).one()
    assert stored.storage_status == str(StorageStatus.STORED)


async def test_a_damaged_tutor_is_archived_without_its_placeholder(
    db: Session, fake_telegram_bot: Any
) -> None:
    """The record is still worth having even when a document is unrecoverable."""
    from app.models import TutorDocument

    tutor = _make_tutor(db, 8305)
    db.add(
        TutorDocument(
            tutor_id=tutor.id,
            document_type=str(DocumentType.UNIVERSITY_TRANSCRIPT),
            telegram_file_id="f",
        )
    )
    db.commit()

    report = await run_backfill()

    assert report.archived, "the record should still be archived"
    assert "UNIVERSITY_TRANSCRIPT" in report.damaged[tutor.public_tutor_id][0]
    assert fake_telegram_bot.sent_documents == [], "a placeholder was sent to Telegram"