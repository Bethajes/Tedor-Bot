"""Property-based test for cross-user request isolation.

Covers:
   Property 3: Cross-user request isolation  (Requirements 2.6, 12.3)
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings as hyp_settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.api.miniapp.auth import TelegramUser, get_current_telegram_user
from app.api.routes import miniapp as miniapp_routes
from app.api.routes.miniapp import router
from app.database import get_db
from app.enums import DocumentType, JobApplicationStatus
from app.models import Draft, JobApplication
from app.services import draft_service
from app.services import job_application_service as applications
from app.services import job_post_service
from app.services.tutor_profile_service import create_ethiopian_tutor
from tests.factories import job_post_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _create_tutor(db: Session, user_id: int) -> Any:
    payload = {
        "telegram_user_id": user_id,
        "full_name": f"Tutor {user_id}",
        "phone": "+251911000001",
        "subjects": ["Mathematics"],
        "grades": ["Grade 9-10"],
        "locations": ["Bole"],
        "teaching_mode": "IN_PERSON",
    }
    tutor = create_ethiopian_tutor(db, payload)
    db.commit()
    return tutor


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Two non-overlapping ranges ensure we always get distinct IDs.
_user_a = st.integers(min_value=100_000, max_value=1_000_000)
_user_b = st.integers(min_value=1_000_001, max_value=2_000_000)


# ---------------------------------------------------------------------------
# Property 3: Cross-user request isolation
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 3: Cross-user request isolation
@given(user_a=_user_a, user_b=_user_b)
@hyp_settings(max_examples=100, deadline=None)
def test_property_cross_user_profile_isolation(
    fresh_db: Any,
    user_a: int,
    user_b: int,
) -> None:
    """For any two distinct users A and B, when authenticated as A, calling
    GET /api/miniapp/tutor/profile must not return user B's data.

    The server must either return 404 (no record for A) or, if A also has a
    record, return only A's own data — never B's data.

    Validates: Requirements 2.6, 12.3
    """
    db: Session = fresh_db()

    # Create a tutor record for user_b only.
    tutor_b = _create_tutor(db, user_b)

    # Authenticate as user_a — who has no record.
    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/api/miniapp/tutor/profile")

    # user_a has no tutor record, so the server must return 404 — never 200.
    # Getting a 200 would mean user A received some data (possibly B's).
    assert response.status_code == 404, (
        f"User {user_a} should get 404 (no record), not {response.status_code}. "
        f"Receiving 200 could mean user B's data was leaked."
    )

    # Extra guard: even if the server returns 200, the telegram_user_id in the
    # response must NOT be user B's.
    if response.status_code == 200:
        body = response.json()
        # The profile endpoint strips telegram_user_id from the response, but
        # if it were ever present it must not be user_b.
        assert body.get("telegram_user_id") != user_b, (
            f"Response contains user B's telegram_user_id — data was leaked"
        )


# Feature: tedor-mini-app, Property 3: Cross-user request isolation
@given(user_a=_user_a, user_b=_user_b)
@hyp_settings(max_examples=100, deadline=None)
def test_property_cross_user_draft_isolation(
    fresh_db: Any,
    user_a: int,
    user_b: int,
) -> None:
    """For any two distinct users A and B, when authenticated as A, calling
    GET /api/miniapp/tutor/draft must not return user B's draft.

    Validates: Requirements 2.6, 12.3
    """
    db: Session = fresh_db()

    # Save a draft for user_b.
    draft_service.save_draft(db, user_b, "tutor", {"step": 1, "owner": user_b})
    db.commit()

    # Authenticate as user_a — who has no draft.
    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/api/miniapp/tutor/draft")

    # user_a has no draft, so the server must return 404.
    assert response.status_code == 404, (
        f"User {user_a} should get 404 (no draft), not {response.status_code}. "
        f"Getting 200 could mean user B's draft was leaked."
    )

    # Extra guard: if somehow a 200 is returned, the payload must not contain
    # user_b's owner marker.
    if response.status_code == 200:
        body = response.json()
        assert body.get("owner") != user_b, (
            f"Response contains user B's owner field — draft data was leaked"
        )


# Feature: tedor-mini-app, Property 3: Cross-user request isolation
@given(user_a=_user_a, user_b=_user_b)
@hyp_settings(max_examples=100, deadline=None)
def test_property_cross_user_parent_draft_isolation(
    fresh_db: Any,
    user_a: int,
    user_b: int,
) -> None:
    """For any two distinct users A and B, when authenticated as A, calling
    GET /api/miniapp/parent/draft must not return user B's parent draft.

    Validates: Requirements 2.6, 12.3
    """
    db: Session = fresh_db()

    # Save a parent draft for user_b.
    draft_service.save_draft(db, user_b, "parent", {"parent_name": "Owner B", "owner": user_b})
    db.commit()

    # Authenticate as user_a — who has no draft.
    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/api/miniapp/parent/draft")

    # user_a has no draft, so the server must return 404.
    assert response.status_code == 404, (
        f"User {user_a} should get 404 (no draft), not {response.status_code}. "
        f"Getting 200 could mean user B's parent draft was leaked."
    )

    if response.status_code == 200:
        body = response.json()
        assert body.get("owner") != user_b, (
            "Response contains user B's owner field — parent draft data was leaked"
        )


# ---------------------------------------------------------------------------
# Unit tests: explicit ownership guards
# ---------------------------------------------------------------------------


def test_tutor_profile_returns_own_data(db: Session) -> None:
    """A user with a tutor record gets their own profile data, not someone else's."""
    user_a = 3_001_001
    user_b = 3_001_002

    _create_tutor(db, user_a)
    _create_tutor(db, user_b)

    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/api/miniapp/tutor/profile")

    assert response.status_code == 200
    body = response.json()
    # The response must not contain user B's name
    assert f"Tutor {user_b}" not in body.get("name", ""), (
        "Profile returned user B's name to user A"
    )
    # Confirm it is user A's record (name contains user A's id)
    assert f"Tutor {user_a}" in body.get("name", ""), (
        "Profile did not return user A's own name"
    )


