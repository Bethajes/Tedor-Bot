"""Admin-only job-match API.

Feature: tutor-onboarding-and-matching, §25
Validates: Requirement 25 (website API), Requirement 24 (privacy)
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.services import job_match_pipeline as pipeline
from tests.factories import tutor_payload
from tests.test_job_candidates import publish_job
from tests.test_matching_service import ideal_tutor

API_HEADERS = {"X-API-Secret": "test-secret-value"}

FORBIDDEN_PUBLIC_FIELDS = (
    "phone",
    "email",
    "telegram_user_id",
    "telegram_storage_chat_id",
    "english_voice",
    "member_referral",
    "details",
    "api_secret",
)


def _match(client, job_id: str, headers=None, **params):
    return client.get(
        f"/api/jobs/{job_id}/matches", headers=headers or API_HEADERS, params=params
    )


def test_matches_require_the_api_secret(client) -> None:
    response = client.get("/api/jobs/JOB-000001/matches")
    assert response.status_code == 401


def test_matches_are_ordered_and_explain(client, fresh_db) -> None:
    session = fresh_db()
    post = publish_job(session)
    for index in range(3):
        ideal_tutor(session, -8601 - index, english_proficiency=10 - index)
    pipeline.run_match_for_job(session, post)

    response = _match(client, post.public_post_id)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    scores = [item["match_score"] for item in body["candidates"]]
    assert scores == sorted(scores, reverse=True)
    candidate = body["candidates"][0]
    assert candidate["explanation"]["strengths"]
    assert candidate["explanation"]["criteria"]


def test_rejected_candidates_are_hidden_by_default(client, fresh_db) -> None:
    session = fresh_db()
    post = publish_job(session)
    a = ideal_tutor(session, -8611)
    b = ideal_tutor(session, -8612)
    pipeline.run_match_for_job(session, post)
    pipeline.set_candidate_status(session, post.public_post_id, b.public_tutor_id, "REJECTED")
    session.commit()

    response = _match(client, post.public_post_id)
    ids = [item["tutor_id"] for item in response.json()["candidates"]]
    assert b.public_tutor_id not in ids

    response = _match(client, post.public_post_id, include_ignored=True)
    ids = [item["tutor_id"] for item in response.json()["candidates"]]
    assert b.public_tutor_id in ids


def test_match_detail_requires_authorization(client, fresh_db) -> None:
    session = fresh_db()
    post = publish_job(session)
    tutor = ideal_tutor(session, -8621)
    pipeline.run_match_for_job(session, post)
    assert client.get(f"/api/jobs/{post.public_post_id}/matches/{tutor.public_tutor_id}").status_code == 401


def test_match_detail_returns_the_explanation(client, fresh_db) -> None:
    session = fresh_db()
    post = publish_job(session)
    tutor = ideal_tutor(session, -8622)
    pipeline.run_match_for_job(session, post)
    response = client.get(
        f"/api/jobs/{post.public_post_id}/matches/{tutor.public_tutor_id}",
        headers=API_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tutor_id"] == tutor.public_tutor_id
    assert body["match_score"] > 0
    assert body["explanation"]["criteria"]["grade"]["score"] >= 0.99


def test_unknown_job_returns_404(client) -> None:
    assert _match(client, "JOB-999999").status_code == 404


def test_public_tutor_endpoints_still_never_leak_private_data(client, fresh_db) -> None:
    """The new fields on Tutor must not leak through the public API."""
    import json as jsonlib

    session = fresh_db()
    tutor = ideal_tutor(session, -8633)
    tutor.status = "VERIFIED"
    tutor.english_voice_file_id = "private-voice-id"
    session.commit()

    public = client.get(f"/api/tutors/{tutor.public_tutor_id}")
    assert public.status_code == 200
    body = jsonlib.dumps(public.json())
    for field in FORBIDDEN_PUBLIC_FIELDS:
        assert field not in body, f"public profile leaked {field}"