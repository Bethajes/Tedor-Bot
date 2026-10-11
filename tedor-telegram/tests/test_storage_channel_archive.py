"""Everything the Mini App accepts must end up in the Telegram archive channel.

The database used to look full while the channel stayed empty: the REST layer
read each upload into memory, checked its size, threw the bytes away, and
stored a ``None`` file ID behind a comment promising an "async archive job"
that was never written. No application submitted through the Mini App was
ever archived at all, so tutors stayed ``PENDING`` forever and the channel
— meant to be the database — held nothing but the first bot-sourced record.

Validates: Requirements 4.3, 12.1, 12.2
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from telegram.error import NetworkError

from app.api.miniapp.auth import TelegramUser, get_current_telegram_user
from app.api.routes.miniapp import router
from app.database import get_db
from app.enums import DocumentType, StorageStatus
from app.models import PendingDocument, Tutor, TutorDocument
from app.services import storage_service
from app.services.tutor_profile_service import create_ethiopian_tutor

_REQUIRED = (
    str(DocumentType.UNIVERSITY_TRANSCRIPT),
    str(DocumentType.UNIVERSITY_ENTRANCE_RESULT),
)


def _app(user_id: int, db: Session) -> FastAPI:
    app = FastAPI()

    def _user() -> TelegramUser:
        return TelegramUser(id=user_id)

    def _db():
        yield db

    app.dependency_overrides[get_current_telegram_user] = _user
    app.dependency_overrides[get_db] = _db
    app.include_router(router)
    return app


def _upload(
    client: TestClient,
    document_type: str,
    filename: str = "transcript.pdf",
    content: bytes = b"%PDF-1.4 fake",
):
    return client.post(
        "/api/miniapp/documents/upload",
        files={"file": (filename, content, "application/pdf")},
        data={"document_type": document_type},
    )


def _payload(user_id: int) -> dict[str, Any]:
    return {
        "telegram_user_id": user_id,
        "full_name": "Hilina Fisseha",
        "phone": "+251911234567",
        "locations": ["Ayat Tsebel"],
        "grades": ["Grade 4"],
        "subjects": ["Mathematics"],
    }


# ---------------------------------------------------------------------------
# The upload itself must reach Telegram
# ---------------------------------------------------------------------------


def test_uploaded_bytes_are_sent_to_the_channel(db: Session, fake_telegram_bot: Any) -> None:
    """The regression: the bytes used to be read and then dropped on the floor."""
    client = TestClient(_app(8201, db))

    response = _upload(client, _REQUIRED[0], content=b"%PDF-1.4 real content")

    assert response.status_code == 201, response.text
    assert len(fake_telegram_bot.sent_documents) == 1
    sent = fake_telegram_bot.sent_documents[0]
    assert sent["bytes"] == b"%PDF-1.4 real content"
    assert sent["chat_id"] == fake_telegram_bot.chat_id
    assert _REQUIRED[0] in sent["caption"]


def test_stored_file_id_is_the_one_telegram_returned(
    db: Session, fake_telegram_bot: Any
) -> None:
    """Requirement 4.3: the stored reference must address a real message."""
    client = TestClient(_app(8202, db))

    response = _upload(client, _REQUIRED[0])

    assert response.status_code == 201, response.text
    staged = db.query(PendingDocument).one()
    assert staged.telegram_file_id == "doc-file-id"
    assert staged.telegram_file_unique_id == "doc-unique-id"
    assert staged.telegram_message_id is not None
    assert staged.storage_chat_id == fake_telegram_bot.chat_id


def test_a_staged_upload_is_not_stored_with_a_null_file_id(
    db: Session, fake_telegram_bot: Any
) -> None:
    """Every tutor whose first upload predates their application, too."""
    client = TestClient(_app(8203, db))
    _upload(client, _REQUIRED[0])

    staged = db.query(PendingDocument).one()
    assert staged.telegram_file_id, "the archive reference was dropped"
    assert fake_telegram_bot.sent_documents, "nothing reached the channel"


def test_an_upload_attached_to_an_existing_tutor_keeps_its_archive_reference(
    db: Session, fake_telegram_bot: Any
) -> None:
    create_ethiopian_tutor(db, _payload(8204))
    db.commit()

    client = TestClient(_app(8204, db))
    response = _upload(client, _REQUIRED[0])

    assert response.status_code == 201, response.text
    doc = db.query(TutorDocument).one()
    assert doc.telegram_file_id == "doc-file-id"
    assert doc.storage_chat_id == fake_telegram_bot.chat_id


def test_a_photo_upload_is_archived_as_a_photo(
    db: Session, fake_telegram_bot: Any
) -> None:
    """A profile photo stays a viewable photo in the archive, not a file blob."""
    client = TestClient(_app(8205, db))

    response = client.post(
        "/api/miniapp/documents/upload",
        files={"file": ("me.png", b"\x89PNG fake", "image/png")},
        data={"document_type": str(DocumentType.PROFILE_PHOTO)},
    )

    assert response.status_code == 201, response.text
    assert fake_telegram_bot.sent_documents[0]["filename"] is None
    assert db.query(PendingDocument).one().telegram_file_id == "photo-file-id"


def test_an_upload_is_refused_when_it_cannot_be_archived(
    db: Session, fake_telegram_bot: Any
) -> None:
    """Requirement 12.1 leaves nowhere else to keep the bytes.

    Storing a null reference is what made the channel look empty while the
    tutor believed they had uploaded their transcript, so a failed archive has
    to surface as a failed upload rather than a silent one.
    """
    fake_telegram_bot.always_fail = NetworkError("telegram is down")
    client = TestClient(_app(8206, db))

    response = _upload(client, _REQUIRED[0])

    assert response.status_code == 503, response.text
    assert db.query(PendingDocument).count() == 0, "a lost file was still recorded"


# ---------------------------------------------------------------------------
# Submission must archive the record, not just store it
# ---------------------------------------------------------------------------


def test_submitting_posts_the_record_into_the_channel(
    db: Session, fake_telegram_bot: Any
) -> None:
    """The other half of the bug: no submission was ever archived."""
    client = TestClient(_app(8207, db))
    for kind in _REQUIRED:
        assert _upload(client, kind, filename=f"{kind}.pdf").status_code == 201

    response = client.post("/api/miniapp/tutor/submit", json=_payload(8207))
    assert response.status_code == 201, response.text

    assert fake_telegram_bot.sent_messages, "the record message was never posted"
    text = fake_telegram_bot.sent_messages[0]["text"]
    assert "NEW TUTOR APPLICATION" in text
    assert response.json()["public_tutor_id"] in text


def test_a_submitted_tutor_is_marked_stored(db: Session, fake_telegram_bot: Any) -> None:
    client = TestClient(_app(8208, db))
    response = client.post("/api/miniapp/tutor/submit", json=_payload(8208))
    assert response.status_code == 201, response.text

    tutor = db.query(Tutor).filter_by(
        public_tutor_id=response.json()["public_tutor_id"]
    ).one()
    assert tutor.storage_status == str(StorageStatus.STORED)
    assert tutor.telegram_storage_chat_id == fake_telegram_bot.chat_id
    assert tutor.telegram_storage_message_id is not None


def test_documents_are_not_posted_twice(db: Session, fake_telegram_bot: Any) -> None:
    """An upload already in the channel must not be copied on every submit."""
    client = TestClient(_app(8209, db))
    for kind in _REQUIRED:
        assert _upload(client, kind, filename=f"{kind}.pdf").status_code == 201
    uploads_before = len(fake_telegram_bot.sent_documents)

    response = client.post("/api/miniapp/tutor/submit", json=_payload(8209))
    assert response.status_code == 201, response.text

    assert len(fake_telegram_bot.sent_documents) == uploads_before, (
        "already-archived documents were posted again"
    )


def test_a_failed_archive_does_not_lose_the_application(
    db: Session, fake_telegram_bot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 4.4: the SQLite record stays, the tutor keeps their TDR ID."""

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("archive is down")

    monkeypatch.setattr(
        storage_service.TelegramStorageService, "store_tutor_record", _explode
    )
    client = TestClient(_app(8210, db))

    response = client.post("/api/miniapp/tutor/submit", json=_payload(8210))

    assert response.status_code == 201, response.text
    assert response.json()["public_tutor_id"]
    assert db.query(Tutor).count() == 1


