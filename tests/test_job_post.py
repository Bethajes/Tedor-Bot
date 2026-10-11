"""Job-post generator tests.

Covers the template contract (static requirements, one location hashtag reused
everywhere), the seven-field conversation flow, the preview/edit cycle and
publishing.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest
from telegram import Chat, Message, MessageEntity, Update, User

from app.bot import build_application
from app.bot.handlers import admin as admin_handler
from app.bot.handlers import job_post as job_post_handler
from app.bot.options import JOB_POST_LOCATIONS
from app.bot.keyboards.job_post import (
    EDITABLE_FIELDS,
    post_edit_keyboard,
    post_preview_keyboard,
)
from app.bot.states import JobPostStates
from app.database import session_scope
from app.models import JobPost
from app.services import job_post_service
from app.templates.tutor_job_post import (
    MAJOR_SUBJECT_TAGS,
    REQUIRED_FIELDS,
    TEMPLATE,
    JobPostTemplateError,
    location_hashtag,
    render_job_post,
    render_subject_hashtags,
    render_tutor_category,
    template_placeholders,
    to_telegram_html,
)
from tests.factories import job_post_payload
from tests.test_conversation_flow import FakeBot, FakeContext, callback_update, dispatch

ADMIN_ID = 111


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _render(**overrides: object) -> str:
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
    return render_job_post(**values)  # type: ignore[arg-type]


def _bot_user(user_id: int) -> User:
    return User(
        id=user_id,
        first_name="Admin",
        last_name="User",
        username="admin1",
        is_bot=False,
    )


def _command_update(command: str, user_id: int = ADMIN_ID, bot: FakeBot | None = None) -> Update:
    text = f"/{command}"
    message = Message(
        message_id=1,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=user_id, type=Chat.PRIVATE),
        from_user=_bot_user(user_id),
        text=text,
        # Telegram always tags the command; ``CommandHandler.check_update``
        # ignores a message whose first entity is not a bot command.
        entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(text))],
    )
    message.set_bot(bot or FakeBot())
    return Update(update_id=1, message=message)


def _text_update(value: str, user_id: int = ADMIN_ID, bot: FakeBot | None = None) -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=user_id, type=Chat.PRIVATE),
        from_user=_bot_user(user_id),
        text=value,
    )
    message.set_bot(bot or FakeBot())
    return Update(update_id=1, message=message)


def _requirements_block(text: str) -> str:
    """Slice the static requirements block out of a rendered post."""
    start = text.index("✅ **Requirements**:")
    end = text.index("📩 Contact us:")
    return text[start:end]


def _description_line(text: str) -> str:
    return next(
        line for line in text.splitlines() if line.startswith("📃 **Tedor Tutors**")
    )


async def _collect_all(
    conversation, context: FakeContext, state: int | None, bot: FakeBot
) -> int:
    """Drive the seven steps using the spec's example answers."""
    answers = {
        "location": "Ayat Tsebel",
        "student_level": "4th Grader",
        "tutor_category": "Both Male and Female",
        "duration": "1 hour/day",
        "frequency": "3 days/week",
        "subjects": "Major Subjects",
        "compensation": "300 Birr / 1 hour",
    }
    for step in job_post_handler.STEPS:
        if state != int(step.state):
            continue
        state = await dispatch(
            conversation, _text_update(answers[step.field], bot=bot), context, state
        )
    return state


# ---------------------------------------------------------------------------
# template architecture
# ---------------------------------------------------------------------------


def test_template_placeholders_are_exactly_the_expected_set() -> None:
    assert set(template_placeholders()) == {
        "location",
        "student_level",
        "tutor_category",
        "duration",
        "frequency",
        "subjects",
        "compensation",
        "subject_hashtags",
    }


def test_template_has_no_requirements_placeholder() -> None:
    """The requirements cannot change because there is nowhere to inject them."""
    assert "requirements" not in {
        name.casefold() for name in template_placeholders()
    }
    assert "{requirements}" not in TEMPLATE
    assert "requirements" not in REQUIRED_FIELDS