def test_tutor_draft_returns_own_draft_not_others(db: Session) -> None:
    """A user gets their own draft; another user's draft is not accessible."""
    user_a = 3_002_001
    user_b = 3_002_002

    draft_service.save_draft(db, user_a, "tutor", {"step": 5, "note": "mine"})
    draft_service.save_draft(db, user_b, "tutor", {"step": 7, "note": "not mine"})
    db.commit()

    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/api/miniapp/tutor/draft")

    assert response.status_code == 200
    body = response.json()
    assert body.get("note") == "mine", (
        f"User A received wrong draft: {body}"
    )
    assert body.get("step") == 5, (
        f"User A received wrong step: {body}"
    )


# ---------------------------------------------------------------------------
# Property 3 (guard form): a resolved resource that belongs to another user
# is refused with 403, never read or mutated.
# ---------------------------------------------------------------------------

_JOB_CHAT_ID = -100776


def _complete_payload(user_id: int) -> dict[str, Any]:
    """A complete Ethiopian application payload for the given user."""
    return {
        "telegram_user_id": user_id,
        "full_name": f"Tutor {user_id}",
        "phone": "+251911234567",
        "gender": "Male",
        "age": 27,
        "current_address": "Bole, Addis Ababa",
        "locations": ["Ayat Tsebel"],
        "grades": ["Grade 4", "Grade 5"],
        "subjects": ["Mathematics"],
        "english_proficiency": 9,
        "university": "AASTU",
        "department": "Mathematics",
        "education_level": "UNIVERSITY_GRADUATE",
        "university_year": 2019,
        "cgpa": 3.75,
        "entrance_exam_score": 612,
        "entrance_exam_year": 2016,
        "entrance_exam_type": "EHE",
        "entrance_exam_max_score": 840,
        "teaching_experience_years": 3,
        "teaching_experience_description": "Taught Grade 4-6 mathematics.",
        "languages": ["Amharic", "English"],
    }


