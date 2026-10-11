"""Documents uploaded before the tutor application exists.

The Mini App asks for academic documents as the second to last step, so a
first-time applicant uploads them while there is no ``Tutor`` row to attach
them to. Refusing those uploads left the form impossible to finish: every
upload 404'd, the documents step never completed, and Next did nothing.

Validates: Requirements 5.1, 5.6, 8.4, 9.4
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings as hyp_settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.api.miniapp.auth import TelegramUser, get_current_telegram_user
from app.api.routes.miniapp import router
from app.database import get_db
from app.enums import DocumentType
from app.models import PendingDocument, TutorDocument
from app.services import pending_document_service
from app.services.tutor_profile_service import create_ethiopian_tutor

REQUIRED = (
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
        "full_name": "Abebe Bekele",
        "phone": "+251911234567",
        "locations": ["Ayat Tsebel"],
        "grades": ["Grade 4"],
        "subjects": ["Mathematics"],
    }


def test_an_upload_with_no_application_yet_is_accepted(db: Session) -> None:
    """The bug: this used to 404, which made the form impossible to finish."""
    client = TestClient(_app(8001, db))

    response = _upload(client, str(DocumentType.UNIVERSITY_TRANSCRIPT))

    assert response.status_code == 201, response.text
    assert response.json()["pending"] is True
    assert db.query(PendingDocument).count() == 1


def test_every_required_document_uploads_before_submitting(db: Session) -> None:
    client = TestClient(_app(8002, db))

    for kind in REQUIRED:
        response = _upload(client, kind, filename=f"{kind}.pdf")
        assert response.status_code == 201, response.text

    assert db.query(PendingDocument).count() == 2


def test_submitting_attaches_the_staged_documents(db: Session) -> None:
    """The whole point: the uploads must end up on the application."""
    client = TestClient(_app(8003, db))
    for kind in REQUIRED:
        assert _upload(client, kind, filename=f"{kind}.pdf").status_code == 201

    response = client.post("/api/miniapp/tutor/submit", json=_payload(8003))
    assert response.status_code == 201, response.text

    tutor_id = response.json()["public_tutor_id"]
    stored = {doc.document_type for doc in db.query(TutorDocument).all()}
    assert stored == set(REQUIRED), f"only {stored} were attached for {tutor_id}"
    # The staged rows are gone: nothing is stored twice.
    assert db.query(PendingDocument).count() == 0


def test_an_existing_application_still_attaches_directly(db: Session) -> None:
    """A returning tutor's upload is not staged — there is a record to hold it."""
    create_ethiopian_tutor(db, _payload(8004))
    db.commit()

    client = TestClient(_app(8004, db))
    response = _upload(client, str(DocumentType.UNIVERSITY_TRANSCRIPT))

    assert response.status_code == 201, response.text
    assert response.json()["pending"] is False
    assert db.query(PendingDocument).count() == 0
    assert db.query(TutorDocument).count() == 1


def test_re_uploading_a_type_replaces_rather_than_duplicates(db: Session) -> None:
    client = TestClient(_app(8005, db))

    _upload(client, str(DocumentType.UNIVERSITY_TRANSCRIPT), filename="first.pdf")
    _upload(client, str(DocumentType.UNIVERSITY_TRANSCRIPT), filename="second.pdf")

    staged = db.query(PendingDocument).one()
    assert staged.file_name == "second.pdf"


def test_one_user_cannot_attach_anothers_staged_documents(db: Session) -> None:
    """Staging is scoped to the authenticated user (Requirements 2.6, 12.3)."""
    pending_document_service.stage_document(
        db, 8006, str(DocumentType.UNIVERSITY_TRANSCRIPT), file_name="theirs.pdf"
    )
    db.commit()

    # User 8007 submits: only their own staged documents may be promoted.
    client = TestClient(_app(8007, db))
    response = client.post("/api/miniapp/tutor/submit", json=_payload(8007))

    assert response.status_code == 201, response.text
    assert db.query(TutorDocument).count() == 0
    assert db.query(PendingDocument).count() == 1


def test_staged_types_are_listed_for_the_ui(db: Session) -> None:
    """The upload screen marks these as done instead of asking again."""
    client = TestClient(_app(8008, db))
    _upload(client, str(DocumentType.UNIVERSITY_TRANSCRIPT))

    assert pending_document_service.staged_document_types(db, 8008) == [
        str(DocumentType.UNIVERSITY_TRANSCRIPT)
    ]


# Feature: tedor-mini-app, Property: documents uploaded before submitting are never lost
@given(
    user_id=st.integers(min_value=8100, max_value=8999),
    kinds=st.lists(
        st.sampled_from([str(kind) for kind in DocumentType]),
        min_size=1,
        max_size=4,
        unique=True,
    ),
)
@hyp_settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_staged_documents_reach_the_application(
    fresh_db: Any,
    monkeypatch: Any,
    user_id: int,
    kinds: list[str],
) -> None:
    """Whatever a tutor uploads before submitting, every one of it is attached.

    Validates: Requirements 5.1, 5.6, 9.4
    """
    db: Session = fresh_db()
    client = TestClient(_app(user_id, db))

    for kind in kinds:
        response = _upload(client, kind, filename=f"{kind}.pdf")
        assert response.status_code == 201, response.text

    response = client.post("/api/miniapp/tutor/submit", json=_payload(user_id))
    assert response.status_code == 201, response.text

    attached = {doc.document_type for doc in db.query(TutorDocument).all()}
    assert attached == set(kinds)
    assert db.query(PendingDocument).count() == 0

# ---------------------------------------------------------------------------
# A malformed attachment must never cost a tutor their whole application
# ---------------------------------------------------------------------------


def test_an_unreadable_document_entry_does_not_fail_the_submission(
    db: Session,
) -> None:
    """A 26-step application is not worth losing to one bad attachment.

    The Mini App used to send its own UI-shaped list (``documentType``), which
    the service could not read. That raised, and the tutor was told 422 after
    finishing everything. Unreadable entries are now skipped and logged.
    """
    client = TestClient(_app(8100, db))

    payload = _payload(8100)
    payload["documents"] = [{"documentType": "UNIVERSITY_TRANSCRIPT", "label": "x"}]

    response = client.post("/api/miniapp/tutor/submit", json=payload)

    assert response.status_code == 201, response.text
    assert response.json()["public_tutor_id"]
    # Nothing was attached from the unreadable entry, and nothing blew up.
    assert db.query(TutorDocument).count() == 0


def test_staged_documents_are_attached_even_with_a_malformed_entry(
    db: Session,
) -> None:
    """The good upload still lands; only the unreadable one is dropped."""
    client = TestClient(_app(8101, db))
    assert _upload(client, str(DocumentType.UNIVERSITY_TRANSCRIPT)).status_code == 201

    payload = _payload(8101)
    payload["documents"] = [{"documentType": "UNIVERSITY_TRANSCRIPT"}]

    response = client.post("/api/miniapp/tutor/submit", json=payload)

    assert response.status_code == 201, response.text
    stored = {doc.document_type for doc in db.query(TutorDocument).all()}
    assert stored == {str(DocumentType.UNIVERSITY_TRANSCRIPT)}