def test_template_lives_outside_the_conversation_logic() -> None:
    """The static copy is a separate module from the flow handlers."""
    source = inspect.getsource(job_post_handler)
    assert "☑️ Proximity to" not in source
    assert "Passion for #teaching" not in source
    assert "Solid #academic background" not in source
    for step in job_post_handler.STEPS:
        assert "requirement" not in step.prompt.casefold(), step.field
        assert "requirement" not in step.field.casefold()


# ---------------------------------------------------------------------------
# location -> hashtag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entered", "expected"),
    [
        ("Ayat Tsebel", "Ayat_Tsebel"),
        ("Addis Ababa", "Addis_Ababa"),
        ("Bole", "Bole"),
        ("Saris", "Saris"),
        ("CMC", "CMC"),
        ("Gerji", "Gerji"),
    ],
)
def test_location_is_converted_to_a_hashtag(entered: str, expected: str) -> None:
    assert location_hashtag(entered) == expected
    assert f"#{expected}" in _render(location=entered)


def test_location_hashtag_ignores_a_typed_hashtag() -> None:
    """The admin never has to type the ``#``."""
    assert location_hashtag("#Ayat Tsebel") == "Ayat_Tsebel"


def test_location_is_inserted_into_the_main_description() -> None:
    text = _render(location="Ayat Tsebel")
    assert (
        "📃 **Tedor Tutors** is currently seeking an excellent, punctual, and "
        "dedicated tutor with strong English fluency for a tutoring opportunity "
        "in #Ayat_Tsebel." == _description_line(text)
    )


def test_location_is_inserted_into_the_requirements() -> None:
    assert "☑️ Proximity to #Ayat_Tsebel," in _requirements_block(_render())


def test_the_same_location_hashtag_is_reused_in_both_places() -> None:
    text = _render(location="Bole")
    slug = location_hashtag("Bole")
    assert text.count(f"#{slug}") == 2
    assert f"#{slug}" in _description_line(text)
    assert f"#{slug}" in _requirements_block(text)


# ---------------------------------------------------------------------------
# static requirements
# ---------------------------------------------------------------------------


def test_requirements_are_always_identical_except_for_the_location() -> None:
    baseline = _requirements_block(_render(location="Ayat Tsebel"))
    for location in ("Bole", "Saris", "CMC", "Gerji", "Addis Ababa"):
        other = _requirements_block(_render(location=location))
        assert other == baseline.replace("Ayat_Tsebel", location_hashtag(location))


def test_requirements_carry_the_exact_brand_wording() -> None:
    block = _requirements_block(_render(location="Bole"))
    assert block == (
        "✅ **Requirements**:\n"
        "\n"
        "☑️ Proximity to #Bole,\n"
        "☑️ Solid #academic background,\n"
        "☑️ Passion for #teaching,\n"
        "☑️ Strong command of the #English language and robust #Math foundation.\n"
        "\n"
    )


@pytest.mark.parametrize(
    "field",
    ["student_level", "tutor_category", "duration", "frequency", "subjects", "compensation"],
)
def test_editing_any_other_field_does_not_touch_the_requirements(field: str) -> None:
    baseline = _requirements_block(_render())
    changed = _requirements_block(_render(**{field: "something completely different"}))
    assert changed == baseline


def test_location_is_the_only_thing_that_appears_twice_in_the_requirements() -> None:
    block = _requirements_block(_render())
    assert block.count("#academic") == 1
    assert block.count("#teaching") == 1
    assert block.count("#English") == 1
    assert block.count("#Math") == 1