def _create_complete_tutor(db: Session, user_id: int) -> Any:
    """A tutor record that satisfies every profile-completion requirement."""
    tutor = create_ethiopian_tutor(db, _complete_payload(user_id))
    for kind in (
        str(DocumentType.UNIVERSITY_TRANSCRIPT),
        str(DocumentType.UNIVERSITY_ENTRANCE_RESULT),
    ):
        from app.services.tutor_profile_service import record_document

        record_document(db, tutor, kind, f"file-{kind}", f"{kind}.pdf", file_name=f"{kind}.pdf")
    db.flush()
    db.refresh(tutor)
    return tutor


def _create_draft(db: Session, user_id: int, draft_type: str) -> Draft:
    draft = Draft(
        telegram_user_id=user_id,
        draft_type=draft_type,
        payload=json.dumps({"owner": user_id, "step": 2}),
    )
    db.add(draft)
    db.flush()
    return draft


def _create_published_job(db: Session) -> Any:
    payload = job_post_payload(111)
    post = job_post_service.create_job_post(db, payload)
    job_post_service.mark_published(db, post, _JOB_CHAT_ID, 7777)
    db.flush()
    db.refresh(post)
    return post


# Feature: tedor-mini-app, Property 3: Cross-user request isolation
@given(user_a=_user_a, user_b=_user_b)
@hyp_settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_cross_user_profile_guard_returns_403(
    fresh_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    user_a: int,
    user_b: int,
) -> None:
    """When the profile lookup resolves a row owned by user B while the request
    is authenticated as user A, the endpoint must answer 403.

    The lookup is stubbed to return B's row: that is exactly the failure mode
    the guard exists for, and the test pins the 403 rather than a silent read.

    Validates: Requirements 2.6, 12.3
    """
    db: Session = fresh_db()
    tutor_b = _create_tutor(db, user_b)

    monkeypatch.setattr(
        miniapp_routes, "get_tutor_by_telegram_id", lambda *_args, **_kwargs: tutor_b
    )

    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/api/miniapp/tutor/profile")

    assert response.status_code == 403, (
        f"User {user_a} requesting user {user_b}'s profile must be 403, "
        f"got {response.status_code}"
    )


# Feature: tedor-mini-app, Property 3: Cross-user request isolation
@given(user_a=_user_a, user_b=_user_b)
@hyp_settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_cross_user_tutor_draft_guard_returns_403(
    fresh_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    user_a: int,
    user_b: int,
) -> None:
    """A tutor draft resolved for user B must be refused with 403 for user A.

    Validates: Requirements 2.6, 12.3
    """
    db: Session = fresh_db()
    draft_b = _create_draft(db, user_b, "tutor")

    monkeypatch.setattr(
        miniapp_routes.draft_service, "get_draft", lambda *_args, **_kwargs: draft_b
    )

    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/api/miniapp/tutor/draft")

    assert response.status_code == 403, (
        f"User {user_a} reading user {user_b}'s draft must be 403, "
        f"got {response.status_code}"
    )
    assert response.json().get("detail") != {"owner": user_b}


# Feature: tedor-mini-app, Property 3: Cross-user request isolation
@given(user_a=_user_a, user_b=_user_b)
@hyp_settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_cross_user_parent_draft_guard_returns_403(
    fresh_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    user_a: int,
    user_b: int,
) -> None:
    """A parent draft resolved for user B must be refused with 403 for user A.

    Validates: Requirements 2.6, 12.3
    """
    db: Session = fresh_db()
    draft_b = _create_draft(db, user_b, "parent")

    monkeypatch.setattr(
        miniapp_routes.draft_service, "get_draft", lambda *_args, **_kwargs: draft_b
    )

    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/api/miniapp/parent/draft")

    assert response.status_code == 403, (
        f"User {user_a} reading user {user_b}'s parent draft must be 403, "
        f"got {response.status_code}"
    )


