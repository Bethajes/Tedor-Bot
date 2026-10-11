"""Property-based tests for the Mini App document upload endpoint.

Covers:
  Property 10: Document upload rejects invalid file types  (Requirements 5.2, 9.7)
  Property 9:  Document upload stores only metadata        (Requirements 12.1)
  Property 11: Document upload authorization               (Requirements 12.5)
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.api.miniapp.auth import TelegramUser, get_current_telegram_user
from app.api.routes.miniapp import router
from app.database import get_db
from app.enums import DocumentType
from app.models import PendingDocument, TutorDocument
from app.services.tutor_profile_service import (
    ALLOWED_DOCUMENT_EXTENSIONS,
    create_ethiopian_tutor,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_ALLOWED_EXTENSIONS = set(ALLOWED_DOCUMENT_EXTENSIONS)  # {'.pdf', '.jpg', '.jpeg', '.png', '.webp'}
_VALID_DOCUMENT_TYPE = str(DocumentType.UNIVERSITY_TRANSCRIPT)
_SMALL_PDF_BYTES = b"%PDF-1.4 fake content"


def _make_app_for_user(user_id: int, db: Session) -> FastAPI:
    app = FastAPI()

    def _fixed_user() -> TelegramUser:
        return TelegramUser(id=user_id)

    def _fixed_db():
        yield db

    app.dependency_overrides[get_current_telegram_user] = _fixed_user
    app.dependency_overrides[get_db] = _fixed_db
    app.include_router(router)
    return app


def _create_tutor(db: Session, user_id: int) -> Any:
    payload = {
        "telegram_user_id": user_id,
        "full_name": f"Test Tutor {user_id}",
        "phone": "+251911000001",
        "subjects": ["Mathematics"],
        "grades": ["Grade 9-10"],
        "locations": ["Bole"],
        "teaching_mode": "IN_PERSON",
    }
    tutor = create_ethiopian_tutor(db, payload)
    db.commit()
    return tutor


def _upload_file(client: TestClient, filename: str, content: bytes = _SMALL_PDF_BYTES) -> Any:
    return client.post(
        "/api/miniapp/documents/upload",
        data={"document_type": _VALID_DOCUMENT_TYPE},
        files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
    )


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

_user_id = st.integers(min_value=100_000, max_value=2_000_000)

# Generate filenames with random extensions (not in the allowed set)
_disallowed_extension = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz",
    min_size=1,
    max_size=8,
).filter(lambda ext: f".{ext}" not in _ALLOWED_EXTENSIONS)

_disallowed_filename = _disallowed_extension.map(lambda ext: f"document.{ext}")

# Generate filenames with allowed extensions
_allowed_filename = st.sampled_from([
    "transcript.pdf",
    "degree.jpg",
    "certificate.jpeg",
    "scan.png",
    "photo.webp",
])


# ---------------------------------------------------------------------------
# Property 10: Document upload rejects invalid file types
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 10: Document upload rejects invalid file types
@given(user_id=_user_id, filename=_disallowed_filename)
@hyp_settings(max_examples=100, deadline=None)
def test_property_document_upload_rejects_invalid_file_types(
    fresh_db: Any,
    user_id: int,
    filename: str,
) -> None:
    """For any file with an extension not in the allowed set, the upload endpoint
    should return HTTP 422.

    Validates: Requirements 5.2, 9.7
    """
    db: Session = fresh_db()
    _create_tutor(db, user_id)

    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = _upload_file(client, filename)

    assert response.status_code == 422, (
        f"Expected 422 for disallowed file '{filename}', got {response.status_code}: {response.text}"
    )


# ---------------------------------------------------------------------------
# Property 9: Document upload stores only metadata
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 9: Document upload stores only metadata
@given(user_id=_user_id, filename=_allowed_filename)
@hyp_settings(max_examples=50, deadline=None)
def test_property_document_upload_stores_only_metadata(
    fresh_db: Any,
    user_id: int,
    filename: str,
) -> None:
    """After a successful upload, TutorDocument has no raw bytes column —
    only metadata (document_type, telegram_file_id, etc.) is stored.

    Validates: Requirements 12.1
    """
    db: Session = fresh_db()
    _create_tutor(db, user_id)

    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = _upload_file(client, filename, content=_SMALL_PDF_BYTES)

    assert response.status_code == 201, (
        f"Expected 201, got {response.status_code}: {response.text}"
    )

    # Verify the TutorDocument row exists
    doc = db.query(TutorDocument).filter_by(document_type=_VALID_DOCUMENT_TYPE).first()
    assert doc is not None, "TutorDocument row must exist after upload"

    # Verify metadata is stored
    assert doc.document_type == _VALID_DOCUMENT_TYPE

    # Verify no raw bytes column exists on the model (Requirement 12.1)
    column_names = {col.name for col in TutorDocument.__table__.columns}
    raw_byte_column_candidates = {
        "content", "data", "file_data", "raw_bytes", "file_bytes",
        "blob", "binary_data", "file_content",
    }
    assert column_names.isdisjoint(raw_byte_column_candidates), (
        f"TutorDocument should not have any raw bytes column, "
        f"found: {column_names & raw_byte_column_candidates}"
    )

    # Verify the response also only contains metadata
    body = response.json()
    assert "document_type" in body
    assert "telegram_file_id" in body
    for forbidden_key in raw_byte_column_candidates:
        assert forbidden_key not in body, (
            f"Response should not contain raw bytes key '{forbidden_key}'"
        )


# ---------------------------------------------------------------------------
# Property 11: Document upload authorization
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 11: Document upload authorization
@given(
    user_a=st.integers(min_value=100_000, max_value=1_000_000),
    user_b=st.integers(min_value=1_000_001, max_value=2_000_000),
)
@hyp_settings(max_examples=100, deadline=None)
def test_property_document_upload_authorization(
    fresh_db: Any,
    user_a: int,
    user_b: int,
) -> None:
    """For any upload authenticated as user A where no tutor record exists
    for user A (but one exists for user B), the endpoint should return HTTP 404
    (tutor not found).

    When user A has no tutor record and tries to upload, the server should not
    allow the upload (404). If user A tries to reference user B's tutor — which
    is impossible via the API since identity is server-derived — the endpoint
    returns 403.

    This tests that ownership is enforced: only the owner of the tutor record
    can upload documents to it.

    Validates: Requirements 12.5
    """
    db: Session = fresh_db()

    # Create a tutor record for user_b only
    _create_tutor(db, user_b)
    documents_before = db.query(TutorDocument).count()

    # Authenticate as user_a (who has no application yet)
    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = _upload_file(client, "transcript.pdf")

    # A first-time applicant's upload is accepted and staged against *their own*
    # account, which is the only way the form can be finished at all.
    assert response.status_code == 201, response.text
    assert response.json()["pending"] is True

    staged = db.query(PendingDocument).one()
    assert staged.telegram_user_id == user_a, (
        "an upload must be staged against the uploader, never against another user"
    )
    # And nothing was attached to user_b's application.
    assert db.query(TutorDocument).count() == documents_before, (
        "user A's upload was attached to user B's application"
    )


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_upload_without_an_application_is_staged_not_refused(db: Session) -> None:
    """A user with no application yet can still upload — documents are asked
    for *before* the application is submitted.

    Refusing this used to leave the form impossible to finish: every upload
    404'd, so the documents step never completed and Submit was unreachable.

    See tests/test_miniapp_pending_documents.py for the round trip.
    """
    user_id = 9_999_001
    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = _upload_file(client, "document.pdf")

    assert response.status_code == 201, response.text
    assert response.json()["pending"] is True
    # Staged against this user, and attached to nothing yet.
    staged = db.query(PendingDocument).one()
    assert staged.telegram_user_id == user_id
    assert db.query(TutorDocument).count() == 0


def test_upload_valid_file_succeeds(db: Session) -> None:
    """A valid PDF upload for an existing tutor returns 201."""
    user_id = 8_000_001
    _create_tutor(db, user_id)
    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = _upload_file(client, "transcript.pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["document_type"] == _VALID_DOCUMENT_TYPE


def test_upload_rejects_unknown_document_type(db: Session) -> None:
    """Upload returns 422 for an unknown document_type."""
    user_id = 8_000_002
    _create_tutor(db, user_id)
    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.post(
            "/api/miniapp/documents/upload",
            data={"document_type": "INVALID_TYPE"},
            files={"file": ("doc.pdf", io.BytesIO(b"pdf"), "application/pdf")},
        )
    assert response.status_code == 422


def test_upload_rejects_oversized_file(db: Session) -> None:
    """Upload returns 413 when the file exceeds 20 MB."""
    user_id = 8_000_003
    _create_tutor(db, user_id)
    app = _make_app_for_user(user_id, db)
    # Generate a 21 MB payload
    oversized = b"x" * (21 * 1024 * 1024)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.post(
            "/api/miniapp/documents/upload",
            data={"document_type": _VALID_DOCUMENT_TYPE},
            files={"file": ("big.pdf", io.BytesIO(oversized), "application/pdf")},
        )
    assert response.status_code == 413


def test_upload_all_allowed_extensions(db: Session) -> None:
    """Each allowed extension (.pdf, .jpg, .jpeg, .png, .webp) returns 201."""
    user_id = 8_000_004
    _create_tutor(db, user_id)
    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        for ext in ALLOWED_DOCUMENT_EXTENSIONS:
            response = _upload_file(client, f"document{ext}")
            assert response.status_code == 201, (
                f"Expected 201 for extension '{ext}', got {response.status_code}: {response.text}"
            )