# ---------------------------------------------------------------------------
# subject hashtags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subjects", "expected"),
    [
        ("Mathematics", "#MATH"),
        ("English", "#ENGLISH"),
        ("Physics", "#PHYSICS"),
        ("Chemistry", "#CHEMISTRY"),
        ("Biology", "#BIOLOGY"),
        ("Science", "#SCIENCE"),
        ("Major Subjects", "#ENGLISH #MATH #SCIENCE"),
    ],
)
def test_subject_hashtags_are_generated(subjects: str, expected: str) -> None:
    assert render_subject_hashtags(subjects) == expected
    assert expected in _render(subjects=subjects)


def test_major_subjects_pins_the_three_documented_tags() -> None:
    assert list(MAJOR_SUBJECT_TAGS) == ["ENGLISH", "MATH", "SCIENCE"]


def test_brand_and_amharic_hashtags_are_always_appended() -> None:
    line = next(
        line for line in _render().splitlines() if line.startswith("#TEDOR")
    )
    assert line == (
        "#TEDOR #TUTORING #TUTORIAL #TUTOR #ENGLISH #MATH #SCIENCE "
        "#አስጠኚዎች #አስጠኚ"
    )


def test_multiple_subjects_produce_multiple_tags() -> None:
    assert render_subject_hashtags("Mathematics and Physics") == "#MATH #PHYSICS"


def test_no_irrelevant_hashtags_are_invented() -> None:
    tags = render_subject_hashtags("Mathematics").split()
    assert tags == ["#MATH"]


# ---------------------------------------------------------------------------
# tutor category
# ---------------------------------------------------------------------------


def test_tutor_category_hashtags_the_gender_words() -> None:
    assert render_tutor_category("Both Male and Female") == "Both #Male and #Female"
    assert "✍️ **Tutor Category**: Both #Male and #Female" in _render()


# ---------------------------------------------------------------------------
# exact template
# ---------------------------------------------------------------------------

EXPECTED_POST = "\n".join(
    [
        "🚨 **Urgent Job Opening** 📰",
        "",
        "Job Status: **Open**",
        "",
        "📃 **Tedor Tutors** is currently seeking an excellent, punctual, and "
        "dedicated tutor with strong English fluency for a tutoring opportunity "
        "in #Ayat_Tsebel.",
        "",
        "✍️ **Student's Level**: 4th Grader",
        "",
        "✍️ **Tutor Category**: Both #Male and #Female",
        "",
        "✍️ **Duration**: 1 hour/day",
        "",
        "✍️ **Frequency**: 3 days/week",
        "",
        "✍️ **Target Subjects**: Major Subjects",
        "",
        "💰 **Compensation**: 300 Birr / 1 hour",
        "",
        "✅ **Requirements**:",
        "",
        "☑️ Proximity to #Ayat_Tsebel,",
        "☑️ Solid #academic background,",
        "☑️ Passion for #teaching,",
        "☑️ Strong command of the #English language and robust #Math foundation.",
        "",
        "📩 Contact us: @Tedor_Team",
        "",
        "#TEDOR #TUTORING #TUTORIAL #TUTOR #ENGLISH #MATH #SCIENCE "
        "#አስጠኚዎች #አስጠኚ",
    ]
)


def test_generated_post_matches_the_exact_template() -> None:
    assert _render() == EXPECTED_POST


def test_the_post_never_stacks_blank_lines() -> None:
    """Runs of blank lines render as tall dead gaps in the Telegram message.

    The template used to pad between sections with three blank lines each,
    which showed up as a large empty band before Requirements and again before
    the contact line.
    """
    text = _render()
    assert "\n\n\n" not in text, "sections are separated by a single blank line"
    assert not text.startswith("\n")
    assert not text.endswith("\n")


def test_each_section_is_separated_by_exactly_one_blank_line() -> None:
    lines = _render().split("\n")
    for marker in ("✅ **Requirements**:", "📩 Contact us: @Tedor_Team"):
        index = lines.index(marker)
        assert lines[index - 1] == "", f"{marker} needs one blank line above it"

    # Compensation is the last header line, so match it by prefix.
    compensation = next(
        index for index, line in enumerate(lines) if line.startswith("💰 **Compensation**")
    )
    assert lines[compensation - 1] == ""