# ---------------------------------------------------------------------------
# The skip logic itself
# ---------------------------------------------------------------------------


def test_a_document_with_an_archive_reference_is_not_resent() -> None:
    """The guard that keeps the channel free of duplicate copies."""
    ref = storage_service.DocumentRef(
        document_type=str(DocumentType.CV),
        telegram_file_id="file",
        telegram_message_id=42,
        storage_chat_id=-100,
    )
    assert ref.is_archived is True


def test_a_document_without_an_archive_reference_must_be_sent() -> None:
    assert storage_service.DocumentRef("CV", "file").is_archived is False
    assert storage_service.DocumentRef("CV", "", telegram_message_id=42).is_archived is False


def test_refs_are_built_for_every_attached_document(db: Session) -> None:
    tutor = create_ethiopian_tutor(db, _payload(8211))
    db.commit()

    assert storage_service.tutor_document_refs(tutor) == []


def test_refs_include_the_voice_introduction(db: Session) -> None:
    tutor = create_ethiopian_tutor(db, _payload(8212))
    tutor.english_voice_file_id = "voice-file-id"
    db.commit()

    refs = storage_service.tutor_document_refs(tutor)
    assert [ref.document_type for ref in refs] == [str(DocumentType.ENGLISH_VOICE)]
    assert refs[0].telegram_file_id == "voice-file-id"