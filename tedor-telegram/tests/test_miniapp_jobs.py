"""Property-based tests for Mini App job endpoints.

Covers:
  Property 12: Job match score is bounded        (Requirements 9.8)
  Property 17: Already-applied status displayed  (Requirements 7.5)
"""

from __future__ import annotations

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
from app.enums import DocumentType, JobApplicationStatus
from app.models import JobApplication, JobPost, Tutor
from app.services import job_application_service as applications
from app.services import job_post_service
from app.services import tutor_profile_service as P
from tests.factories import job_post_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ADMIN_ID = 111
JOB_CHAT_ID = -100777
TUTOR_TELEGRAM_ID = 55_000_001


def _make_app_for_user(user_id: int, db: Session) -> FastAPI:
    """Minimal FastAPI app with the miniapp router wired to a fixed user."""
    app = FastAPI()

    def _fixed_user() -> TelegramUser:
        return TelegramUser(id=user_id)

    def _fixed_db():
        yield db

    app.dependency_overrides[get_current_telegram_user] = _fixed_user
    app.dependency_overrides[get_db] = _fixed_db
    app.include_router(router)
    return app


def _ethiopian_payload(telegram_id: int, **overrides: Any) -> dict[str, Any]:
    """A complete Ethiopian tutor payload that passes profile-completion checks."""
    payload: dict[str, Any] = {
        "telegram_user_id": telegram_id,
        "full_name": f"Test Tutor {telegram_id}",
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
        "teaching_experience_description": "Taught Grade 4-6 maths.",
        "languages": ["Amharic", "English"],
    }
    payload.update(overrides)
    return payload


def _make_tutor(db: Session, telegram_id: int, *, with_documents: bool = True) -> Tutor:
    """Create a complete tutor, optionally with required documents."""
    tutor = P.create_ethiopian_tutor(db, _ethiopian_payload(telegram_id))
    if with_documents:
        for kind in (
            str(DocumentType.UNIVERSITY_TRANSCRIPT),
            str(DocumentType.UNIVERSITY_ENTRANCE_RESULT),
        ):
            P.record_document(db, tutor, kind, f"file-{kind}", f"{kind}.pdf", file_name=f"{kind}.pdf")
    db.flush()
    db.refresh(tutor)
    return tutor


def _make_job(db: Session, **overrides: Any) -> JobPost:
    """Create and publish a job post."""
    payload = job_post_payload(ADMIN_ID, **overrides)
    post = job_post_service.create_job_post(db, payload)
    job_post_service.mark_published(db, post, JOB_CHAT_ID, 9999)
    db.flush()
    db.refresh(post)
    return post


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

_user_id = st.integers(min_value=200_000, max_value=3_000_000)

_job_overrides = st.fixed_dictionaries(
    {},
    optional={
        "location": st.sampled_from(["Ayat Tsebel", "Bole", "CMC", "Megenagna"]),
        "student_level": st.sampled_from(["Grade 4", "Grade 9", "Grade 12", "University"]),
        "subjects": st.sampled_from(["Mathematics", "Physics", "English", "Chemistry"]),
    },
)


# ---------------------------------------------------------------------------
# Property 12: Job match score is bounded
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 12: Job match score is bounded
@given(user_id=_user_id, job_overrides=_job_overrides)
@hyp_settings(max_examples=100, deadline=None)
def test_property_job_match_score_is_bounded(
    fresh_db: Any,
    user_id: int,
    job_overrides: dict,
) -> None:
    """For any tutor/job combination, the score in GET /jobs/{ref} is in [0, 100].

    Validates: Requirements 9.8
    """
    db: Session = fresh_db()

    tutor = _make_tutor(db, user_id)
    tutor.status = "VERIFIED"
    db.flush()

    post = _make_job(db, **job_overrides)
    db.commit()

    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get(f"/api/miniapp/jobs/{post.public_post_id}")

    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )

    body = response.json()
    assert "score" in body, f"'score' missing from response: {body}"
    score = body["score"]
    assert isinstance(score, (int, float)), f"score is not numeric: {score!r}"
    assert 0 <= score <= 100, f"score {score} is out of [0, 100]"


# ---------------------------------------------------------------------------
# Property 17: Already-applied status displayed correctly
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 17: Already-applied status displayed correctly
@given(
    user_id=_user_id,
    terminal_status=st.sampled_from([
        str(JobApplicationStatus.SUBMITTED),
        str(JobApplicationStatus.UNDER_REVIEW),
        str(JobApplicationStatus.SHORTLISTED),
        str(JobApplicationStatus.REJECTED),
    ]),
)
@hyp_settings(max_examples=100, deadline=None)
def test_property_terminal_status_displayed_correctly(
    fresh_db: Any,
    user_id: int,
    terminal_status: str,
) -> None:
    """For each terminal status, GET /jobs/{ref} returns that status, not a score payload.

    Validates: Requirements 7.5
    """
    db: Session = fresh_db()

    tutor = _make_tutor(db, user_id)
    tutor.status = "VERIFIED"
    db.flush()

    post = _make_job(db)

    # Manually create an application row in a terminal state
    application = JobApplication(
        job_post_id=post.id,
        tutor_id=tutor.id,
        status=terminal_status,
        confirmed_submitted=(terminal_status == str(JobApplicationStatus.SUBMITTED)),
        match_score=0.75,
        match_rank=1,
    )
    db.add(application)
    db.commit()

    app = _make_app_for_user(user_id, db)
    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get(f"/api/miniapp/jobs/{post.public_post_id}")

    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )

    body = response.json()

    # Must carry the status
    assert "status" in body, f"'status' missing from terminal response: {body}"
    assert body["status"] == terminal_status, (
        f"Expected status {terminal_status!r}, got {body['status']!r}"
    )

    # Must NOT carry a fresh recommendation (the tutor already applied)
    assert "score" not in body, (
        f"'score' should not be present for a terminal application, got: {body}"
    )
    assert "recommendation" not in body, (
        f"'recommendation' should not be present for a terminal application, got: {body}"
    )
