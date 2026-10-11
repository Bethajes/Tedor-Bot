"""Multi-select for the job post's Student's Level (Step 2/7).

The admin taps as many levels as the opening covers and confirms with Done.
Both ways of answering — toggled buttons and typed free text — must render an
identical post.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from telegram import Chat, Message, MessageEntity, Update, User

from app.bot.handlers import job_post as job_post_handler
from app.bot.keyboards.job_post import (
    MULTISELECT_FIELDS,
    post_multi_select_keyboard,
    post_preview_keyboard,
)
from app.bot.options import JOB_POST_STUDENT_LEVELS
from app.bot.states import JobPostStates
from app.database import session_scope
from app.models import JobPost
from app.services import job_post_service
from app.templates.tutor_job_post import join_values, render_job_post
from tests.factories import job_post_payload
from tests.test_conversation_flow import FakeContext, callback_update, dispatch

ADMIN_ID = 111

LEVELS = list(JOB_POST_STUDENT_LEVELS)


def _text(value: str, bot, user_id: int = ADMIN_ID) -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=user_id, type=Chat.PRIVATE),
        from_user=User(id=user_id, first_name="A", is_bot=False),
        text=value,
    )
    message.set_bot(bot)
    return Update(update_id=1, message=message)


def _base(**overrides):
    values = {
        "location": "Ayat Tsebel",
        "student_level": "4th Grader",
        "tutor_category": "Both Male and Female",
        "duration": "1 hour/day",
        "frequency": "3 days/week",
        "subjects": "Major Subjects",
        "compensation": "300 Birr / 1 hour",
    }
    values.update(overrides)
    return values


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------


def test_the_student_level_step_is_a_multi_select() -> None:
    step = next(s for s in job_post_handler.STEPS if s.field == "student_level")
    assert step.kind == "multiselect"
    assert step.options == JOB_POST_STUDENT_LEVELS
    assert MULTISELECT_FIELDS == {"student_level"}


def test_no_other_step_became_a_multi_select() -> None:
    kinds = {s.field: s.kind for s in job_post_handler.STEPS}
    assert kinds["location"] != "multiselect"
    assert kinds["tutor_category"] == "choice"
    assert kinds["compensation"] == "text"


def test_the_keyboard_offers_toggle_and_done_in_the_post_namespace() -> None:
    keyboard = post_multi_select_keyboard("student_level", LEVELS, show_back=False)
    data = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "post:student_level:toggle:0" in data
    assert "post:student_level:done" in data
    assert "post:cancel" in data
    assert "post:back" not in data
    for item in data:
        assert len(item.encode("utf-8")) <= 64, item


def test_selected_levels_are_marked() -> None:
    keyboard = post_multi_select_keyboard(
        "student_level", LEVELS, ["7th Grader", "9th Grader"]
    )
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert "✅ 7th Grader" in labels
    assert "✅ 9th Grader" in labels
    assert "✅ 8th Grader" not in labels
    assert sum(1 for item in labels if item.startswith("✅ ")) == 3


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_a_toggled_list_renders_one_level_per_line() -> None:
    text = render_job_post(**_base(student_level=["7th Grader", "9th Grader"]))
    body = text.split("✍️ **Student's Level**:")[1].split("✍️")[0]
    assert body.split() == ["7th", "Grader", "9th", "Grader"]


def test_toggled_and_typed_answers_render_identically() -> None:
    toggled = render_job_post(**_base(student_level=["7th Grader", "8th Grader", "9th Grader"]))
    typed = render_job_post(**_base(student_level="7th Grader\n8th Grader\n9th Grader"))
    assert toggled == typed


def test_a_single_level_is_unchanged() -> None:
    text = render_job_post(**_base())
    assert "✍️ **Student's Level**: 4th Grader\n" in text


def test_no_python_repr_can_reach_the_post() -> None:
    text = render_job_post(**_base(student_level=["7th Grader", "9th Grader"]))
    assert "['" not in text
    assert "']" not in text


def test_join_values_flattens_for_storage_only() -> None:
    assert join_values(["7th Grader", "9th Grader"]) == "7th Grader, 9th Grader"
    assert join_values("4th Grader") == "4th Grader"


def test_the_column_fits_every_option() -> None:
    from app.models import JobPost as Model

    column = Model.__table__.columns["student_level"]
    assert len(", ".join(JOB_POST_STUDENT_LEVELS)) <= column.type.length


# ---------------------------------------------------------------------------
# the conversation
# ---------------------------------------------------------------------------


def _command(bot) -> Update:
    text = "/post"
    message = Message(
        message_id=1,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=ADMIN_ID, type=Chat.PRIVATE),
        from_user=User(id=ADMIN_ID, first_name="A", is_bot=False),
        text=text,
        entities=[
            MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(text))
        ],
    )
    message.set_bot(bot)
    return Update(update_id=1, message=message)


async def _reach_levels(context: FakeContext):
    """Start /post and answer only the location, landing on the level step."""
    conversation = job_post_handler.build_job_post_conversation()
    bot = context.bot
    state = await dispatch(conversation, _command(bot), context, None)
    assert state == job_post_handler.STATE_ORDER[0]
    state = await dispatch(conversation, _text("Ayat Tsebel", bot=bot), context, state)
    assert state == int(JobPostStates.STUDENT_LEVEL)
    return conversation, state


@pytest.mark.asyncio
async def test_several_levels_can_be_toggled_then_confirmed(api_env) -> None:
    context = FakeContext()
    conversation, state = await _reach_levels(context)
    bot = context.bot

    for wanted in ("7th Grader", "9th Grader", "University Student"):
        state = await dispatch(
            conversation,
            callback_update(f"post:student_level:toggle:{LEVELS.index(wanted)}", ADMIN_ID, bot),
            context,
            state,
        )
        # toggling never advances
        assert state == int(JobPostStates.STUDENT_LEVEL)

    assert context.user_data["job_post"]["student_level"] == [
        "7th Grader",
        "9th Grader",
        "University Student",
    ]

    state = await dispatch(
        conversation,
        callback_update("post:student_level:done", ADMIN_ID, bot),
        context,
        state,
    )
    assert state == int(JobPostStates.TUTOR_CATEGORY)


@pytest.mark.asyncio
async def test_toggling_the_same_level_twice_removes_it(api_env) -> None:
    context = FakeContext()
    conversation, state = await _reach_levels(context)
    bot = context.bot
    toggle = f"post:student_level:toggle:{LEVELS.index('9th Grader')}"

    await dispatch(conversation, callback_update(toggle, ADMIN_ID, bot), context, state)
    assert context.user_data["job_post"]["student_level"] == ["9th Grader"]
    await dispatch(conversation, callback_update(toggle, ADMIN_ID, bot), context, state)
    assert context.user_data["job_post"]["student_level"] == []


@pytest.mark.asyncio
async def test_done_without_a_selection_is_refused(api_env) -> None:
    context = FakeContext()
    conversation, state = await _reach_levels(context)
    bot = context.bot

    state = await dispatch(
        conversation,
        callback_update("post:student_level:done", ADMIN_ID, bot),
        context,
        state,
    )
    assert state == int(job_post_handler.STEPS[1].state)
    assert any("at least one level" in text for text in bot.sent)


@pytest.mark.asyncio
async def test_the_preview_shows_every_selected_level(api_env) -> None:
    context = FakeContext()
    conversation, state = await _reach_levels(context)
    bot = context.bot

    for wanted in ("7th Grader", "8th Grader"):
        await dispatch(
            conversation,
            callback_update(f"post:student_level:toggle:{LEVELS.index(wanted)}", ADMIN_ID, bot),
            context,
            state,
        )
    await dispatch(
        conversation, callback_update("post:student_level:done", ADMIN_ID, bot), context, state
    )
    for field, value in (
        ("tutor_category", "Both Male and Female"),
        ("duration", "1 hour/day"),
        ("frequency", "3 days/week"),
        ("subjects", "Major Subjects"),
        ("compensation", "300 Birr / 1 hour"),
    ):
        state = await dispatch(conversation, _text(value, bot=bot), context, state)

    assert state == job_post_handler.PREVIEW_STATE
    preview = bot.sent[-1]
    assert "7th Grader" in preview
    assert "8th Grader" in preview
    assert "['" not in preview


@pytest.mark.asyncio
async def test_typed_levels_are_split_into_a_list(api_env) -> None:
    context = FakeContext()
    conversation, state = await _reach_levels(context)
    bot = context.bot

    state = await dispatch(
        conversation,
        _text("7th Grader, 8th Grader", bot=bot),
        context,
        state,
    )
    assert context.user_data["job_post"]["student_level"] == ["7th Grader", "8th Grader"]
    assert state == int(JobPostStates.TUTOR_CATEGORY)


# ---------------------------------------------------------------------------
# persistence and publishing
# ---------------------------------------------------------------------------


def test_the_service_renders_from_the_raw_list(api_env, fresh_db) -> None:
    session = fresh_db()
    with session_scope() as create:
        post = job_post_service.create_job_post(
            create, job_post_payload(-1, student_level=["7th Grader", "9th Grader"])
        )
        text = post.generated_text
        stored = post.student_level
        post_id = post.public_post_id

    # stored comma-joined for search, rendered one per line in the post
    assert stored == "7th Grader, 9th Grader"
    body = text.split("✍️ **Student's Level**:")[1].split("✍️")[0]
    assert body.split() == ["7th", "Grader", "9th", "Grader"]

    with session_scope() as check:
        loaded = job_post_service.get_job_post_by_id(check, post_id)
        assert loaded is not None
        assert loaded.student_level == "7th Grader, 9th Grader"
    session.rollback()


@pytest.mark.asyncio
async def test_publishing_a_multi_level_post(api_env, fresh_db, monkeypatch) -> None:
    session = fresh_db()
    monkeypatch.setattr(job_post_handler.settings, "job_post_chat_id", -100444)

    context = FakeContext()
    conversation, state = await _reach_levels(context)
    bot = context.bot

    for wanted in ("7th Grader", "9th Grader"):
        await dispatch(
            conversation,
            callback_update(f"post:student_level:toggle:{LEVELS.index(wanted)}", ADMIN_ID, bot),
            context,
            state,
        )
    await dispatch(
        conversation, callback_update("post:student_level:done", ADMIN_ID, bot), context, state
    )
    for value in (
        "Both Male and Female",
        "1 hour/day",
        "3 days/week",
        "Major Subjects",
        "300 Birr / 1 hour",
    ):
        state = await dispatch(conversation, _text(value, bot=bot), context, state)
    state = await dispatch(
        conversation, callback_update("post:publish", ADMIN_ID, bot), context, state
    )
    assert state == conversation.END

    with session_scope() as check:
        post = check.query(JobPost).one()
        assert post.student_level == "7th Grader, 9th Grader"
        assert "7th Grader" in post.generated_text
        assert "9th Grader" in post.generated_text
        assert post.is_published
        # the requirements block is still the static one
        assert "☑️ Proximity to #Ayat_Tsebel," in post.generated_text
    session.rollback()


@pytest.mark.asyncio
async def test_editing_the_level_replaces_the_selection(api_env) -> None:
    context = FakeContext()
    conversation, state = await _reach_levels(context)
    bot = context.bot

    for wanted in ("7th Grader", "9th Grader"):
        await dispatch(
            conversation,
            callback_update(f"post:student_level:toggle:{LEVELS.index(wanted)}", ADMIN_ID, bot),
            context,
            state,
        )
    await dispatch(
        conversation, callback_update("post:student_level:done", ADMIN_ID, bot), context, state
    )
    for value in (
        "Both Male and Female",
        "1 hour/day",
        "3 days/week",
        "Major Subjects",
        "300 Birr / 1 hour",
    ):
        await dispatch(conversation, _text(value, bot=bot), context, state)

    state = await dispatch(
        conversation, callback_update("post:edit", ADMIN_ID, bot), context, state
    )
    state = await dispatch(
        conversation,
        callback_update("post:editfield:student_level", ADMIN_ID, bot),
        context,
        state,
    )
    # editing clears the previous selection rather than merging into it
    assert "student_level" not in context.user_data["job_post"]

    state = await dispatch(
        conversation,
        callback_update(f"post:student_level:toggle:{LEVELS.index('12th Grader')}", ADMIN_ID, bot),
        context,
        state,
    )
    state = await dispatch(
        conversation, callback_update("post:student_level:done", ADMIN_ID, bot), context, state
    )

    assert state == job_post_handler.PREVIEW_STATE
    assert context.user_data["job_post"]["student_level"] == ["12th Grader"]
    preview = bot.sent[-1]
    assert "12th Grader" in preview
    assert "7th Grader" not in preview
    assert "☑️ Proximity to #Ayat_Tsebel," in preview


def test_preview_buttons_still_offer_edit_cancel_publish() -> None:
    labels = [
        b.text for row in post_preview_keyboard().inline_keyboard for b in row
    ]
    assert labels == ["✏️ Edit", "❌ Cancel", "📢 Publish"]