def test_empty_location_cannot_be_rendered() -> None:
    with pytest.raises(JobPostTemplateError):
        render_job_post(
            location="   ",
            student_level="4th Grader",
            tutor_category="Male",
            duration="1 hour/day",
            frequency="3 days/week",
            subjects="Mathematics",
            compensation="300 Birr",
        )


def test_telegram_html_conversion_only_touches_bold_markers() -> None:
    html_text = to_telegram_html(_render())
    assert "<b>Urgent Job Opening</b>" in html_text
    assert "**" not in html_text
    assert "#Ayat_Tsebel" in html_text
    assert "☑️ Proximity to #Ayat_Tsebel," in html_text


def test_admin_entered_values_are_escaped_for_telegram() -> None:
    html_text = to_telegram_html(_render(compensation="300 <b>Birr</b> & up"))
    assert "&lt;b&gt;Birr&lt;/b&gt; &amp; up" in html_text


# ---------------------------------------------------------------------------
# conversation flow
# ---------------------------------------------------------------------------


def test_the_flow_collects_exactly_seven_fields(api_env) -> None:
    assert len(job_post_handler.STEPS) == 7
    assert [step.field for step in job_post_handler.STEPS] == list(REQUIRED_FIELDS)


def test_state_bands_stay_disjoint_from_the_other_flows(api_env) -> None:
    from app.bot.states import AdminStates, StudentStates, SupportStates, TutorStates

    values = [
        {int(item) for item in enum}
        for enum in (TutorStates, StudentStates, SupportStates, AdminStates, JobPostStates)
    ]
    for index, left in enumerate(values):
        for right in values[index + 1 :]:
            assert not left & right


def test_the_job_post_conversation_is_registered(api_env) -> None:
    application = build_application()
    names = [getattr(handler, "name", None) for handler in application.handlers[0]]
    assert "job_post" in names
    # the job-post flow must be registered before the admin console so that it
    # owns the ``admin:post_job`` button instead of the admin conversation
    assert names.index("job_post") < names.index("admin")


def test_the_admin_console_offers_the_job_post_button(api_env) -> None:
    from app.bot.keyboards import admin_menu_keyboard

    buttons = [
        button for row in admin_menu_keyboard().inline_keyboard for button in row
    ]
    labels = [button.text for button in buttons]
    assert "📰 Post Job Opening" in labels
    assert "admin:post_job" in [button.callback_data for button in buttons]
    for button in buttons:
        assert len((button.callback_data or "").encode("utf-8")) <= 64


@pytest.mark.asyncio
async def test_the_admin_console_button_starts_the_flow(api_env) -> None:
    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(
        conversation, callback_update("admin:post_job", ADMIN_ID, bot), context, None
    )
    assert state == int(JobPostStates.LOCATION)
    assert "Step 1/7" in bot.sent[-1]


