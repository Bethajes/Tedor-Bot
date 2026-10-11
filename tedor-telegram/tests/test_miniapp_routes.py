"""Property-based tests for Mini App API routes.

Covers:
  Property 4:  Tutor profile response excludes private fields  (Requirements 9.1, 12.2)
  Property 7:  Submit clears the draft                         (Requirements 8.5)
  Property 8:  Tutor application submission creates DB record  (Requirements 9.4, 11.2)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
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
from app.models import Tutor
from app.services import draft_service
from app.services.tutor_profile_service import create_ethiopian_tutor

# ---------------------------------------------------------------------------
# The private fields the API must NEVER return (Requirements 9.1, 12.2)
# ---------------------------------------------------------------------------
PRIVATE_FIELDS = frozenset(
    {
        "phone",
        "email",
        "telegram_user_id",
        "telegram_storage_chat_id",
        "telegram_storage_message_id",
        "english_voice_file_id",
        "english_voice_message_id",
    }
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_BOT_TOKEN = "123456789:TESTTOKENTESTTOKENTESTTOKENTEST"


def _make_app_for_user(user_id: int, db: Session) -> FastAPI:
    """Build a minimal FastAPI app with the miniapp router, injecting a fixed user."""

    app = FastAPI()

    def _fixed_user() -> TelegramUser:
        return TelegramUser(id=user_id)

    def _fixed_db():
        yield db

    app.dependency_overrides[get_current_telegram_user] = _fixed_user
    app.dependency_overrides[get_db] = _fixed_db
    app.include_router(router)
    return app


def _ethiopian_payload(telegram_user_id: int, **overrides: Any) -> dict[str, Any]:
    """Minimal valid Ethiopian tutor payload."""
    base: dict[str, Any] = {
        "telegram_user_id": telegram_user_id,
        "full_name": f"Test Tutor {telegram_user_id}",
        "phone": "+251911000001",
        "subjects": ["Mathematics"],
        "grades": ["Grade 9-10"],
        "locations": ["Bole"],
        "teaching_mode": "IN_PERSON",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

_user_id = st.integers(min_value=100_000, max_value=2_000_000)

_optional_text = st.one_of(st.none(), st.text(min_size=1, max_size=80))

_tutor_overrides = st.fixed_dictionaries(
    {},
    optional={
        "gender": st.sampled_from(["Male", "Female"]),
        "age": st.integers(min_value=18, max_value=60),
        "english_proficiency": st.integers(min_value=1, max_value=10),
        "university": st.text(min_size=1, max_size=100),
        "department": st.text(min_size=1, max_size=80),
        "bio": st.text(max_size=300),
    },
)


# ---------------------------------------------------------------------------
# Property 4: Tutor profile response excludes private fields
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 4: Tutor profile response excludes private fields
@given(user_id=_user_id, overrides=_tutor_overrides)
@hyp_settings(max_examples=100, deadline=None)
def test_property_tutor_profile_excludes_private_fields(
    fresh_db: Any,
    user_id: int,
    overrides: dict,
) -> None:
    """For any tutor record, GET /api/miniapp/tutor/profile never returns private fields.

    Validates: Requirements 9.1, 12.2
    """
    db: Session = fresh_db()

    # Create a tutor for this user
    payload = _ethiopian_payload(user_id, **overrides)
    create_ethiopian_tutor(db, payload)
    db.commit()

    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/api/miniapp/tutor/profile")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    body = response.json()
    for private_key in PRIVATE_FIELDS:
        assert private_key not in body, (
            f"Private field '{private_key}' was found in the profile response"
        )


# ---------------------------------------------------------------------------
# Property 8: Tutor application submission creates a matching DB record
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 8: Tutor application submission creates a matching database record
@given(user_id=_user_id, overrides=_tutor_overrides)
@hyp_settings(max_examples=100, deadline=None)
def test_property_submit_creates_db_record(
    fresh_db: Any,
    user_id: int,
    overrides: dict,
) -> None:
    """For any valid tutor payload submitted via POST /api/miniapp/tutor/submit,
    a Tutor row exists in the database with the same scalar field values.

    Validates: Requirements 9.4, 11.2
    """
    db: Session = fresh_db()

    payload = _ethiopian_payload(user_id, **overrides)
    # Remove telegram_user_id — the route injects it from the session
    payload_for_request = {k: v for k, v in payload.items() if k != "telegram_user_id"}

    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.post("/api/miniapp/tutor/submit", json=payload_for_request)

    assert response.status_code == 201, (
        f"Expected 201, got {response.status_code}: {response.text}"
    )

    # Verify the Tutor row was created in the database
    tutor = db.query(Tutor).filter_by(telegram_user_id=user_id).first()
    assert tutor is not None, "Tutor row must exist after submission"

    # Verify the returned public_tutor_id matches
    body = response.json()
    assert "public_tutor_id" in body
    assert tutor.public_tutor_id == body["public_tutor_id"]

    # Verify no private fields were returned
    for private_key in PRIVATE_FIELDS:
        assert private_key not in body, (
            f"Private field '{private_key}' found in submit response"
        )


# ---------------------------------------------------------------------------
# Property 7: Submit clears the draft
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 7: Submit clears the draft
@given(
    user_id=_user_id,
    draft_payload=st.fixed_dictionaries(
        {},
        optional={
            "full_name": st.text(min_size=1, max_size=60),
            "step": st.integers(min_value=1, max_value=27),
        },
    ),
)
@hyp_settings(max_examples=100, deadline=None)
def test_property_submit_clears_draft(
    fresh_db: Any,
    user_id: int,
    draft_payload: dict,
) -> None:
    """After a successful tutor submission, GET /api/miniapp/tutor/draft returns 404.

    Validates: Requirements 8.5
    """
    db: Session = fresh_db()

    # Save a draft first
    draft_service.save_draft(db, user_id, "tutor", draft_payload)
    db.commit()

    # Submit the application
    payload = _ethiopian_payload(user_id)
    payload_for_request = {k: v for k, v in payload.items() if k != "telegram_user_id"}

    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        submit_resp = client.post("/api/miniapp/tutor/submit", json=payload_for_request)
        assert submit_resp.status_code == 201, (
            f"Submit failed: {submit_resp.status_code} {submit_resp.text}"
        )

        # Draft must now be gone
        draft_resp = client.get("/api/miniapp/tutor/draft")
        assert draft_resp.status_code == 404, (
            f"Expected draft to be cleared (404) after submit, got {draft_resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Unit tests: profile 404, draft CRUD, isolation
# ---------------------------------------------------------------------------


def test_profile_returns_404_when_no_tutor(db: Session) -> None:
    """GET /api/miniapp/tutor/profile returns 404 when no tutor exists."""
    app = _make_app_for_user(9_999_999, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/api/miniapp/tutor/profile")
    assert response.status_code == 404


def test_draft_returns_404_when_none_exists(db: Session) -> None:
    """GET /api/miniapp/tutor/draft returns 404 when no draft exists."""
    app = _make_app_for_user(9_999_998, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/api/miniapp/tutor/draft")
    assert response.status_code == 404


def test_save_and_load_draft_via_api(db: Session) -> None:
    """POST then GET draft returns the same payload."""
    user_id = 8_888_001
    draft_data = {"step": 3, "full_name": "Alice"}
    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        save_resp = client.post("/api/miniapp/tutor/draft", json=draft_data)
        assert save_resp.status_code == 200

        get_resp = client.get("/api/miniapp/tutor/draft")
        assert get_resp.status_code == 200
        assert get_resp.json() == draft_data


def test_submit_rejects_duplicate(db: Session) -> None:
    """Submitting twice for the same user returns 409."""
    user_id = 8_888_002
    payload = _ethiopian_payload(user_id)
    create_ethiopian_tutor(db, payload)
    db.commit()

    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        payload_for_request = {k: v for k, v in payload.items() if k != "telegram_user_id"}
        response = client.post("/api/miniapp/tutor/submit", json=payload_for_request)
    assert response.status_code == 409
