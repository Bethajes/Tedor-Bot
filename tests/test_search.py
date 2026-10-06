"""Search and matching properties (Properties 6 and 8).

Feature: tedor-telegram-bot, Property 8: Matching service score ordering
Validates: Requirements 7.2, 7.6

Feature: tedor-telegram-bot, Property 6: Search filter soundness
Validates: Requirements 10.3, 7.1
"""

from __future__ import annotations

from hypothesis import given, settings as hypothesis_settings, strategies as st

from app.enums import TutorFilters, TutorStatus
from app.services import search_service, student_service, tutor_service
from tests.factories import create_tutor, student_request_payload

PROFILE = hypothesis_settings(max_examples=100, deadline=None)

SUBJECT_POOL = ["Mathematics", "Physics", "Chemistry", "English", "Biology"]
LEVEL_POOL = ["Grade 9-10", "Grade 11-12", "University", "All Levels"]
LANGUAGE_POOL = ["English", "Amharic", "French"]
MODE_POOL = ["ONLINE", "IN_PERSON", "BOTH"]


def _seed_tutor(session, telegram_id: int, status: str = TutorStatus.VERIFIED):
    index = abs(telegram_id)
    return create_tutor(
        session,
        telegram_id,
        status=status,
        subjects=[SUBJECT_POOL[index % len(SUBJECT_POOL)]],
        levels=[LEVEL_POOL[index % len(LEVEL_POOL)]],
        languages=[LANGUAGE_POOL[index % len(LANGUAGE_POOL)]],
        teaching_mode=MODE_POOL[index % len(MODE_POOL)],
        country="Ethiopia" if index % 2 == 0 else "Kenya",
        etb_rate=100.0 + index,
        usd_rate=1.0 + index,
    )


# ---------------------------------------------------------------------------
# Property 8
# ---------------------------------------------------------------------------


@given(
    tutor_count=st.integers(min_value=1, max_value=5),
    subject=st.sampled_from(SUBJECT_POOL),
    level=st.sampled_from(LEVEL_POOL),
    language=st.sampled_from(LANGUAGE_POOL),
    mode=st.sampled_from(MODE_POOL),
    budget=st.floats(min_value=1, max_value=500),
    seed=st.integers(min_value=1, max_value=10**6),
)
@PROFILE
def test_property_match_scores_are_sorted_descending(
    fresh_db,
    tutor_count: int,
    subject: str,
    level: str,
    language: str,
    mode: str,
    budget: float,
    seed: int,
) -> None:
    """Feature: tedor-telegram-bot, Property 8: Matching service score ordering."""
    session = fresh_db()
    for index in range(tutor_count):
        _seed_tutor(session, -(seed * 10 + index + 1))
    session.commit()

    request = student_service.create_student_request(
        session,
        student_request_payload(
            -(seed + 1),
            subject=subject,
            education_level=level,
            preferred_language=language,
            mode=mode,
            budget=budget,
            country="Ethiopia",
        ),
    )
    session.commit()

    matches = search_service.match_tutors(session, request, limit=50)
    scores = [item.score for item in matches]
    assert scores == sorted(scores, reverse=True)
    for item in matches:
        assert item.tutor.status == TutorStatus.VERIFIED


@given(
    budget=st.floats(min_value=1, max_value=500),
    seed=st.integers(min_value=1, max_value=10**6),
)
@PROFILE
def test_property_unverified_tutors_are_never_matched(fresh_db, budget: float, seed: int) -> None:
    session = fresh_db()
    for offset, status in enumerate(
        (TutorStatus.PENDING, TutorStatus.REJECTED, TutorStatus.SUSPENDED, TutorStatus.UNDER_REVIEW)
    ):
        _seed_tutor(session, -(seed * 10 + offset + 1), status=status)
    session.commit()
    request = student_service.create_student_request(
        session, student_request_payload(-(seed + 1), budget=budget)
    )
    session.commit()
    assert search_service.match_tutors(session, request, limit=10) == []


