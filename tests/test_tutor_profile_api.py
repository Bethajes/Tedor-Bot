"""The public structured tutor profile endpoint (specification section 25).

The endpoint exists so the website can show real qualifications. The risk it
carries is publishing something section 24 protects, so the tests spend most
of their effort on what must *not* appear.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from app.services import tutor_profile_service as P

#: Nothing here may ever appear in a public profile (section 24), plus the
#: 50-member rule, which is an onboarding condition rather than a quality
#: signal and so has no business on a public page.
FORBIDDEN_FIELDS = (
    "phone",
    "email",
    "currentAddress",
    "current_address",
    "telegram_user_id",
    "telegramUserId",
    "telegram_storage_chat_id",
    "telegramMessageId",
    "english_voice_file_id",
    "englishVoiceFileId",
    "memberReferral",
    "documents",
    "documentsTotal",
    "api_secret",
    "bot_token",
)


def _verified_tutor(session: Session, telegram_id: int = 4242, **overrides):
    payload = {
        "telegram_user_id": telegram_id,
        "full_name": "Abebe Bekele",
        "phone": "+251911234567",
        "current_address": "Bole, Addis Ababa",
        "gender": "Male",
        "age": 27,
        "locations": ["Ayat Tsebel", "Bole"],
        "grades": ["Grade 4", "Grade 5"],
        "subjects": ["Mathematics"],
        "languages": ["Amharic", "English"],
        "english_proficiency": 9,
        "university": "AASTU",
        "department": "Mathematics",
        "education_level": "UNIVERSITY_GRADUATE",
        "university_year": 2019,
        "cgpa": 3.75,
        "entrance_exam_type": "EHE",
        "entrance_exam_max_score": 840,
        "entrance_exam_score": 612,
        "entrance_exam_year": 2016,
        "teaching_experience_years": 3,
    }
    payload.update(overrides)
    tutor = P.create_ethiopian_tutor(session, payload)
    tutor.status = "VERIFIED"
    session.flush()
    return tutor


def test_the_profile_endpoint_returns_the_structured_fields(client, fresh_db) -> None:
    session = fresh_db()
    tutor = _verified_tutor(session)
    session.commit()

    response = client.get(f"/api/tutors/{tutor.public_tutor_id}/profile")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == tutor.public_tutor_id
    assert body["displayName"] == "Abebe Bekele"
    assert body["grades"] == ["Grade 4", "Grade 5"]
    assert body["teachingLocations"] == ["Ayat Tsebel", "Bole"]
    assert body["subjects"] == ["Mathematics"]
    assert body["englishProficiency"] == 9
    assert body["university"] == "AASTU"
    assert body["cgpa"] == 3.75


def test_the_profile_needs_no_api_secret(client, fresh_db) -> None:
    """It is public, so it must not be gated."""
    session = fresh_db()
    tutor = _verified_tutor(session)
    session.commit()
    assert client.get(f"/api/tutors/{tutor.public_tutor_id}/profile").status_code == 200


def test_the_profile_publishes_no_private_data(client, fresh_db) -> None:
    session = fresh_db()
    tutor = _verified_tutor(session)
    P.record_english_voice(session, tutor, "PRIVATE-VOICE-FILE-ID", 77)
    P.record_document(
        session, tutor, "UNIVERSITY_TRANSCRIPT", "PRIVATE-DOC-FILE-ID",
        file_name="t.pdf",
    )
    session.commit()

    body = json.dumps(client.get(f"/api/tutors/{tutor.public_tutor_id}/profile").json())
    for field in FORBIDDEN_FIELDS:
        assert field not in body, f"public profile leaked {field}"
    # The actual stored values must not leak either, not just the field names.
    assert "PRIVATE-VOICE-FILE-ID" not in body
    assert "PRIVATE-DOC-FILE-ID" not in body
    assert "+251911234567" not in body
    assert "Bole, Addis Ababa" not in body


def test_the_profile_never_exposes_a_price(client, fresh_db) -> None:
    """Ethiopian tutors have no rate on file, and none is published."""
    session = fresh_db()
    tutor = _verified_tutor(session)
    session.commit()
    body = client.get(f"/api/tutors/{tutor.public_tutor_id}/profile").json()
    assert "etbRate" not in body
    assert "usdRate" not in body


def test_the_entrance_exam_is_published_with_its_scale(client, fresh_db) -> None:
    """612/840 and 72/100 must both be interpretable."""
    session = fresh_db()
    tutor = _verified_tutor(session)
    session.commit()
    exam = client.get(f"/api/tutors/{tutor.public_tutor_id}/profile").json()["entranceExam"]
    assert exam["score"] == 612
    assert exam["maxScore"] == 840
    assert exam["examType"] == "EHE"
    assert exam["year"] == 2016
    assert exam["normalisedPercentage"] == pytest.approx(72.9, abs=0.1)


def test_an_exam_without_a_recorded_scale_is_not_normalised(client, fresh_db) -> None:
    session = fresh_db()
    tutor = _verified_tutor(
        session, -4243, entrance_exam_max_score=None, entrance_exam_score=500
    )
    session.commit()
    exam = client.get(f"/api/tutors/{tutor.public_tutor_id}/profile").json()["entranceExam"]
    assert exam["score"] == 500
    assert exam["maxScore"] is None
    assert exam["normalisedPercentage"] is None


def test_a_tutor_with_no_exam_publishes_no_exam_block(client, fresh_db) -> None:
    session = fresh_db()
    tutor = _verified_tutor(
        session, -4244, entrance_exam_score=None, entrance_exam_max_score=None,
        entrance_exam_type=None,
    )
    session.commit()
    body = client.get(f"/api/tutors/{tutor.public_tutor_id}/profile").json()
    assert body["entranceExam"] is None


def test_unverified_tutors_are_not_publicly_visible(client, fresh_db) -> None:
    session = fresh_db()
    tutor = _verified_tutor(session, -4245)
    tutor.status = "PENDING"
    session.commit()
    assert client.get(f"/api/tutors/{tutor.public_tutor_id}/profile").status_code == 404


def test_an_unknown_tutor_is_a_404(client) -> None:
    assert client.get("/api/tutors/TDR-999999/profile").status_code == 404


def test_the_profile_route_is_not_shadowed_by_the_plain_route(client, fresh_db) -> None:
    """``/tutors/{id}`` must not swallow ``/tutors/{id}/profile``."""
    session = fresh_db()
    tutor = _verified_tutor(session, -4246)
    session.commit()
    profile = client.get(f"/api/tutors/{tutor.public_tutor_id}/profile")
    plain = client.get(f"/api/tutors/{tutor.public_tutor_id}")
    assert profile.status_code == 200
    assert "teachingLocations" in profile.json()
    assert "teachingLocations" not in plain.json(), (
        "the legacy route must keep its original shape"
    )


def test_the_legacy_tutor_route_is_unchanged(client, fresh_db) -> None:
    """Adding /profile must not change the response of the existing route."""
    from tests.factories import tutor_payload
    from app.services import tutor_service

    session = fresh_db()
    tutor = tutor_service.create_tutor(
        session, tutor_payload(4247, status="VERIFIED")
    )
    session.commit()
    body = client.get(f"/api/tutors/{tutor.public_tutor_id}").json()
    assert set(body) >= {
        "id",
        "displayName",
        "country",
        "city",
        "bio",
        "subjects",
        "levels",
        "languages",
        "teachingMode",
        "experienceYears",
    }
    assert "grades" not in body
    assert "teachingLocations" not in body


def test_a_legacy_tutor_profile_shows_what_it_actually_has(client, fresh_db) -> None:
    """An imported tutor has coarse bands and no areas — report that honestly."""
    from tests.factories import tutor_payload
    from app.services import tutor_service

    session = fresh_db()
    tutor = tutor_service.create_tutor(
        session, tutor_payload(4248, levels=["Grade 9-10"], status="VERIFIED")
    )
    session.commit()
    body = client.get(f"/api/tutors/{tutor.public_tutor_id}/profile").json()
    assert body["levels"] == ["Grade 9-10"]
    assert body["grades"] == []
    assert body["teachingLocations"] == []
    assert body["englishProficiency"] is None
    assert body["entranceExam"] is None