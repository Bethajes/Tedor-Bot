"""Job applications: the Apply button, profile completion and recommendations.

Feature: job-application-buttons-and-tutor-recommendations
Validates: Requirements 2-9 (apply button, callback handling, profile
completion, job evaluation, submission, persistence, admin notifications) and
Requirement 11 (server-side validation, bounded callback data, admin
authorization, escaped text, duplicate protection).

Every test drives the real handlers against the real in-memory database and a
stub bot, so nothing contacts Telegram. ``pytest.ini`` sets
``asyncio_mode = auto``, so the async tests carry no marker.

Note on ``any_message``: the admin interest notification is sent *after* the
recommendation, so ``bot.sent[-1]`` is not the message under test in those
cases. Assertions therefore search the whole transcript where that matters.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from telegram import (
    CallbackQuery,
    Chat,
    Message,
    MessageEntity,
    Update,
    User,
)
from telegram.error import TelegramError

from app.bot.handlers import job_apply as apply_handler
from app.bot.handlers.job_post import DATA_KEY as POST_DATA_KEY
from app.bot.handlers.job_post import publish_post
from app.bot.keyboards import job_apply as apply_keys
from app.config import settings
from app.database import session_scope
from app.enums import DocumentType, JobApplicationStatus
from app.models import JobApplication, JobPost, Tutor
from app.services import job_application_service as applications
from app.services import job_post_service
from app.services import tutor_profile_service as P
from tests.factories import job_post_payload
from tests.test_conversation_flow import FakeBot, FakeContext

TUTOR_ID = 4242
OTHER_TUTOR_ID = 4243
STRANGER_ID = 999
ADMIN_ID = 111
JOB_CHAT_ID = -100777


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class StubBot(FakeBot):
    """A FakeBot that also resolves its own username, as a real bot does."""

    async def get_me(self) -> Any:  # pragma: no cover - trivial accessor
        return type("Me", (), {"username": self.username})()


def ethiopian_payload(telegram_id: int = TUTOR_ID, **overrides: Any) -> dict[str, Any]:
    """Exactly what the Ethiopian onboarding flow collects.

    Mirrors ``tests/test_profile_service.py`` so the two suites agree on what
    "complete" means.
    """
    payload = {
        "telegram_user_id": telegram_id,
        "full_name": "Abebe Bekele",
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
        "teaching_experience_description": "Taught Grade 4-6 mathematics in Ayat Tsebel.",
        "languages": ["Amharic", "English"],
    }
    payload.update(overrides)
    return payload


def make_tutor(
    db: Any,
    telegram_id: int = TUTOR_ID,
    *,
    with_documents: bool = True,
    status: str | None = None,
    **overrides: Any,
) -> Tutor:
    """A complete tutor, optionally with the mandatory documents uploaded."""
    tutor = P.create_ethiopian_tutor(db, ethiopian_payload(telegram_id, **overrides))
    if with_documents:
        for kind in (
            str(DocumentType.UNIVERSITY_TRANSCRIPT),
            str(DocumentType.UNIVERSITY_ENTRANCE_RESULT),
        ):
            P.record_document(
                db, tutor, kind, f"file-{kind}", f"{kind}.pdf", file_name=f"{kind}.pdf"
            )
    if status is not None:
        tutor.status = status
    db.flush()
    db.refresh(tutor)
    return tutor


def make_job(db: Any, **overrides: Any) -> JobPost:
    """A published job, so it is open and can be applied to."""
    payload = job_post_payload(ADMIN_ID)
    payload.update(overrides)
    post = job_post_service.create_job_post(db, payload)
    job_post_service.mark_published(db, post, JOB_CHAT_ID, 4321)
    db.flush()
    db.refresh(post)
    return post


def find_tutor(db: Any, telegram_id: int = TUTOR_ID) -> Tutor | None:
    from app.services import tutor_service

    return tutor_service.get_tutor_by_telegram_id(db, telegram_id)


def context_for(bot: FakeBot) -> FakeContext:
    context = FakeContext(bot)
    context.bot_data = {}
    return context


def publish(bot: FakeBot, **overrides: Any) -> FakeContext:
    """Prepare a context holding a complete, publishable post payload."""
    bot.sent.clear()
    bot.markups.clear()
    context = context_for(bot)
    context.user_data[POST_DATA_KEY] = job_post_payload(ADMIN_ID, **overrides)
    return context


def admin_command(bot: FakeBot, command: str = "/post") -> Any:
    """A ``CommandHandler``-compatible ``/post`` message from the admin."""
    text = command
    message = Message(
        message_id=1,
        date=None,
        chat=Chat(id=ADMIN_ID, type=Chat.PRIVATE),
        from_user=User(id=ADMIN_ID, first_name="Admin", is_bot=False),
        text=text,
        entities=[
            MessageEntity(
                type=MessageEntity.BOT_COMMAND, offset=0, length=len(text)
            )
        ],
    )
    message.set_bot(bot)
    return Update(update_id=1, message=message)


def tap(bot: FakeBot, context: FakeContext, data: str, user_id: int = TUTOR_ID) -> Any:
    """Press one ``japply:`` button exactly as the private chat would."""
    return _press(bot, context, data, user_id, private=True)


def channel_tap(bot: FakeBot, context: FakeContext, data: str) -> Any:
    """The same press, but on a channel post rather than in the private chat."""
    return _press(bot, context, data, TUTOR_ID, private=False)


def _press(
    bot: FakeBot, context: FakeContext, data: str, user_id: int, *, private: bool
) -> Any:
    user = User(id=user_id, first_name="Tutor", is_bot=False)
    message = Message(
        message_id=1,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(
            id=user_id if private else JOB_CHAT_ID,
            type=Chat.PRIVATE if private else Chat.CHANNEL,
        ),
        from_user=user,
    )
    query = CallbackQuery(
        id="q", from_user=user, chat_instance="i", message=message, data=data
    )
    query.set_bot(bot)
    message.set_bot(bot)
    return apply_handler.apply_callback(
        Update(update_id=1, callback_query=query), context
    )


def apply_button(markups: list[tuple[Any, str, Any]]) -> Any:
    """The single Apply button attached beneath a published post."""
    channel = [item for item in markups if item[0] == JOB_CHAT_ID]
    assert channel, "the post was never sent to the job channel"
    markup = channel[-1][2]
    assert markup is not None, "no reply markup was attached"
    assert len(markup.inline_keyboard) == 1
    entry = markup.inline_keyboard[0][0]
    assert entry.text == apply_keys.BTN_APPLY
    return entry


def row_for(post: JobPost, tutor: Tutor) -> JobApplication | None:
    with session_scope() as session:
        return session.execute(
            select(JobApplication).where(
                JobApplication.job_post_id == post.id,
                JobApplication.tutor_id == tutor.id,
            )
        ).scalar_one_or_none()


def last(bot: FakeBot) -> str:
    return bot.sent[-1] if bot.sent else ""


def last_keyboard(bot: FakeBot) -> list[list[Any]]:
    """The inline keyboard of the most recent message."""
    assert bot.markups, "no message with a reply markup was sent"
    markup = bot.markups[-1][2]
    assert markup is not None, "the last message had no keyboard"
    return markup.inline_keyboard


def any_message(bot: FakeBot, needle: str) -> bool:
    return any(needle in text for text in bot.sent)


def count_rows(check: Any) -> int:
    return check.query(JobApplication).count()


def all_rows() -> list[JobApplication]:
    with session_scope() as session:
        return list(session.execute(select(JobApplication)).scalars())


# ---------------------------------------------------------------------------
# the Apply button on a published post
# ---------------------------------------------------------------------------


async def test_every_published_post_carries_an_apply_button(
    fresh_db, monkeypatch
) -> None:
    session = fresh_db()
    monkeypatch.setattr(settings, "job_post_chat_id", JOB_CHAT_ID, raising=False)
    bot = StubBot()
    await publish_post(admin_command(bot), publish(bot, location="Ayat Tsebel"))

    with session_scope() as check:
        post = check.query(JobPost).one()
        assert str(post.status) == "PUBLISHED"
        assert str(post.telegram_chat_id) == str(JOB_CHAT_ID)
        assert post.telegram_message_id is not None
        reference = post.public_post_id

    entry = apply_button(bot.markups)
    assert entry.url is not None, "the button must be a deep link"
    assert apply_keys.START_PAYLOAD_PREFIX + reference in entry.url
    session.rollback()


async def test_the_button_names_the_job_not_the_title_or_the_location(
    fresh_db, monkeypatch
) -> None:
    session = fresh_db()
    monkeypatch.setattr(settings, "job_post_chat_id", JOB_CHAT_ID, raising=False)
    bot = StubBot()
    await publish_post(
        admin_command(bot), publish(bot, location="Ayat Tsebel", student_level="4th Grader")
    )
    with session_scope() as check:
        reference = check.query(JobPost).one().public_post_id

    url = apply_button(bot.markups).url
    assert reference in url
    # The mapping to a human heading stays on the server, so the link is short
    # and independent of how the location is spelled.
    assert "Ayat" not in url
    assert "4th" not in url
    session.rollback()


async def test_two_published_jobs_each_carry_their_own_button(
    fresh_db, monkeypatch
) -> None:
    session = fresh_db()
    monkeypatch.setattr(settings, "job_post_chat_id", JOB_CHAT_ID, raising=False)
    bot = StubBot()

    await publish_post(admin_command(bot), publish(bot, location="Ayat Tsebel"))
    with session_scope() as check:
        first = check.query(JobPost).one().public_post_id
    first_url = apply_button(bot.markups).url

    await publish_post(admin_command(bot), publish(bot, location="CMC"))
    with session_scope() as check:
        second = check.query(JobPost).order_by(JobPost.id.desc()).first().public_post_id
    second_url = apply_button(bot.markups).url

    assert first != second
    assert apply_keys.START_PAYLOAD_PREFIX + first in first_url
    assert apply_keys.START_PAYLOAD_PREFIX + second in second_url
    session.rollback()


async def test_a_failed_delivery_publishes_nothing_and_leaves_no_button(
    fresh_db, monkeypatch
) -> None:
    session = fresh_db()
    monkeypatch.setattr(settings, "job_post_chat_id", JOB_CHAT_ID, raising=False)
    bot = StubBot()
    bot.raise_for_message = TelegramError("network down")
    await publish_post(admin_command(bot), publish(bot))

    with session_scope() as check:
        post = check.query(JobPost).one()
        assert str(post.status) == "DRAFT"
        assert post.telegram_message_id is None
    assert any("publishing failed" in text for text in bot.sent)
    session.rollback()


async def test_the_button_never_carries_personal_data(fresh_db, monkeypatch) -> None:
    session = fresh_db()
    monkeypatch.setattr(settings, "job_post_chat_id", JOB_CHAT_ID, raising=False)
    bot = StubBot()
    make_tutor(fresh_db(), phone="+251911999888")
    await publish_post(admin_command(bot), publish(bot))

    entry = apply_button(bot.markups)
    link = entry.url or entry["callback_data"]
    assert "+251" not in link
    assert "TDR" not in link
    session.rollback()


# ---------------------------------------------------------------------------
# callback data and deep-link payloads
# ---------------------------------------------------------------------------


def test_callback_data_stays_inside_the_telegram_size_limit() -> None:
    """Telegram rejects ``callback_data`` above 64 bytes."""
    for action in apply_keys.ALL_ACTIONS:
        data = apply_keys.callback_data(action, "JOB-000123")
        assert len(data.encode("utf-8")) <= apply_keys.CALLBACK_DATA_LIMIT
    assert apply_keys.callback_data("continue", "JOB-000123") == (
        "japply:continue:JOB-000123"
    )


def test_a_deep_link_carries_only_the_job_reference() -> None:
    link = apply_keys.deep_link("tedor_tutors_bot", "JOB-000123")
    assert link == "https://t.me/tedor_tutors_bot?start=japply_JOB-000123"
    payload = link.split("start=", 1)[1]
    # Nothing personal may ride in the link — only the job reference.
    assert payload == "japply_JOB-000123"
    assert "TDR" not in payload and "phone" not in payload and "name" not in payload


def test_deep_link_payloads_round_trip() -> None:
    assert apply_keys.parse_start_payload("japply_JOB-000123") == "JOB-000123"
    assert apply_keys.parse_start_payload("") is None
    assert apply_keys.parse_start_payload("something_else") is None
    assert apply_keys.parse_start_payload("japply_") is None


def test_callback_data_is_parsed_and_foreign_data_is_refused() -> None:
    assert apply_keys.parse_callback_data("japply:continue:JOB-000001") == (
        "continue",
        "JOB-000001",
    )
    assert apply_keys.parse_callback_data("menu:home") is None
    assert apply_keys.parse_callback_data("japply:broken") is None
    assert apply_keys.parse_callback_data("admin:verify:TDR-000001") is None


def test_only_known_actions_are_routed() -> None:
    assert apply_keys.parse_callback_data("japply:go:JOB-000001")[0] in (
        apply_keys.ALL_ACTIONS
    )


async def test_a_deep_link_opens_the_apply_workflow(fresh_db) -> None:
    """``/start japply_JOB-000123`` must reach the workflow, not the menu."""
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    context.args = [f"{apply_keys.START_PAYLOAD_PREFIX}{post.public_post_id}"]

    message = Message(
        message_id=1,
        date=None,
        chat=Chat(id=TUTOR_ID, type=Chat.PRIVATE),
        from_user=User(id=TUTOR_ID, first_name="Tutor", is_bot=False),
        text="/start",
    )
    message.set_bot(bot)

    await apply_handler.apply_deep_link(
        Update(update_id=1, message=message), context
    )
    assert any_message(bot, "good match")
    session.rollback()


async def test_a_start_without_a_payload_is_ignored_by_the_apply_flow(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    context = context_for(bot)
    context.args = []

    message = Message(
        message_id=1,
        date=None,
        chat=Chat(id=TUTOR_ID, type=Chat.PRIVATE),
        from_user=User(id=TUTOR_ID, first_name="Tutor", is_bot=False),
        text="/start",
    )
    message.set_bot(bot)

    assert (
        await apply_handler.apply_deep_link(Update(update_id=1, message=message), context)
        is False
    )
    session.rollback()


# ---------------------------------------------------------------------------
# job status
# ---------------------------------------------------------------------------


async def test_pressing_apply_on_an_open_job_records_interest(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, status="VERIFIED")

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    stored = row_for(post, tutor)
    assert stored is not None
    # A complete profile is evaluated immediately, so the row already waits for
    # the tutor's confirmation.
    assert str(stored.status) == str(JobApplicationStatus.READY_TO_SUBMIT)
    assert stored.confirmed_submitted is False
    assert any_message(bot, "good match")
    session.rollback()


async def test_a_tutor_with_no_profile_is_sent_to_the_application_form(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert any_message(bot, "we need your tutor profile")
    assert "Step 1/27" in last(bot)
    with session_scope() as check:
        assert count_rows(check) == 0
    session.rollback()


async def test_a_draft_job_cannot_be_applied_to(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    with session_scope() as scope:
        draft = job_post_service.create_job_post(scope, job_post_payload(ADMIN_ID))
        reference = draft.public_post_id

    await tap(bot, context_for(bot), f"japply:go:{reference}")

    assert "not accepting applications" in last(bot)
    with session_scope() as check:
        assert count_rows(check) == 0
    session.rollback()


async def test_a_closed_job_cannot_be_applied_to(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    with session_scope() as scope:
        stored = job_post_service.get_job_post_by_id(scope, post.public_post_id)
        stored.status = "CLOSED"

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert "not accepting applications" in last(bot)
    session.rollback()


async def test_a_deleted_job_is_reported_safely(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    reference = post.public_post_id
    with session_scope() as scope:
        scope.delete(job_post_service.get_job_post_by_id(scope, reference))

    await tap(bot, context_for(bot), f"japply:go:{reference}")

    assert "not accepting applications" in last(bot)
    session.rollback()


async def test_a_malformed_job_reference_never_reaches_the_database(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    make_tutor(session)
    malicious = "'; DROP TABLE job_posts; --"

    for bad in ("", "JOB-1", "JOB-abc", "job-000001", malicious):
        await tap(bot, context_for(bot), f"japply:go:{bad}")
        assert "not valid" in last(bot), bad

    with session_scope() as check:
        assert count_rows(check) == 0
        assert check.query(JobPost).count() == 0
    session.rollback()


# ---------------------------------------------------------------------------
# profile completion: only what is missing is requested
# ---------------------------------------------------------------------------


async def test_an_incomplete_profile_is_asked_only_for_what_is_missing(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, with_documents=True)
    # Wipe two fields the completeness check counts, leaving the rest intact.
    with session_scope() as scope:
        stored = find_tutor(scope)
        stored.english_proficiency = None
        for location in list(stored.tutoring_locations):
            scope.delete(location)

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert any_message(bot, "almost complete")
    assert any_message(bot, "English")
    assert any_message(bot, "Tutoring locations")
    # Fields already answered are never re-asked.
    assert not any_message(bot, "Full name")
    assert not any_message(bot, "University / institution")
    session.rollback()


async def test_an_incomplete_profile_is_not_evaluated_yet(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, with_documents=False)

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    with session_scope() as check:
        row = check.query(JobApplication).one()
        assert str(row.status) == str(JobApplicationStatus.PROFILE_INCOMPLETE)
        assert row.confirmed_submitted is False
        assert row.match_score == 0.0
    session.rollback()


async def test_a_complete_profile_goes_straight_to_evaluation(fresh_db) -> None:
    """The common case: no questions asked, just a recommendation."""
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="VERIFIED")

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert not any_message(bot, "Step 1/27")
    assert not any_message(bot, "almost complete")
    assert any_message(bot, "good match")
    session.rollback()


async def test_a_profile_awaiting_verification_may_still_apply(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="DOCUMENTS_SUBMITTED")

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert any_message(bot, "good match")
    assert not any_message(bot, "Cannot apply")
    session.rollback()


async def test_a_rejected_profile_cannot_apply(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, status="REJECTED")

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert any_message(bot, "Cannot apply")
    assert any_message(bot, "rejected")
    # Interest is still recorded — an admin may want to see who tried — but
    # nothing was evaluated or submitted.
    stored = row_for(post, tutor)
    assert stored is not None
    assert str(stored.status) == str(JobApplicationStatus.STARTED)
    assert stored.confirmed_submitted is False
    assert stored.match_score == 0.0
    session.rollback()


async def test_a_suspended_profile_cannot_apply(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="SUSPENDED")

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert any_message(bot, "Cannot apply")
    assert any_message(bot, "suspended")
    session.rollback()


async def test_a_partial_profile_resumes_where_it_left_off(fresh_db) -> None:
    """Filling the gap later picks the same job back up, on one row."""
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, with_documents=True)
    with session_scope() as scope:
        stored = find_tutor(scope)
        stored.english_proficiency = None

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")
    stored = row_for(post, tutor)
    assert stored is not None
    assert str(stored.status) == str(JobApplicationStatus.PROFILE_INCOMPLETE)

    # The tutor answers the outstanding question later and presses Apply again.
    with session_scope() as scope:
        find_tutor(scope).english_proficiency = 8
    bot.sent.clear()
    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    stored = row_for(post, tutor)
    assert stored is not None
    assert str(stored.status) == str(JobApplicationStatus.READY_TO_SUBMIT)
    assert any_message(bot, "good match")
    assert len([r for r in all_rows()]) == 1
    session.rollback()


async def test_pressing_apply_repeatedly_still_yields_one_row(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, with_documents=False)

    for _ in range(4):
        await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    stored = row_for(post, tutor)
    assert stored is not None
    assert str(stored.status) == str(JobApplicationStatus.PROFILE_INCOMPLETE)
    assert len(all_rows()) == 1
    session.rollback()


# ---------------------------------------------------------------------------
# submission and duplicates
# ---------------------------------------------------------------------------


async def test_continue_application_submits_the_application(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    reference = post.public_post_id

    await tap(bot, context, f"japply:go:{reference}")
    await tap(bot, context, f"japply:continue:{reference}")

    stored = row_for(post, tutor)
    assert stored.confirmed_submitted is True
    assert str(stored.status) == str(JobApplicationStatus.SUBMITTED)
    assert stored.submitted_at is not None
    assert any_message(bot, "Your application has been submitted")
    session.rollback()


async def test_pressing_apply_does_not_submit_anything(fresh_db) -> None:
    """The two actions are distinct: interest is not a submission."""
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, status="VERIFIED")

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    stored = row_for(post, tutor)
    assert stored.confirmed_submitted is False
    assert stored.submitted_at is None
    assert str(stored.status) != str(JobApplicationStatus.SUBMITTED)
    assert any_message(bot, "recommendation based on your profile")
    session.rollback()


async def test_pressing_continue_twice_submits_once(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    reference = post.public_post_id

    await tap(bot, context, f"japply:go:{reference}")
    await tap(bot, context, f"japply:continue:{reference}")
    first_submitted_at = row_for(post, tutor).submitted_at
    bot.sent.clear()
    await tap(bot, context, f"japply:continue:{reference}")

    stored = row_for(post, tutor)
    assert stored.submitted_at == first_submitted_at
    assert len(all_rows()) == 1
    # The duplicate press must not notify the admin a second time.
    assert not any_message(bot, "New job application submitted")
    session.rollback()


async def test_a_second_tutor_can_apply_to_the_same_job(fresh_db) -> None:
    """The unique constraint is per tutor, not per job."""
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    first = make_tutor(session, TUTOR_ID)
    second = make_tutor(session, OTHER_TUTOR_ID)
    reference = post.public_post_id

    await tap(bot, context_for(bot), f"japply:go:{reference}", TUTOR_ID)
    await tap(bot, context_for(bot), f"japply:go:{reference}", OTHER_TUTOR_ID)

    assert row_for(post, first) is not None
    assert row_for(post, second) is not None
    assert row_for(post, first).id != row_for(post, second).id
    session.rollback()


async def test_the_same_tutor_can_apply_to_different_jobs(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    first_post = make_job(session, location="Ayat Tsebel")
    second_post = make_job(session, location="CMC")
    tutor = make_tutor(session, status="VERIFIED")

    await tap(bot, context_for(bot), f"japply:go:{first_post.public_post_id}")
    await tap(bot, context_for(bot), f"japply:go:{second_post.public_post_id}")

    assert row_for(first_post, tutor) is not None
    assert row_for(second_post, tutor) is not None
    assert len(all_rows()) == 2
    session.rollback()


async def test_a_submitted_application_is_shown_as_such_on_a_later_press(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    reference = post.public_post_id

    await tap(bot, context, f"japply:go:{reference}")
    await tap(bot, context, f"japply:continue:{reference}")
    bot.sent.clear()
    await tap(bot, context, f"japply:go:{reference}")

    assert any_message(bot, "already applied")
    assert any_message(bot, "submitted")
    assert not any_message(bot, "good match")
    session.rollback()


async def test_an_incomplete_profile_blocks_the_confirmation(fresh_db) -> None:
    """Confirming with a missing field must not submit anything."""
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, with_documents=False)
    context = context_for(bot)
    reference = post.public_post_id

    await tap(bot, context, f"japply:go:{reference}")
    await tap(bot, context, f"japply:continue:{reference}")

    stored = row_for(post, tutor)
    assert stored.confirmed_submitted is False
    assert any_message(bot, "almost complete")
    session.rollback()


async def test_a_stranger_cannot_confirm_for_someone_else(fresh_db) -> None:
    """The tutor always comes from the Telegram user, never from the callback."""
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    reference = post.public_post_id

    await tap(bot, context, f"japply:go:{reference}", STRANGER_ID)

    stored = all_rows()
    assert stored == []
    session.rollback()


# ---------------------------------------------------------------------------
# the recommendation itself
# ---------------------------------------------------------------------------


async def test_the_recommendation_explains_itself_and_hedges(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="VERIFIED")

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert any_message(bot, "This job may be a good match for you!")
    # Subject, grade, confirmed area, English, entrance and experience are named.
    assert any_message(bot, "Teaches Mathematics")
    assert any_message(bot, "Grade match")
    assert any_message(bot, "Available in Ayat Tsebel")
    assert any_message(bot, "English 9/10")
    assert any_message(bot, "Entrance exam 612/840")
    assert any_message(bot, "year(s) of teaching")
    assert any_message(bot, "not a guarantee of employment")
    session.rollback()


async def test_a_weak_match_is_advised_not_blocked(fresh_db) -> None:
    """A weak match must still reach a recommendation rather than a refusal."""
    session = fresh_db()
    bot = StubBot()
    # An online job whose subject the tutor does not teach: location stops
    # mattering, and the fit is genuinely poor.
    post = make_job(session, location="online", subjects="Physics")
    make_tutor(session, status="VERIFIED")

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert not any_message(bot, "Cannot apply")
    assert any_message(bot, "Does not teach Physics")
    # The offer to continue is still made — it is a button, so it lives in the
    # keyboard rather than the message body.
    assert last_keyboard(bot)[0][0].text == apply_keys.BTN_CONTINUE_APPLICATION
    session.rollback()


async def test_a_missing_entrance_score_is_unknown_not_zero(fresh_db) -> None:
    """Spec 6: missing scores are unknown, never treated as zero ability.

    Drives the scoring service directly, as the admin's re-match does, because
    the completeness rule still requires the entrance chain. What matters is
    that the entrance component is scored neutral rather than collapsed to zero.
    """
    session = fresh_db()
    post = make_job(session)
    tutor = make_tutor(
        session,
        entrance_exam_score=None,
        entrance_exam_max_score=None,
        entrance_exam_year=None,
    )

    from app.services import matching_service

    assert matching_service.normalised_entrance_score(tutor) is None

    with session_scope() as check:
        stored = find_tutor(check)
        with_entrance = applications.evaluate(check, stored, post)
        without_entrance = matching_service.evaluate_tutor(check, stored, post)

    # A tutor with no entrance data is not scored as if they had scored zero:
    # they still earn a positive overall score from their other evidence.
    assert without_entrance.percentage > 0
    assert with_entrance.result.percentage > 0
    session.rollback()


async def test_an_in_person_job_reports_the_location_mismatch(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session, location="Adama")
    make_tutor(session, status="VERIFIED")

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    # The mismatch is named honestly rather than silently ignored.
    assert any_message(bot, "away from the job's area")
    session.rollback()


async def test_an_online_job_drops_location_entirely(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session, location="online")
    make_tutor(session, status="VERIFIED")

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert any_message(bot, "location does not apply")
    session.rollback()


async def test_the_gender_requirement_blocks_a_genuine_mismatch(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session, tutor_category="Male")
    make_tutor(session, gender="Female", status="VERIFIED")

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert any_message(bot, "Male tutors only")
    session.rollback()


async def test_an_unknown_gender_does_not_block(fresh_db) -> None:
    """Refusing on an absent answer would be guessing, not enforcing."""
    session = fresh_db()
    bot = StubBot()
    post = make_job(session, tutor_category="Male")
    make_tutor(session, with_documents=True)
    with session_scope() as scope:
        find_tutor(scope).gender = None
        scope.flush()

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert not any_message(bot, "Male tutors only")
    session.rollback()


# ---------------------------------------------------------------------------
# admin authorization and notifications
# ---------------------------------------------------------------------------


async def test_only_an_admin_may_move_an_application_status(fresh_db, api_env) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    reference = post.public_post_id
    await tap(bot, context, f"japply:go:{reference}")
    await tap(bot, context, f"japply:continue:{reference}")
    stored = row_for(post, tutor)
    application_id = stored.id

    await tap(bot, context, f"japply:admin:shortlist:{application_id}", STRANGER_ID)

    refreshed = row_for(post, tutor)
    assert str(refreshed.status) == str(JobApplicationStatus.SUBMITTED)
    assert any_message(bot, "Access denied")
    session.rollback()


async def test_an_admin_can_shortlist_a_submitted_application(fresh_db, api_env) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    reference = post.public_post_id
    await tap(bot, context, f"japply:go:{reference}")
    await tap(bot, context, f"japply:continue:{reference}")
    application_id = row_for(post, tutor).id

    await tap(bot, context, f"japply:admin:shortlist:{application_id}", ADMIN_ID)

    refreshed = row_for(post, tutor)
    assert str(refreshed.status) == str(JobApplicationStatus.SHORTLISTED)
    assert any_message(bot, "marked")
    session.rollback()


async def test_an_admin_unknown_application_is_reported(fresh_db, api_env) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    await tap(bot, context, f"japply:continue:{post.public_post_id}")

    await tap(bot, context, "japply:admin:shortlist:99999", ADMIN_ID)

    assert any_message(bot, "no longer exists")
    session.rollback()


async def test_the_admin_is_told_about_interest_once(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, with_documents=False)

    for _ in range(3):
        await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert sum(1 for text in bot.sent if "Job interest recorded" in text) == 1
    assert row_for(post, tutor) is not None
    session.rollback()


async def test_the_admin_is_told_once_a_submission_is_made(fresh_db, api_env) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    reference = post.public_post_id
    bot.sent.clear()
    await tap(bot, context, f"japply:continue:{reference}")

    assert sum(1 for text in bot.sent if "New job application submitted" in text) == 1
    session.rollback()


async def test_the_admin_notification_carries_no_phone_number(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    await tap(bot, context, f"japply:go:{post.public_post_id}")
    await tap(bot, context, f"japply:continue:{post.public_post_id}")

    assert not any_message(bot, "+251911234567")
    assert not any_message(bot, "file-")
    assert any_message(bot, "TDR-")
    session.rollback()


# ---------------------------------------------------------------------------
# escaping and channel hand-off
# ---------------------------------------------------------------------------


async def test_a_tutor_name_with_html_is_escaped(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="VERIFIED", full_name="<b>Abebe</b> & Co")

    await tap(bot, context_for(bot), f"japply:go:{post.public_post_id}")

    assert not any_message(bot, "<b>Abebe</b> & Co")
    assert any_message(bot, "&lt;b&gt;Abebe&lt;/b&gt;")
    session.rollback()


async def test_a_press_on_a_channel_post_continues_privately(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="VERIFIED")
    context = context_for(bot)

    await channel_tap(bot, context, f"japply:go:{post.public_post_id}")

    assert any_message(bot, "Continue in our private chat")
    assert any_message(bot, "never posted in a public channel")
    # The private workflow was not started there.
    assert not any_message(bot, "good match")
    session.rollback()


# ---------------------------------------------------------------------------
# withdrawing
# ---------------------------------------------------------------------------


async def test_a_tutor_can_withdraw_an_application(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    reference = post.public_post_id
    await tap(bot, context, f"japply:go:{reference}")
    await tap(bot, context, f"japply:continue:{reference}")

    await tap(bot, context, f"japply:withdraw:{reference}")

    stored = row_for(post, tutor)
    assert str(stored.status) == str(JobApplicationStatus.WITHDRAWN)
    # The history is kept: they really did apply once.
    assert stored.confirmed_submitted is True
    assert any_message(bot, "withdrawn")
    session.rollback()


async def test_a_withdrawn_application_can_be_restarted(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    reference = post.public_post_id
    await tap(bot, context, f"japply:go:{reference}")
    await tap(bot, context, f"japply:withdraw:{reference}")

    await tap(bot, context, f"japply:go:{reference}")

    stored = row_for(post, tutor)
    assert str(stored.status) == str(JobApplicationStatus.READY_TO_SUBMIT)
    assert stored.confirmed_submitted is False
    assert len(all_rows()) == 1
    session.rollback()


# ---------------------------------------------------------------------------
# back to the job post
# ---------------------------------------------------------------------------


async def test_back_to_the_job_restates_the_opening(fresh_db) -> None:
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    await tap(bot, context, f"japply:go:{post.public_post_id}")

    await tap(bot, context, f"japply:back:{post.public_post_id}")

    assert any_message(bot, "Urgent Job Opening")
    assert any_message(bot, "#Ayat_Tsebel")
    session.rollback()


# ---------------------------------------------------------------------------
# percentage display
# ---------------------------------------------------------------------------


async def test_the_fit_score_is_shown_as_a_percentage(fresh_db) -> None:
    """The stored score is a fraction; the tutor and admin see a percentage."""
    session = fresh_db()
    bot = StubBot()
    post = make_job(session)
    tutor = make_tutor(session, status="VERIFIED")
    context = context_for(bot)
    reference = post.public_post_id
    await tap(bot, context, f"japply:go:{reference}")
    await tap(bot, context, f"japply:continue:{reference}")

    stored = row_for(post, tutor)
    assert 0.0 <= stored.match_score <= 1.0
    assert any_message(bot, f"Fit score: {round(100 * stored.match_score)}%")
    session.rollback()