@pytest.mark.asyncio
async def test_start_post_publishes_the_slash_command_list(api_env) -> None:
    """Without this the admin sees no command list and cannot find /post."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.bot import _publish_admin_commands

    bot = SimpleNamespace(set_my_commands=AsyncMock())
    application = SimpleNamespace(bot=bot)

    await _publish_admin_commands(application)

    assert bot.set_my_commands.await_count == len(api_env.admin_user_ids)
    published = bot.set_my_commands.await_args.kwargs["commands"]
    assert "post" in [command.command for command in published]
    scope = bot.set_my_commands.await_args.kwargs["scope"]
    assert scope.chat_id in {int(item) for item in api_env.admin_user_ids}


@pytest.mark.asyncio
async def test_command_list_failure_never_breaks_startup(api_env) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from telegram.error import BadRequest

    from app.bot import _publish_admin_commands

    bot = SimpleNamespace(set_my_commands=AsyncMock(side_effect=BadRequest("nope")))
    await _publish_admin_commands(SimpleNamespace(bot=bot))


@pytest.mark.asyncio
async def test_location_is_requested_only_once(api_env) -> None:
    """The seven prompts contain exactly one location question."""
    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    state = await dispatch(
        conversation, _command_update("post", bot=context.bot), context, None
    )
    assert state == int(JobPostStates.LOCATION)

    bot = context.bot
    state = await _collect_all(conversation, context, state, bot)
    assert state == job_post_handler.PREVIEW_STATE

    location_prompts = [
        text for text in bot.sent if "Step 1/7" in text or "Location" in text
    ]
    assert len(location_prompts) == 1

    # and the collected payload holds exactly one location value
    assert list(job_post_handler.get_post(context)) == [
        "telegram_user_id",
        *REQUIRED_FIELDS,
    ]


@pytest.mark.asyncio
async def test_admin_is_never_asked_for_requirements(api_env) -> None:
    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(conversation, _command_update("post", bot=bot), context, None)
    state = await _collect_all(conversation, context, state, bot)

    prompts = [
        text
        for text in bot.sent
        if any(f"Step {n}/7" in text for n in range(1, 8))
    ]
    assert len(prompts) == 7

    for text in prompts:
        assert "requirement" not in text.casefold()
        assert "academic background" not in text.casefold()
        assert "passion" not in text.casefold()
        assert "english requirement" not in text.casefold()

    # the only requirements the admin ever sees are the generated static block
    assert "requirements" not in job_post_handler.get_post(context)
    preview = bot.sent[-1]
    assert _requirements_block(preview.replace("<b>", "**").replace("</b>", "**")) == (
        _requirements_block(_render())
    )


@pytest.mark.asyncio
async def test_choice_keyboard_values_are_accepted(api_env) -> None:
    from app.bot.options import JOB_POST_SUBJECTS, JOB_POST_TUTOR_CATEGORIES

    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(conversation, _command_update("post", bot=bot), context, None)
    assert state == int(JobPostStates.LOCATION)

    state = await dispatch(
        conversation, _text_update("Ayat Tsebel", bot=bot), context, state
    )
    assert state == int(JobPostStates.STUDENT_LEVEL)

    category_index = list(JOB_POST_TUTOR_CATEGORIES).index("Both Male and Female")
    state = await dispatch(
        conversation,
        callback_update(f"post:tutor_category:pick:{category_index}", ADMIN_ID, bot),
        context,
        state,
    )
    assert job_post_handler.get_post(context)["tutor_category"] == "Both Male and Female"
    assert state == int(JobPostStates.DURATION)

    state = await dispatch(conversation, _text_update("1 hour/day", bot=bot), context, state)
    assert state == int(JobPostStates.FREQUENCY)

    state = await dispatch(conversation, _text_update("3 days/week", bot=bot), context, state)
    assert state == int(JobPostStates.SUBJECTS)

    subject_index = list(JOB_POST_SUBJECTS).index("Major Subjects")
    state = await dispatch(
        conversation,
        callback_update(f"post:subjects:pick:{subject_index}", ADMIN_ID, bot),
        context,
        state,
    )
    assert job_post_handler.get_post(context)["subjects"] == "Major Subjects"
    assert state == int(JobPostStates.COMPENSATION)


@pytest.mark.asyncio
async def test_invalid_location_keeps_the_admin_on_the_step(api_env) -> None:
    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(conversation, _command_update("post", bot=bot), context, None)

    for _ in range(3):
        state = await dispatch(
            conversation, _text_update("!!!", bot=bot), context, state
        )
        assert state == int(JobPostStates.LOCATION)
    assert "location" not in job_post_handler.get_post(context)
    assert bot.sent[-1].startswith("⚠")


@pytest.mark.asyncio
async def test_non_admin_cannot_start_a_post(api_env) -> None:
    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(
        conversation, _command_update("post", user_id=999, bot=bot), context, None
    )
    assert state == conversation.END
    denial = next(text for text in bot.sent if "Access denied" in text)
    assert "999" in denial
    assert "ADMIN_USER_IDS" in denial


@pytest.mark.asyncio
async def test_id_command_reports_the_callers_own_id(api_env) -> None:
    from app.bot.handlers.start import id_command

    context = FakeContext()
    bot = context.bot
    await id_command(_text_update("hi", user_id=987654321, bot=bot), context)

    reply = bot.sent[-1]
    assert "987654321" in reply
    assert "not an admin" in reply
    assert "✅" not in reply


@pytest.mark.asyncio
async def test_id_command_confirms_a_configured_admin(api_env) -> None:
    from app.bot.handlers.start import id_command

    context = FakeContext()
    bot = context.bot
    await id_command(_text_update("hi", user_id=ADMIN_ID, bot=bot), context)
    assert "You are an admin" in bot.sent[-1]


def test_the_denial_message_explains_the_channel_id_mistake(api_env) -> None:
    from app.bot.handlers.admin import access_denied_text

    text = access_denied_text(4242)
    assert "4242" in text
    assert "ADMIN_USER_IDS" in text
    assert access_denied_text(None) == admin_handler.ACCESS_DENIED


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_shows_the_finished_post_and_offers_the_three_actions(api_env) -> None:
    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(conversation, _command_update("post", bot=bot), context, None)
    state = await _collect_all(conversation, context, state, bot)

    assert state == job_post_handler.PREVIEW_STATE
    preview = bot.sent[-1]
    assert "Preview" in preview
    assert "<b>Urgent Job Opening</b>" in preview
    assert "#Ayat_Tsebel" in preview
    assert "☑️ Proximity to #Ayat_Tsebel," in preview

    buttons = [b for row in post_preview_keyboard().inline_keyboard for b in row]
    assert [button.text for button in buttons] == [
        "✏️ Edit",
        "❌ Cancel",
        "📢 Publish",
    ]


@pytest.mark.asyncio
async def test_preview_does_not_publish(api_env, fresh_db) -> None:
    session = fresh_db()
    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(conversation, _command_update("post", bot=bot), context, None)
    await _collect_all(conversation, context, state, bot)

    assert bot.sent  # the preview was sent to the admin
    with session_scope() as check:
        assert check.query(JobPost).count() == 0
    session.rollback()


@pytest.mark.asyncio
async def test_cancel_stores_nothing(api_env, fresh_db) -> None:
    session = fresh_db()
    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(conversation, _command_update("post", bot=bot), context, None)
    state = await _collect_all(conversation, context, state, bot)
    state = await dispatch(
        conversation, callback_update("post:cancel", ADMIN_ID, bot), context, state
    )

    assert state == conversation.END
    with session_scope() as check:
        assert check.query(JobPost).count() == 0
    session.rollback()


# ---------------------------------------------------------------------------
# editing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_editing_the_location_updates_every_occurrence(api_env) -> None:
    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(conversation, _command_update("post", bot=bot), context, None)
    state = await _collect_all(conversation, context, state, bot)

    state = await dispatch(
        conversation, callback_update("post:edit", ADMIN_ID, bot), context, state
    )
    assert state == job_post_handler.EDIT_MENU_STATE

    state = await dispatch(
        conversation,
        callback_update("post:editfield:location", ADMIN_ID, bot),
        context,
        state,
    )
    assert state == int(JobPostStates.LOCATION)

    state = await dispatch(
        conversation, _text_update("Bole", bot=bot), context, state
    )
    assert state == job_post_handler.PREVIEW_STATE

    preview = bot.sent[-1]
    assert "#Ayat_Tsebel" not in preview
    assert preview.count("#Bole") == 2
    assert job_post_handler.get_post(context)["location"] == "Bole"


@pytest.mark.asyncio
async def test_editing_the_subject_regenerates_the_subject_hashtags(api_env) -> None:
    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(conversation, _command_update("post", bot=bot), context, None)
    state = await _collect_all(conversation, context, state, bot)

    await dispatch(
        conversation, callback_update("post:edit", ADMIN_ID, bot), context, state
    )
    await dispatch(
        conversation,
        callback_update("post:editfield:subjects", ADMIN_ID, bot),
        context,
        job_post_handler.EDIT_MENU_STATE,
    )
    await dispatch(
        conversation,
        _text_update("Chemistry", bot=bot),
        context,
        int(JobPostStates.SUBJECTS),
    )

    preview = bot.sent[-1]
    assert "#CHEMISTRY" in preview
    assert "#ENGLISH #MATH #SCIENCE" not in preview
    assert "☑️ Proximity to #Ayat_Tsebel," in preview


@pytest.mark.asyncio
async def test_editing_a_plain_field_leaves_the_requirements_untouched(api_env) -> None:
    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(conversation, _command_update("post", bot=bot), context, None)
    state = await _collect_all(conversation, context, state, bot)

    before = _requirements_block(
        job_post_handler.preview_text(job_post_handler.get_post(context))
    )

    await dispatch(
        conversation, callback_update("post:edit", ADMIN_ID, bot), context, state
    )
    await dispatch(
        conversation,
        callback_update("post:editfield:compensation", ADMIN_ID, bot),
        context,
        job_post_handler.EDIT_MENU_STATE,
    )
    await dispatch(
        conversation,
        _text_update("500 Birr / 1 hour", bot=bot),
        context,
        int(JobPostStates.COMPENSATION),
    )

    after = _requirements_block(
        job_post_handler.preview_text(job_post_handler.get_post(context))
    )
    assert after == before
    assert "500 Birr / 1 hour" in bot.sent[-1]


def test_the_edit_menu_offers_no_requirements_field() -> None:
    data = [
        button.callback_data
        for row in post_edit_keyboard().inline_keyboard
        for button in row
        if button.callback_data and button.callback_data.startswith("post:editfield:")
    ]
    assert data == [f"post:editfield:{key}" for key, _label in EDITABLE_FIELDS]
    assert not any("requirement" in item for item in data)


def test_every_job_post_callback_data_fits_telegram_limit() -> None:
    from app.bot.keyboards.job_post import post_choice_keyboard, post_nav_keyboard

    keyboards = [
        post_preview_keyboard(),
        post_edit_keyboard(),
        post_nav_keyboard(),
        post_choice_keyboard("location", list(JOB_POST_LOCATIONS)),
    ]
    for step in job_post_handler.STEPS:
        keyboards.append(job_post_handler._keyboard_for(step, {}))

    for keyboard in keyboards:
        for row in keyboard.inline_keyboard:
            for button in row:
                if button.callback_data is None:
                    continue
                assert len(button.callback_data.encode("utf-8")) <= 64, (
                    button.callback_data
                )


# ---------------------------------------------------------------------------
# publishing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publishing_works(api_env, fresh_db, monkeypatch) -> None:
    session = fresh_db()
    monkeypatch.setattr(job_post_handler.settings, "job_post_chat_id", -100555)

    conversation = job_post_handler.build_job_post_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(conversation, _command_update("post", bot=bot), context, None)
    state = await _collect_all(conversation, context, state, bot)
    assert state == job_post_handler.PREVIEW_STATE

    state = await dispatch(
        conversation, callback_update("post:publish", ADMIN_ID, bot), context, state
    )
    assert state == conversation.END

    # the post itself went to the job channel as HTML
    published = [text for text in bot.sent if "<b>Urgent Job Opening</b>" in text]
    assert published, "the post was never sent"
    assert "#Ayat_Tsebel" in published[-1]

    with session_scope() as check:
        post = check.query(JobPost).one()
        assert post.public_post_id.startswith("JOB-")
        assert post.telegram_user_id == ADMIN_ID
        assert post.location == "Ayat Tsebel"
        assert post.student_level == "4th Grader"
        assert post.tutor_category == "Both Male and Female"
        assert post.duration == "1 hour/day"
        assert post.frequency == "3 days/week"
        assert post.subjects == "Major Subjects"
        assert post.compensation == "300 Birr / 1 hour"
        assert post.generated_text == EXPECTED_POST
        assert post.telegram_chat_id == -100555
        assert post.telegram_message_id is not None
        assert post.published_at is not None
        assert post.is_published
    session.rollback()


@pytest.mark.asyncio
async def test_publishing_never_stores_requirements_separately(api_env, fresh_db) -> None:
    session = fresh_db()
    monkey_job_post = JobPost.__table__
    assert "requirements" not in monkey_job_post.columns.keys()

    with session_scope() as create:
        post = job_post_service.create_job_post(
            create, job_post_payload(-4242, requirements="IGNORE ME")
        )
        post_id = post.public_post_id

    with session_scope() as check:
        stored = job_post_service.get_job_post_by_id(check, post_id)
        assert stored is not None
        assert "IGNORE ME" not in stored.generated_text
        assert "☑️ Proximity to #Ayat_Tsebel," in stored.generated_text
    session.rollback()


@pytest.mark.asyncio
async def test_publishing_without_every_field_is_refused(api_env, fresh_db) -> None:
    session = fresh_db()
    context = FakeContext()
    context.bot = FakeBot()
    job_post_handler.get_post(context).update(
        {"telegram_user_id": ADMIN_ID, "location": "Bole"}
    )

    state = await job_post_handler.publish_post(
        _text_update("publish", bot=context.bot), context
    )
    assert state == job_post_handler.PREVIEW_STATE
    assert "Cannot publish yet" in context.bot.sent[-1]
    with session_scope() as check:
        assert check.query(JobPost).count() == 0
    session.rollback()


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------


def test_service_always_renders_the_static_template(api_env, fresh_db) -> None:
    session = fresh_db()
    with session_scope() as create:
        post = job_post_service.create_job_post(create, job_post_payload(-1))
        post_id = post.public_post_id
    with session_scope() as check:
        stored = job_post_service.get_job_post_by_id(check, post_id)
        assert stored is not None
        assert stored.generated_text == EXPECTED_POST
    session.rollback()


def test_service_rejects_an_incomplete_payload(api_env, fresh_db) -> None:
    session = fresh_db()
    payload = job_post_payload(-1)
    payload.pop("compensation")
    with session_scope() as create:
        with pytest.raises(job_post_service.JobPostValidationError):
            job_post_service.create_job_post(create, payload)
    session.rollback()


def test_job_post_ids_are_unique_and_monotonic(api_env, fresh_db) -> None:
    session = fresh_db()
    ids: list[str] = []
    with session_scope() as create:
        for index in range(3):
            post = job_post_service.create_job_post(
                create, job_post_payload(-100 - index)
            )
            ids.append(post.public_post_id)
    assert len(set(ids)) == 3
    assert ids == sorted(ids)
    assert all(job_post_service.JOB_POST_PATTERN.match(item) for item in ids)
    session.rollback()


def test_publishing_is_recorded_on_the_model(api_env, fresh_db) -> None:
    session = fresh_db()
    with session_scope() as create:
        post = job_post_service.create_job_post(create, job_post_payload(-1))
        post_id = post.public_post_id
    with session_scope() as update:
        stored = job_post_service.get_job_post_by_id(update, post_id)
        assert stored is not None
        job_post_service.mark_published(update, stored, -100777, 4321)
    with session_scope() as check:
        stored = job_post_service.get_job_post_by_id(check, post_id)
        assert stored is not None
        assert stored.telegram_chat_id == -100777
        assert stored.telegram_message_id == 4321
        assert stored.published_at is not None
        assert stored.is_published
    session.rollback()