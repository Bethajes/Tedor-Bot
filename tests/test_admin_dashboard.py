"""Admin dashboard (specification section 28), member referral (section 7)
and profile completeness in the detail view (section 27).

Every action is exercised through the admin conversation's own callback
handler, with the admin-gate asserted on the way in.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.ext import ConversationHandler

from app.bot.handlers import admin as admin_handler
from app.bot.keyboards.admin import (
    admin_menu_keyboard,
    job_list_keyboard,
    member_referral_keyboard,
    tutor_list_keyboard,
    tutor_status_filter_keyboard,
)
from app.bot.states import AdminStates
from app.database import session_scope
from app.enums import CandidateStatus, MemberReferralStatus
from app.services import job_match_pipeline, tutor_profile_service as P
from app.services import tutor_service
from tests.test_matching_service import ideal_tutor, publish_job

ADMIN_ID = 111


def _update(data: str | None = None, user_id: int = ADMIN_ID):
    """A callback or plain-message update, with reply_text captured."""
    sent: list[str] = []

    async def reply_text(text: str, **kwargs):
        sent.append(text)
        return SimpleNamespace()

    message = SimpleNamespace(reply_text=AsyncMock(side_effect=reply_text))
    user = SimpleNamespace(id=user_id)
    update = SimpleNamespace(
        effective_user=user,
        effective_chat=SimpleNamespace(id=user_id),
        effective_message=message,
        message=message,
        callback_query=None,
        _sent=sent,
    )
    if data is not None:
        query = SimpleNamespace(
            data=data, message=message, answer=AsyncMock(), from_user=user
        )
        update.callback_query = query
    return update


def _rendered(update) -> str:
    return "\n".join(update._sent)


# ---------------------------------------------------------------------------
# dashboard menu
# ---------------------------------------------------------------------------


def test_the_dashboard_has_the_four_specified_sections() -> None:
    labels = [
        button.text
        for row in admin_menu_keyboard().inline_keyboard
        for button in row
    ]
    assert "👨‍🏫 Tutors" in labels
    assert "🎯 Job Matches" in labels
    assert "⏳ Verification" in labels
    assert "📊 Statistics" in labels


def test_the_dashboard_keeps_the_post_job_button() -> None:
    """Requirement 9 must not be lost by the new menu."""
    data = [
        button.callback_data
        for row in admin_menu_keyboard().inline_keyboard
        for button in row
    ]
    assert "admin:post_job" in data


def test_every_dashboard_callback_fits_the_telegram_limit() -> None:
    for keyboard in (
        admin_menu_keyboard(),
        tutor_status_filter_keyboard(),
        member_referral_keyboard("TDR-000123", "NOT_COMPLETED"),
    ):
        for row in keyboard.inline_keyboard:
            for button in row:
                if button.callback_data:
                    assert len(button.callback_data.encode("utf-8")) <= 64


# ---------------------------------------------------------------------------
# statistics (section 28)
# ---------------------------------------------------------------------------


def test_statistics_lists_every_specified_counter(api_env, fresh_db) -> None:
    session = fresh_db()
    for index in range(3):
        ideal_tutor(session, -9100 - index, status="VERIFIED")
    tutor_service.create_tutor(
        session,
        {
            "telegram_user_id": -9200,
            "name": "Legacy",
            "display_name": "Legacy",
            "phone": "+251911111111",
            "email": "legacy@example.com",
            "country": "Ethiopia",
            "city": "Addis Ababa",
            "teaching_mode": "IN_PERSON",
        },
    )
    post = publish_job(session)
    job_match_pipeline.run_match_for_job(session, post)
    tutor = ideal_tutor(session, -9300, status="VERIFIED")
    job_match_pipeline.set_candidate_status(
        session, post.public_post_id, tutor.public_tutor_id, CandidateStatus.SELECTED
    )
    session.commit()

    text = admin_handler.statistics_text()
    for label in (
        "Total tutors",
        "Verified",
        "Pending",
        "Documents submitted",
        "Incomplete profiles",
        "Total job posts",
        "Open jobs",
        "Recommended candidates",
        "Shortlisted",
        "Selected tutors",
        "Student requests",
        "Support tickets",
    ):
        assert label in text, label


def test_the_counters_reflect_the_database(api_env, fresh_db) -> None:
    session = fresh_db()
    ideal_tutor(session, -9400, status="VERIFIED")
    post = publish_job(session)
    results = job_match_pipeline.run_match_for_job(session, post)
    session.commit()

    stats = job_match_pipeline.platform_statistics(session)
    assert stats["total_tutors"] == 1
    assert stats["VERIFIED"] == 1
    assert stats["open_jobs"] == 1
    assert stats["job_candidates"] == len(results)


def test_incomplete_profiles_agree_with_the_per_tutor_percentage(
    api_env, fresh_db
) -> None:
    """The dashboard count must never contradict the detail view."""
    session = fresh_db()
    complete = ideal_tutor(session, -9500, status="VERIFIED")
    P.record_document(
        session, complete, "UNIVERSITY_TRANSCRIPT", "f", file_name="t.pdf"
    )
    P.update_profile_fields(
        session, complete, {"age": 28, "current_address": "Bole, Addis Ababa"}
    )
    assert P.profile_completion_percentage(complete) == 100
    # This one is missing its English rating and its academic record.
    ideal_tutor(
        session,
        -9501,
        status="VERIFIED",
        english_proficiency=None,
        cgpa=None,
        entrance_exam_score=None,
        entrance_exam_max_score=None,
        university=None,
        department=None,
        teaching_experience_years=None,
    )
    session.commit()

    by_tutor = sum(
        1
        for tutor in tutor_service.list_tutors(session)
        if P.profile_completion_percentage(tutor) < 100
    )
    assert job_match_pipeline.incomplete_profile_count(session) == by_tutor
    assert by_tutor == 1, "only the deliberately incomplete tutor is counted"


def test_legacy_tutors_count_as_incomplete(api_env, fresh_db) -> None:
    """The 1,000+ imported tutors have none of the new structured fields."""
    from tests.factories import tutor_payload

    session = fresh_db()
    tutor_service.create_tutor(session, tutor_payload(-9600))
    session.commit()
    assert job_match_pipeline.incomplete_profile_count(session) == 1


# ---------------------------------------------------------------------------
# dashboard navigation (admin-gated)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_non_admin_cannot_reach_the_dashboard(api_env, fresh_db) -> None:
    update = _update("admin:tutors", user_id=999999)
    state = await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    assert state == ConversationHandler.END
    assert "denied" in _rendered(update).lower()


@pytest.mark.asyncio
async def test_the_tutor_section_offers_a_status_filter(api_env) -> None:
    update = _update("admin:tutors")
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    assert "Filter by status" in _rendered(update)


@pytest.mark.asyncio
async def test_a_status_filter_lists_the_matching_tutors(api_env, fresh_db) -> None:
    session = fresh_db()
    verified = ideal_tutor(session, -9700, status="VERIFIED")
    ideal_tutor(session, -9701, status="PENDING")
    session.commit()

    update = _update("admin:tutors:filter:VERIFIED")
    state = await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    rendered = _rendered(update)
    assert verified.public_tutor_id in rendered
    assert state == int(AdminStates.TUTOR_LIST)


@pytest.mark.asyncio
async def test_an_empty_filter_says_so(api_env, fresh_db) -> None:
    update = _update("admin:tutors:filter:REJECTED")
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    assert "No tutors with status" in _rendered(update)


@pytest.mark.asyncio
async def test_the_verification_section_lists_waiting_tutors(api_env, fresh_db) -> None:
    session = fresh_db()
    tutor = ideal_tutor(session, -9800, status="PENDING")
    session.commit()

    update = _update("admin:verification")
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    rendered = _rendered(update)
    assert tutor.public_tutor_id in rendered
    assert "% complete" in rendered


@pytest.mark.asyncio
async def test_the_job_matches_section_lists_open_jobs(api_env, fresh_db) -> None:
    session = fresh_db()
    post = publish_job(session)
    ideal_tutor(session, -9900, status="VERIFIED")
    job_match_pipeline.run_match_for_job(session, post)
    session.commit()

    update = _update("admin:matches")
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    rendered = _rendered(update)
    assert post.public_post_id in rendered
    assert "candidate(s)" in rendered


@pytest.mark.asyncio
async def test_the_job_matches_section_is_empty_without_open_jobs(
    api_env, fresh_db
) -> None:
    update = _update("admin:matches")
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    assert "no open jobs" in _rendered(update).lower()


@pytest.mark.asyncio
async def test_a_job_candidate_list_shows_the_explanation(api_env, fresh_db) -> None:
    session = fresh_db()
    post = publish_job(session)
    ideal_tutor(session, -9910, status="VERIFIED")
    job_match_pipeline.run_match_for_job(session, post)
    session.commit()

    update = _update(f"admin:matches:{post.public_post_id}")
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    rendered = _rendered(update)
    assert post.public_post_id in rendered
    assert "%" in rendered
    assert "RECOMMENDED" in rendered


@pytest.mark.asyncio
async def test_a_job_candidate_list_without_matches_explains_what_to_do(
    api_env, fresh_db
) -> None:
    session = fresh_db()
    post = publish_job(session)
    session.commit()

    update = _update(f"admin:matches:{post.public_post_id}")
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    assert "/match" in _rendered(update)


# ---------------------------------------------------------------------------
# member referral (section 7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_admin_can_advance_the_member_requirement(api_env, fresh_db) -> None:
    session = fresh_db()
    tutor = ideal_tutor(session, -9920, status="PENDING")
    session.commit()

    update = _update("admin:referral:VERIFIED:TDR-000000")
    # Use the real TDR reference.
    update = _update(f"admin:referral:VERIFIED:{tutor.public_tutor_id}")
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))

    session.expire_all()
    stored = tutor_service.get_tutor_by_tdr_id(session, tutor.public_tutor_id)
    assert stored.member_referral_status == str(MemberReferralStatus.VERIFIED)
    assert stored.verified_by == ADMIN_ID
    assert "onboarding condition only" in _rendered(update)


@pytest.mark.asyncio
async def test_an_unknown_member_status_is_refused(api_env, fresh_db) -> None:
    session = fresh_db()
    tutor = ideal_tutor(session, -9930, status="PENDING")
    session.commit()

    update = _update(f"admin:referral:MAYBE:{tutor.public_tutor_id}")
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    assert "⚠️" in _rendered(update)
    session.expire_all()
    stored = tutor_service.get_tutor_by_tdr_id(session, tutor.public_tutor_id)
    assert stored.member_referral_status == str(MemberReferralStatus.NOT_COMPLETED)


@pytest.mark.asyncio
async def test_a_non_admin_cannot_touch_the_member_requirement(
    api_env, fresh_db
) -> None:
    session = fresh_db()
    tutor = ideal_tutor(session, -9940, status="PENDING")
    session.commit()

    update = _update(f"admin:referral:VERIFIED:{tutor.public_tutor_id}", user_id=999999)
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    session.expire_all()
    stored = tutor_service.get_tutor_by_tdr_id(session, tutor.public_tutor_id)
    assert stored.member_referral_status == str(MemberReferralStatus.NOT_COMPLETED)


def test_the_member_keyboard_offers_every_state() -> None:
    data = [
        button.callback_data
        for row in member_referral_keyboard("TDR-000001", "NOT_COMPLETED").inline_keyboard
        for button in row
    ]
    for status in MemberReferralStatus:
        assert f"admin:referral:{status.value}:TDR-000001" in data


def test_the_member_keyboard_marks_the_current_state() -> None:
    labels = [
        button.text
        for row in member_referral_keyboard("TDR-000001", "COMPLETED").inline_keyboard
        for button in row
    ]
    assert any("✅" in label and "Completed" in label for label in labels)


# ---------------------------------------------------------------------------
# tutor detail view (sections 18, 27)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_detail_view_shows_completion_and_what_is_missing(
    api_env, fresh_db
) -> None:
    session = fresh_db()
    tutor = ideal_tutor(session, -9950, status="PENDING", english_proficiency=None)
    session.commit()

    update = _update(f"admin:review:{tutor.public_tutor_id}")
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    rendered = _rendered(update)
    assert "Profile completeness" in rendered
    assert "Missing" in rendered
    assert "English" in rendered


@pytest.mark.asyncio
async def test_the_detail_view_flags_a_recorded_voice_intro(api_env, fresh_db) -> None:
    session = fresh_db()
    tutor = ideal_tutor(session, -9960, status="PENDING")
    P.record_english_voice(session, tutor, "voice-file", 5)
    session.commit()

    update = _update(f"admin:review:{tutor.public_tutor_id}")
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    assert "recording on file" in _rendered(update)


@pytest.mark.asyncio
async def test_the_detail_view_never_exposes_a_price_for_ethiopian_tutors(
    api_env, fresh_db
) -> None:
    """Requirement 2: no rate was ever collected, so none is shown."""
    session = fresh_db()
    tutor = ideal_tutor(session, -9970, status="PENDING")
    session.commit()

    update = _update(f"admin:review:{tutor.public_tutor_id}")
    await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    rendered = _rendered(update)
    assert "ETB" not in rendered
    assert "USD" not in rendered


# ---------------------------------------------------------------------------
# list keyboards
# ---------------------------------------------------------------------------


def test_the_tutor_list_keyboard_links_to_each_detail_view(api_env, fresh_db) -> None:
    session = fresh_db()
    tutor = ideal_tutor(session, -9980, status="VERIFIED")
    data = [
        button.callback_data
        for row in tutor_list_keyboard([tutor]).inline_keyboard
        for button in row
    ]
    assert f"admin:review:{tutor.public_tutor_id}" in data


def test_the_job_list_keyboard_links_to_each_job(api_env, fresh_db) -> None:
    session = fresh_db()
    post = publish_job(session)
    data = [
        button.callback_data
        for row in job_list_keyboard([post]).inline_keyboard
        for button in row
    ]
    assert f"admin:matches:{post.public_post_id}" in data