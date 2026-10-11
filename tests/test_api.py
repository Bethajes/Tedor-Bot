"""REST API tests (Properties 5, 7 and 11).

Feature: tedor-telegram-bot, Property 5: Verified-only public API
Validates: Requirements 10.1, 10.3

Feature: tedor-telegram-bot, Property 7: No private data in public API response
Validates: Requirements 10.7

Feature: tedor-telegram-bot, Property 11: API authentication
Validates: Requirements 10.6
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings as hypothesis_settings, strategies as st

from app.api.schemas import TutorPublicResponse, TutorSearchParams
from app.enums import TutorStatus
from tests.factories import create_tutor

PROFILE = hypothesis_settings(max_examples=100, deadline=None)

FORBIDDEN_FIELDS = (
    "telegram_user_id",
    "phone",
    "email",
    "telegram_storage_chat_id",
    "telegram_storage_message_id",
    "bot_token",
    "api_secret",
)

PRIVATE_PHONE = "+251998877665"
PRIVATE_EMAIL = "private.person@example.com"


def _seed_mixed_statuses(fresh_db, count: int = 6, seed: int = 1) -> int:
    """Seed tutors with mixed statuses and return the verified count."""
    session = fresh_db()
    statuses = [
        TutorStatus.VERIFIED,
        TutorStatus.PENDING,
        TutorStatus.REJECTED,
        TutorStatus.UNDER_REVIEW,
        TutorStatus.SUSPENDED,
        TutorStatus.VERIFIED,
    ]
    verified_expected = sum(1 for index in range(count) if statuses[index % 6] == TutorStatus.VERIFIED)
    for index in range(count):
        create_tutor(
            session,
            -(seed * 100 + index + 1),
            status=statuses[index % len(statuses)],
            country="Ethiopia" if index % 2 == 0 else "Kenya",
            subjects=["Mathematics"] if index % 3 else ["Physics"],
            levels=["Grade 9-10"] if index % 2 else ["University"],
            languages=["English"] if index % 2 else ["Amharic"],
            teaching_mode="ONLINE" if index % 3 else "BOTH",
            phone=PRIVATE_PHONE,
            email=PRIVATE_EMAIL,
            usd_rate=10.0 + index,
            etb_rate=500.0 + index,
        )
    session.commit()
    return verified_expected


# ---------------------------------------------------------------------------
# Property 5 — verified only
# ---------------------------------------------------------------------------


@given(
    count=st.integers(min_value=2, max_value=8),
    seed=st.integers(min_value=1, max_value=10**6),
)
@PROFILE
def test_property_verified_only_public_api(client, fresh_db, count: int, seed: int) -> None:
    """Feature: tedor-telegram-bot, Property 5: Verified-only public API."""
    expected_verified = _seed_mixed_statuses(fresh_db, count=count, seed=seed)

    listing = client.get("/api/tutors")
    assert listing.status_code == 200
    payload = listing.json()
    assert all(item["verified"] for item in payload["items"])
    assert payload["total"] == expected_verified

    search = client.get("/api/tutors/search?subject=Mathematics")
    assert search.status_code == 200
    for item in search.json()["items"]:
        assert item["verified"] is True


def test_detail_endpoint_only_returns_verified_tutors(client, fresh_db) -> None:
    session = fresh_db()
    verified = create_tutor(session, -3001, status=TutorStatus.VERIFIED)
    pending = create_tutor(session, -3002, status=TutorStatus.PENDING)
    session.commit()

    ok = client.get(f"/api/tutors/{verified.public_tutor_id}")
    assert ok.status_code == 200
    assert ok.json()["verified"] is True

    blocked = client.get(f"/api/tutors/{pending.public_tutor_id}")
    assert blocked.status_code == 404
    assert client.get("/api/tutors/TDR-999999").status_code == 404


def test_search_soundness_through_api(client, fresh_db) -> None:
    _seed_mixed_statuses(fresh_db, count=6)
    params = TutorSearchParams(subject="Mathematics", country="Ethiopia", mode="ONLINE")
    response = client.get(
        "/api/tutors/search",
        params={
            "subject": params.subject,
            "country": params.country,
            "mode": params.mode,
        },
    )
    assert response.status_code == 200
    for item in response.json()["items"]:
        assert item["verified"]
        assert "Mathematics" in item["subjects"]
        assert item["country"] == "Ethiopia"
        assert item["teachingMode"] == "ONLINE"


# ---------------------------------------------------------------------------
# Property 7 — no private data
# ---------------------------------------------------------------------------


@given(
    count=st.integers(min_value=1, max_value=4),
    seed=st.integers(min_value=1, max_value=10**6),
)
@PROFILE
def test_property_no_private_data_in_public_response(
    client, fresh_db, count: int, seed: int
) -> None:
    """Feature: tedor-telegram-bot, Property 7: No private data in public API."""
    session = fresh_db()
    tdr_ids = []
    for index in range(count):
        tutor = create_tutor(
            session,
            -(seed * 100 + index + 1),
            status=TutorStatus.VERIFIED,
            phone=PRIVATE_PHONE,
            email=PRIVATE_EMAIL,
        )
        tdr_ids.append(tutor.public_tutor_id)
    session.commit()

    listing = client.get("/api/tutors")
    assert listing.status_code == 200
    body = listing.text
    for field in FORBIDDEN_FIELDS:
        assert field not in body, f"{field} leaked into the public payload"
    assert PRIVATE_PHONE not in body
    assert PRIVATE_EMAIL not in body

    for tdr_id in tdr_ids:
        detail = client.get(f"/api/tutors/{tdr_id}")
        assert detail.status_code == 200
        payload = detail.json()
        for field in FORBIDDEN_FIELDS:
            assert field not in payload
        assert set(payload) <= set(TutorPublicResponse.model_fields)


def test_schema_declares_no_private_fields() -> None:
    declared = set(TutorPublicResponse.model_fields)
    for field in FORBIDDEN_FIELDS:
        assert field not in declared


# ---------------------------------------------------------------------------
# Property 11 — authentication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Secret": "wrong"}, {"X-API-Secret": ""}, {"X-Api-Secret": "nope"}],
)
def test_property_api_authentication_rejects_invalid_secret(
    client, fresh_db, headers: dict[str, str]
) -> None:
    """Feature: tedor-telegram-bot, Property 11: API authentication."""
    session = fresh_db()
    tutor = create_tutor(session, -4001, status=TutorStatus.VERIFIED)
    session.commit()

    response = client.get(f"/api/tutors/{tutor.public_tutor_id}/documents", headers=headers)
    assert response.status_code == 401


def test_property_api_authentication_accepts_valid_secret(client, fresh_db) -> None:
    session = fresh_db()
    tutor = create_tutor(
        session,
        -4002,
        status=TutorStatus.VERIFIED,
        documents=[{"document_type": "CV", "telegram_file_id": "file-1"}],
    )
    session.commit()

    response = client.get(
        f"/api/tutors/{tutor.public_tutor_id}/documents",
        headers={"X-API-Secret": "test-secret-value"},
    )
    assert response.status_code == 200
    assert response.json()[0]["documentType"] == "CV"


def test_public_endpoints_do_not_require_the_secret(client, fresh_db) -> None:
    session = fresh_db()
    create_tutor(session, -4003, status=TutorStatus.VERIFIED)
    session.commit()
    for path in ("/api/tutors", "/api/tutors/search", "/api/subjects", "/api/health"):
        assert client.get(path).status_code == 200


# ---------------------------------------------------------------------------
# remaining endpoints
# ---------------------------------------------------------------------------


def test_health_endpoint(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_distinct_value_endpoints(client, fresh_db) -> None:
    _seed_mixed_statuses(fresh_db, count=6)
    for path in ("/api/subjects", "/api/levels", "/api/countries", "/api/languages"):
        response = client.get(path)
        assert response.status_code == 200
        assert isinstance(response.json()["values"], list)

    countries = client.get("/api/countries").json()["values"]
    assert set(countries) == {"Ethiopia", "Kenya"}


def test_photo_endpoint_reports_missing_photo(client, fresh_db) -> None:
    session = fresh_db()
    tutor = create_tutor(session, -4100, status=TutorStatus.VERIFIED)
    session.commit()
    response = client.get(f"/api/tutors/{tutor.public_tutor_id}/photo")
    assert response.status_code == 404


def test_pagination_metadata(client, fresh_db) -> None:
    session = fresh_db()
    for index in range(5):
        create_tutor(session, -(4200 + index), status=TutorStatus.VERIFIED)
    session.commit()
    response = client.get("/api/tutors", params={"page": 2, "pageSize": 2})
    body = response.json()
    assert body["page"] == 2
    assert body["pageSize"] == 2
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert json.dumps(body)  # the payload is JSON serialisable