# Feature: tedor-mini-app, Property 3: Cross-user request isolation
@given(user_a=_user_a, user_b=_user_b)
@hyp_settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_cross_user_draft_write_guard_returns_403(
    fresh_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    user_a: int,
    user_b: int,
) -> None:
    """Saving a draft that resolves to user B's row must be refused with 403.

    Validates: Requirements 2.6, 12.3, 12.4
    """
    db: Session = fresh_db()
    draft_b = _create_draft(db, user_b, "tutor")

    monkeypatch.setattr(
        miniapp_routes.draft_service, "save_draft", lambda *_args, **_kwargs: draft_b
    )

    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.post("/api/miniapp/tutor/draft", json={"step": 1})

    assert response.status_code == 403, (
        f"User {user_a} writing into user {user_b}'s draft must be 403, "
        f"got {response.status_code}"
    )


# Feature: tedor-mini-app, Property 3: Cross-user request isolation
@given(user_a=_user_a, user_b=_user_b)
@hyp_settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_cross_user_document_upload_guard_returns_403(
    fresh_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    user_a: int,
    user_b: int,
) -> None:
    """Uploading a document against user B's tutor record must be refused 403.

    Validates: Requirements 2.6, 12.3, 12.5
    """
    db: Session = fresh_db()
    tutor_b = _create_tutor(db, user_b)

    monkeypatch.setattr(
        miniapp_routes, "get_tutor_by_telegram_id", lambda *_args, **_kwargs: tutor_b
    )

    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.post(
            "/api/miniapp/documents/upload",
            files={"file": ("transcript.pdf", b"%PDF-1.4 fake", "application/pdf")},
            data={"document_type": "UNIVERSITY_TRANSCRIPT"},
        )

    assert response.status_code == 403, (
        f"User {user_a} uploading to user {user_b}'s application must be 403, "
        f"got {response.status_code}"
    )


# Feature: tedor-mini-app, Property 3: Cross-user request isolation
@given(user_a=_user_a, user_b=_user_b)
@hyp_settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_cross_user_job_details_guard_returns_403(
    fresh_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    user_a: int,
    user_b: int,
) -> None:
    """Job details resolved against user B's tutor row must be refused 403.

    Validates: Requirements 2.6, 12.3
    """
    db: Session = fresh_db()
    tutor_b = _create_complete_tutor(db, user_b)
    post = _create_published_job(db)

    monkeypatch.setattr(
        miniapp_routes, "get_tutor_by_telegram_id", lambda *_args, **_kwargs: tutor_b
    )

    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get(f"/api/miniapp/jobs/{post.public_post_id}")

    assert response.status_code == 403, (
        f"User {user_a} reading a job through user {user_b}'s profile must be 403, "
        f"got {response.status_code}"
    )


# Feature: tedor-mini-app, Property 3: Cross-user request isolation
@given(user_a=_user_a, user_b=_user_b)
@hyp_settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_cross_user_job_confirm_guard_returns_403(
    fresh_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    user_a: int,
    user_b: int,
) -> None:
    """Confirming a job application that belongs to user B must be refused 403.

    Validates: Requirements 2.6, 12.3
    """
    db: Session = fresh_db()
    tutor_b = _create_complete_tutor(db, user_b)
    post = _create_published_job(db)
    application_b = applications.start_or_get_application(db, tutor_b, post)[0]
    db.flush()

    monkeypatch.setattr(
        miniapp_routes, "get_tutor_by_telegram_id", lambda *_args, **_kwargs: tutor_b
    )
    monkeypatch.setattr(
        miniapp_routes.applications,
        "get_application",
        lambda *_args, **_kwargs: application_b,
    )

    app = _make_app_for_user(user_a, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.post(f"/api/miniapp/jobs/{post.public_post_id}/confirm")

    assert response.status_code == 403, (
        f"User {user_a} confirming user {user_b}'s application must be 403, "
        f"got {response.status_code}"
    )
    # The refused submission must never have been written through.
    assert applications.get_application(db, post.public_post_id, tutor_b) is not None
    assert application_b.status != JobApplicationStatus.SUBMITTED