# ---------------------------------------------------------------------------
# Property 6
# ---------------------------------------------------------------------------


@given(
    subject=st.sampled_from(SUBJECT_POOL),
    level=st.sampled_from(LEVEL_POOL),
    country=st.sampled_from(["Ethiopia", "Kenya", "Germany"]),
    language=st.sampled_from(LANGUAGE_POOL),
    mode=st.sampled_from(MODE_POOL),
    max_etb=st.floats(min_value=1, max_value=500),
    max_usd=st.floats(min_value=1, max_value=50),
    tutor_count=st.integers(min_value=1, max_value=4),
    seed=st.integers(min_value=1, max_value=10**6),
)
@PROFILE
def test_property_search_filter_soundness(
    fresh_db,
    subject: str,
    level: str,
    country: str,
    language: str,
    mode: str,
    max_etb: float,
    max_usd: float,
    tutor_count: int,
    seed: int,
) -> None:
    """Feature: tedor-telegram-bot, Property 6: Search filter soundness."""
    session = fresh_db()
    # One tutor is built to satisfy every filter so the property is not vacuous.
    create_tutor(
        session,
        -(seed * 10),
        status=TutorStatus.VERIFIED,
        subjects=[subject],
        levels=[level],
        languages=[language],
        teaching_mode=mode,
        country=country,
        etb_rate=max_etb / 2,
        usd_rate=max_usd / 2,
    )
    for index in range(tutor_count):
        _seed_tutor(session, -(seed * 10 + index + 1))
    session.commit()

    filters = TutorFilters(
        subject=subject,
        level=level,
        country=country,
        language=language,
        mode=mode,
        max_etb=max_etb,
        max_usd=max_usd,
    )
    results = tutor_service.search_tutors(session, filters)
    assert results, "the pool must contain at least one candidate to filter"
    for tutor in results:
        assert subject.casefold() in " ".join(tutor.subject_list).casefold()
        assert level.casefold() in " ".join(tutor.level_list).casefold()
        assert tutor.country.casefold() == country.casefold()
        assert language.casefold() in " ".join(tutor.language_list).casefold()
        assert tutor.teaching_mode == mode
        assert tutor.etb_rate <= max_etb
        assert tutor.usd_rate <= max_usd


@given(
    minimum=st.floats(min_value=1, max_value=100),
    maximum=st.floats(min_value=101, max_value=900),
    seed=st.integers(min_value=1, max_value=10**6),
)
@PROFILE
def test_property_price_range_filter(fresh_db, minimum: float, maximum: float, seed: int) -> None:
    session = fresh_db()
    for index in range(3):
        _seed_tutor(session, -(seed * 10 + index + 1))
    session.commit()
    results = tutor_service.search_tutors(
        session, TutorFilters(min_usd=minimum, max_usd=maximum)
    )
    for tutor in results:
        assert minimum <= tutor.usd_rate <= maximum


def test_search_supports_status_filter(fresh_db) -> None:
    session = fresh_db()
    _seed_tutor(session, -101, status=TutorStatus.PENDING)
    _seed_tutor(session, -102, status=TutorStatus.VERIFIED)
    session.commit()
    pending = tutor_service.search_tutors(session, TutorFilters(status="pending"))
    assert [tutor.status for tutor in pending] == ["PENDING"]


def test_scoring_weights_match_design(fresh_db) -> None:
    session = fresh_db()
    tutor = create_tutor(
        session,
        -900,
        subjects=["Mathematics"],
        levels=["Grade 9-10"],
        languages=["English"],
        teaching_mode="ONLINE",
        country="Ethiopia",
        etb_rate=100.0,
        usd_rate=2.0,
    )
    session.commit()
    request = student_service.create_student_request(
        session,
        student_request_payload(
            -901,
            subject="Mathematics",
            education_level="Grade 9-10",
            preferred_language="English",
            mode="ONLINE",
            country="Ethiopia",
            budget=500,
        ),
    )
    session.commit()
    scored = search_service.score_tutor(tutor, request)
    assert scored.score == search_service.MAX_SCORE