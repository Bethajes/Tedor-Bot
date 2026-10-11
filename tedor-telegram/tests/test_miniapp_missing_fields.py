"""Property 16: the job screen's missing fields match the Profile Completion Service.

The Mini App's job screen shows a tutor exactly what their profile is missing,
and that list comes from one place only: ``profile_completion_service.assess``.
If the API assembled its own list, the tutor would be told to fill in fields
the Apply flow does not actually check — or, worse, sent through the whole
form and still be told something is outstanding.

Validates: Requirement 7.2
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
from app.services import job_post_service
from app.services import profile_completion_service
from app.services import tutor_profile_service as P
from tests.factories import job_post_payload

ADMIN_ID = 111
JOB_CHAT_ID = -100775


def _app_for(user_id: int, db: Session) -> FastAPI:
    app = FastAPI()

    def _fixed_user() -> TelegramUser:
        return TelegramUser(id=user_id)

    def _fixed_db():
        yield db

    app.dependency_overrides[get_current_telegram_user] = _fixed_user
    app.dependency_overrides[get_db] = _fixed_db
    app.include_router(router)
    return app


def _complete_payload(user_id: int, **overrides: Any) -> dict[str, Any]:
    payload = {
        "telegram_user_id": user_id,
        "full_name": f"Tutor {user_id}",
        "phone": "+251911234567",
        "gender": "Male",
        "age": 27,
        "current_address": "Bole, Addis Ababa",
        "locations": ["Ayat Tsebel"],
        "grades": ["Grade 4"],
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
    payload.update(overrides)
    return payload


def _make_tutor(db: Session, user_id: int, **overrides: Any):
    return P.create_ethiopian_tutor(db, _complete_payload(user_id, **overrides))


def _publish_job(db: Session):
    post = job_post_service.create_job_post(db, job_post_payload(ADMIN_ID))
    job_post_service.mark_published(db, post, JOB_CHAT_ID, 6666)
    db.flush()
    db.refresh(post)
    return post


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_user_id = st.integers(min_value=400_000, max_value=900_000)

#: Fields that can be left blank on a stored tutor, so the profile is incomplete
#: in a way the API must report.
_none = st.just(None)

_optional_overrides = st.one_of(
    st.fixed_dictionaries({"current_address": _none}),
    st.fixed_dictionaries({"university": _none}),
    st.fixed_dictionaries({"department": _none}),
    st.fixed_dictionaries({"cgpa": _none}),
    st.fixed_dictionaries({"teaching_experience_years": _none}),
    st.fixed_dictionaries({"entrance_exam_score": _none, "entrance_exam_max_score": _none}),
    st.fixed_dictionaries({"age": _none}),
    st.fixed_dictionaries({"english_proficiency": _none}),
    st.fixed_dictionaries({"education_level": _none}),
    st.fixed_dictionaries(
        {"current_address": _none, "university": _none, "cgpa": _none}
    ),
)

#: Values outside their own range: "missing" and "invalid" are different things,
#: and both belong in the list the tutor is shown. They are written straight to
#: the row, because ``create_ethiopian_tutor`` refuses to store them — which is
#: the point: only an import or a row written under older rules can hold one.
_invalid_overrides = st.one_of(
    st.fixed_dictionaries({"age": st.just(12)}),
    st.fixed_dictionaries({"age": st.just(130)}),
    st.fixed_dictionaries({"english_proficiency": st.just(0)}),
    st.fixed_dictionaries({"english_proficiency": st.just(11)}),
    st.fixed_dictionaries({"cgpa": st.just(4.6)}),
    st.fixed_dictionaries(
        {"entrance_exam_score": st.just(999), "entrance_exam_max_score": st.just(840)}
    ),
    st.fixed_dictionaries({"academic_stream": st.just("ASTRONOMY")}),
    st.fixed_dictionaries({"phone": st.just("not-a-number")}),
)

_document_state = st.booleans()


# Feature: tedor-mini-app, Property 16: Missing fields displayed match Profile Completion Service
@given(
    user_id=_user_id,
    overrides=st.one_of(st.none(), _optional_overrides, _invalid_overrides),
    with_documents=_document_state,
)
@hyp_settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_missing_fields_match_profile_completion_service(
    fresh_db: Any,
    user_id: int,
    overrides: dict[str, Any] | None,
    with_documents: bool,
) -> None:
    """For any tutor record, the API's ``missing_fields`` is exactly what the
    Profile Completion Service says is outstanding.

    Validates: Requirement 7.2
    """
    db: Session = fresh_db()

    overrides = dict(overrides or {})
    # Blank values are accepted on creation; out-of-range ones are not, so they
    # are applied to the stored row afterwards.
    stored = {key: value for key, value in overrides.items() if value is not None}
    create_overrides = {
        key: value for key, value in overrides.items() if value is None
    }

    tutor = _make_tutor(db, user_id, **create_overrides)
    for column, value in stored.items():
        setattr(tutor, column, value)
    db.flush()
    if with_documents:
        for kind in (
            str(DocumentType.UNIVERSITY_TRANSCRIPT),
            str(DocumentType.UNIVERSITY_ENTRANCE_RESULT),
        ):
            P.record_document(db, tutor, kind, f"file-{kind}", f"{kind}.pdf",
                              file_name=f"{kind}.pdf")
    post = _publish_job(db)
    db.commit()
    db.refresh(tutor)

    expected = profile_completion_service.assess(tutor)
    expected_outstanding = list(expected.outstanding)

    app = _app_for(user_id, db)
    with TestClient(app) as client:
        response = client.get(f"/api/miniapp/jobs/{post.public_post_id}")

    assert response.status_code == 200, response.text
    body = response.json()

    if expected.state.value == "INCOMPLETE":
        assert body["profile_state"] == "INCOMPLETE"
        assert body["missing_fields"] == expected_outstanding, (
            "the API's missing_fields disagree with the Profile Completion "
            f"Service: {body['missing_fields']} != {expected_outstanding}"
        )
        assert body.get("score") is None, (
            "an incomplete profile must not be scored: it would invite the "
            "tutor to apply before they can"
        )
    else:
        # A complete profile carries no outstanding list and is scored.
        assert body["profile_state"] == expected.state.value
        assert not body.get("missing_fields")
        assert body.get("score") is not None


# Feature: tedor-mini-app, Property 16: Missing fields displayed match Profile Completion Service
@given(user_id=_user_id)
@hyp_settings(max_examples=20, deadline=None)
def test_property_a_complete_profile_reports_nothing_outstanding(
    fresh_db: Any,
    user_id: int,
) -> None:
    """The other half: a fully complete profile is not sent back through the form.

    Validates: Requirement 7.2
    """
    db: Session = fresh_db()

    tutor = _make_tutor(db, user_id)
    for kind in (
        str(DocumentType.UNIVERSITY_TRANSCRIPT),
        str(DocumentType.UNIVERSITY_ENTRANCE_RESULT),
    ):
        P.record_document(db, tutor, kind, f"file-{kind}", f"{kind}.pdf",
                          file_name=f"{kind}.pdf")
    tutor.status = "VERIFIED"
    post = _publish_job(db)
    db.commit()
    db.refresh(tutor)

    expected = profile_completion_service.assess(tutor)
    assert not expected.outstanding, "the fixture is supposed to be complete"

    app = _app_for(user_id, db)
    with TestClient(app) as client:
        response = client.get(f"/api/miniapp/jobs/{post.public_post_id}")

    body = response.json()
    assert response.status_code == 200
    assert body["missing_fields"] == []
    assert "score" in body


# Feature: tedor-mini-app, Property 16: Missing fields displayed match Profile Completion Service
@given(user_id=_user_id, status=st.sampled_from(["REJECTED", "SUSPENDED"]))
@hyp_settings(max_examples=10, deadline=None)
def test_property_a_blocked_profile_is_not_sent_through_the_form(
    fresh_db: Any,
    user_id: int,
    status: str,
) -> None:
    """A refused or suspended profile is reported as such, not as a to-do list.

    Validates: Requirement 7.2
    """
    db: Session = fresh_db()

    tutor = _make_tutor(db, user_id)
    tutor.status = status
    post = _publish_job(db)
    db.commit()
    db.refresh(tutor)

    expected = profile_completion_service.assess(tutor)

    app = _app_for(user_id, db)
    with TestClient(app) as client:
        response = client.get(f"/api/miniapp/jobs/{post.public_post_id}")

    body = response.json()
    assert response.status_code == 200
    assert body["profile_state"] == expected.state.value
    assert body.get("reason") == expected.reason
    assert body.get("score") is None