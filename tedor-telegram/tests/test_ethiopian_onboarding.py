"""End-to-end Ethiopian onboarding conversation tests.

Drives the new flow through the same dispatch emulator used for the legacy
tutor flow, so every step is exercised without contacting Telegram.
"""

from __future__ import annotations

from typing import Any

import pytest
from telegram import CallbackQuery, Chat, Message, PhotoSize, Update, User

from app.bot.handlers import tutor_onboarding as eth
from app.bot.states import EthiopianTutorStates
from app.database import session_scope
from app.enums import DocumentType
from app.services import tutor_profile_service as P
from app.services import tutor_service


class FakeBot:
    username = "tedor_tutors_test_bot"

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.answers: list[str] = []
        self._next = 900

    def _bump(self) -> int:
        self._next += 1
        return self._next

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> Message:
        self.sent.append(text)
        return Message(
            message_id=self._bump(),
            date=None,
            chat=Chat(id=chat_id, type=Chat.PRIVATE),
            text=text,
        )

    async def edit_message_text(self, text: str = "", **kwargs: Any) -> Message:
        self.sent.append(text)
        return Message(
            message_id=self._bump(),
            date=None,
            chat=Chat(id=1, type=Chat.PRIVATE),
            text=text,
        )

    async def edit_message_reply_markup(self, **kwargs: Any) -> bool:
        return True

    async def answer_callback_query(self, text: str = "", **kwargs: Any) -> bool:
        self.answers.append(text)
        return True

    # The Telegram storage layer broadcasts the profile, documents, voice and
    # photos into the admin/archive channel; stubs let the archive path run
    # end-to-end without Telegram.
    async def _stub_send(self, text: str, **kwargs: Any) -> Message:
        self.sent.append(text)
        return Message(
            message_id=self._bump(),
            date=None,
            chat=Chat(id=kwargs.get("chat_id", -1000), type=Chat.CHANNEL),
            text=text,
        )

    async def send_document(self, **kwargs: Any) -> Message:
        caption = kwargs.get("caption") or "document"
        return await self._stub_send(caption, **kwargs)

    async def send_photo(self, **kwargs: Any) -> Message:
        caption = kwargs.get("caption") or "photo"
        return await self._stub_send(caption, **kwargs)

    async def send_voice(self, **kwargs: Any) -> Message:
        caption = kwargs.get("caption") or "voice"
        return await self._stub_send(caption, **kwargs)


class FakeContext:
    def __init__(self, bot: FakeBot | None = None) -> None:
        self.user_data: dict[str, Any] = {}
        self.bot = bot or FakeBot()
        self.chat_data: dict[str, Any] = {}
        self.args: list[Any] = []


def _user(user_id: int = 4242) -> User:
    return User(id=user_id, first_name="Test", last_name="User", username="tutor1", is_bot=False)


def _message(user_id: int, **kwargs: Any) -> Message:
    message = Message(
        message_id=1,
        date=None,
        chat=Chat(id=user_id, type=Chat.PRIVATE),
        from_user=_user(user_id),
        **kwargs,
    )
    return message


def text_update(value: str, user_id: int = 4242, bot: FakeBot | None = None) -> Update:
    message = _message(user_id, text=value)
    message.set_bot(bot or FakeBot())
    return Update(update_id=1, message=message)


def callback_update(data: str, user_id: int = 4242, bot: FakeBot | None = None) -> Update:
    message = _message(user_id)
    query = CallbackQuery(
        id="query-1",
        from_user=_user(user_id),
        chat_instance="instance",
        message=message,
        data=data,
    )
    stub = bot or FakeBot()
    query.set_bot(stub)
    message.set_bot(stub)
    return Update(update_id=1, callback_query=query)


def command_update(command: str, user_id: int = 4242, bot: FakeBot | None = None) -> Update:
    """A real command message, so ``CommandHandler.check_update`` matches."""
    from telegram import MessageEntity

    message = _message(
        user_id,
        text=f"/{command}",
        entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(command) + 1)],
    )
    message.set_bot(bot or FakeBot())
    return Update(update_id=1, message=message)


def voice_update(user_id: int = 4242, bot: FakeBot | None = None) -> Update:
    from telegram import Voice

    message = _message(
        user_id,
        voice=Voice(file_id="voice-file-1", file_unique_id="vu", duration=45),
    )
    message.set_bot(bot or FakeBot())
    return Update(update_id=1, message=message)


def document_update(
    user_id: int = 4242, file_name: str = "transcript.pdf", bot: FakeBot | None = None
) -> Update:
    from telegram import Document

    message = _message(
        user_id,
        document=Document(file_id="doc-file-1", file_unique_id="du", file_name=file_name),
    )
    message.set_bot(bot or FakeBot())
    return Update(update_id=1, message=message)


async def drive(
    conversation,
    context: FakeContext,
    answers: list[tuple[str, str]],
    state: int | None = None,
    start: bool = True,
) -> int:
    """Run a list of ``(kind, value)`` answers, always via ``context.bot``.

    The bot must be threaded through explicitly: ``Message.reply_text`` sends
    to the bot attached to the message, so a helper that invents its own stub
    would silently discard everything the handlers emit.

    ``start`` sends the "Become a Tutor" button first; pass ``False`` when the
    first answer is itself an entry point (a command, for example).
    """
    if state is None and start:
        state = await dispatch(
            conversation,
            callback_update("menu:become_tutor_ethiopia", bot=context.bot),
            context,
            None,
        )
    for kind, value in answers:
        if kind == "text":
            update = text_update(value, bot=context.bot)
        elif kind == "voice":
            update = voice_update(bot=context.bot)
        elif kind == "document":
            update = document_update(bot=context.bot)
        elif kind == "command":
            update = command_update(value, bot=context.bot)
        else:
            update = callback_update(value, bot=context.bot)
        state = await dispatch(conversation, update, context, state)
    return state


#: A complete, valid set of answers. Indices follow ``ETH_GRADES`` and
#: ``ETH_ENGLISH_LEVELS``; tutoring areas are free text.
_COMPLETE_ANSWERS: list[tuple[str, str]] = [
    ("text", "Abebe Bekele"),
    ("callback", "eth:gender:pick:0"),            # Male
    ("text", "27"),
    ("text", "+251911234567"),
    ("text", "Bole, Addis Ababa"),
    ("text", "Ayat Tsebel, Bole"),
    ("callback", "eth:grades:toggle:1"),           # Primary school (1-6)
    ("callback", "eth:grades:toggle:6"),           # University
    ("callback", "eth:grades:done"),
    ("callback", "eth:subjects:toggle:0"),        # Mathematics
    ("callback", "eth:subjects:done"),
    ("callback", "eth:english:pick:8"),            # 9/10
    ("callback", "eth:continue"),                  # skip the voice intro
    ("callback", "eth:education_level:pick:1"),    # University graduate
    ("text", "AASTU"),
    ("text", "Mathematics"),
    ("text", "2019"),
    ("text", "3.75"),
    ("text", "700"),
    ("text", "612"),
    ("text", "2016"),
    ("callback", "eth:academic_stream:pick:0"),    # Natural
    ("text", "82"),                              # Grade 8 exam average
    ("text", "78"),                              # Grade 9
    ("text", "80"),                              # Grade 10
    ("text", "84"),                              # Grade 11
    ("text", "86"),                              # Grade 12
    ("text", "3"),
    ("text", "Taught Grade 4-6 mathematics in Ayat Tsebel."),
    ("document", "transcript.pdf"),                # university transcript
    ("document", "transcript.pdf"),                # university entrance exam result
    ("callback", "eth:documents:done"),
]

#: Everything up to (but excluding) the Documents step — the answer list a test
#: needs to sit on that step. Derived rather than hard-coded so inserting a step
#: cannot silently point these tests at the wrong one.
_TO_DOCUMENTS = _COMPLETE_ANSWERS.index(("document", "transcript.pdf"))


def _handler_for(handlers, update: Update):
    for handler in handlers:
        check = handler.check_update(update)
        if check is not None and check is not False:
            return handler
    return None


async def dispatch(conversation, update: Update, context: FakeContext, state: int | None):
    handler = None
    if state is None or conversation.allow_reentry:
        handler = _handler_for(conversation.entry_points, update)
    if handler is None and state is not None:
        handler = _handler_for(conversation.states.get(state, []), update)
    if handler is None:
        handler = _handler_for(conversation.fallbacks, update)
    if handler is None:
        return state
    result = await handler.callback(update, context)
    if result is None or result == conversation.END or result == state:
        return result if result is not None else state
    return result


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_ethiopian_onboarding_flow(api_env, fresh_db) -> None:
    """Drive every step and submit, then verify the stored profile."""
    session = fresh_db()
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS)
    assert state == int(EthiopianTutorStates.SUMMARY)

    state = await drive(
        conversation, context, [("callback", "eth:submit")], state=state
    )
    assert state == conversation.END

    with session_scope() as check:
        tutor = tutor_service.get_tutor_by_telegram_id(check, 4242)
        assert tutor is not None
        assert tutor.public_tutor_id.startswith("TDR-")
        assert tutor.name == "Abebe Bekele"
        assert tutor.gender == "Male"
        assert tutor.age == 27
        assert tutor.phone == "+251911234567"
        assert tutor.current_address == "Bole, Addis Ababa"
        assert tutor.location_list == ["Ayat Tsebel", "Bole"]
        assert tutor.normalized_location_list == ["ayat tsebel", "bole"]
        assert tutor.grade_list == ["Primary school (1-6)", "University"]
        assert tutor.subject_list == ["Mathematics"]
        assert tutor.english_proficiency == 9
        assert tutor.education_level == "UNIVERSITY_GRADUATE"
        assert tutor.university == "AASTU"
        assert tutor.department == "Mathematics"
        assert tutor.university_year == 2019
        assert tutor.cgpa == 3.75
        assert tutor.academic_stream == "NATURAL"
        assert tutor.entrance_exam_max_score == 700
        assert tutor.entrance_exam_score == 612
        assert tutor.entrance_exam_year == 2016
        assert tutor.teaching_experience_years == 3
        assert tutor.status == "PENDING"
        assert tutor.member_referral_status == "NOT_COMPLETED"
        # Requirement 2: no pricing is ever asked of an Ethiopian tutor.
        assert tutor.etb_rate is None
        assert tutor.usd_rate is None


@pytest.mark.asyncio
async def test_a_new_tutor_is_not_verified_on_submission(api_env, fresh_db) -> None:
    """Submission never grants VERIFIED — that stays an admin decision."""
    session = fresh_db()
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS)
    state = await drive(
        conversation, context, [("callback", "eth:submit")], state=state
    )
    with session_scope() as check:
        tutor = tutor_service.get_tutor_by_telegram_id(check, 4242)
        assert tutor.status == "PENDING"
        assert tutor.verified_at is None


# ---------------------------------------------------------------------------
# navigation (section 6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_back_button_preserves_answers(api_env) -> None:
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(
        conversation, context, [("text", "Abebe Bekele"), ("callback", "eth:gender:pick:0")]
    )
    assert state == int(EthiopianTutorStates.AGE)

    state = await drive(conversation, context, [("callback", "eth:back")], state=state)
    assert state == int(EthiopianTutorStates.GENDER)
    assert eth.get_app(context)["full_name"] == "Abebe Bekele"
    assert eth.get_app(context)["gender"] == "Male"


@pytest.mark.asyncio
async def test_edit_menu_jumps_to_a_step(api_env) -> None:
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, [("text", "Abebe Bekele")])
    assert state == int(EthiopianTutorStates.GENDER)

    state = await drive(conversation, context, [("callback", "eth:edit")], state=state)
    state = await drive(
        conversation,
        context,
        [("callback", f"eth:editfield:{int(EthiopianTutorStates.PHONE)}")],
        state=state,
    )
    assert state == int(EthiopianTutorStates.PHONE)


@pytest.mark.asyncio
async def test_cancel_ends_the_flow_and_clears_the_form(api_env) -> None:
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, [("text", "Abebe Bekele")])
    state = await drive(conversation, context, [("callback", "eth:cancel")], state=state)
    assert state == conversation.END
    assert eth.get_app(context) == {}
    assert eth.current_step_state(context) is None


@pytest.mark.asyncio
async def test_multiselect_needs_at_least_one_option(api_env) -> None:
    """Section 23: grades and subjects are required."""
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(
        conversation,
        context,
        [
            ("text", "Abebe Bekele"),
            ("callback", "eth:gender:pick:0"),
            ("text", "27"),
            ("text", "+251911234567"),
            ("text", "Bole, Addis Ababa"),
            ("text", "Ayat Tsebel, Bole"),
            ("callback", "eth:grades:done"),
        ],
    )
    assert state == int(EthiopianTutorStates.GRADES)


@pytest.mark.asyncio
async def test_a_toggled_option_can_be_untoggled(api_env) -> None:
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(
        conversation,
        context,
        [
            ("text", "Abebe Bekele"),
            ("callback", "eth:gender:pick:0"),
            ("text", "27"),
            ("text", "+251911234567"),
            ("text", "Bole, Addis Ababa"),
            ("text", "Ayat Tsebel, Bole"),
            ("callback", "eth:grades:toggle:1"),
            ("callback", "eth:grades:toggle:1"),
        ],
    )
    assert eth.get_app(context)["grades"] == []
    assert state == int(EthiopianTutorStates.GRADES)


# ---------------------------------------------------------------------------
# validation (section 23)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("age", ["15", "5", "abc", "-3"])
async def test_an_underage_or_nonsense_age_is_rejected(api_env, age: str) -> None:
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(
        conversation,
        context,
        [("text", "Abebe Bekele"), ("callback", "eth:gender:pick:0"), ("text", age)],
    )
    assert state == int(EthiopianTutorStates.AGE)


@pytest.mark.asyncio
@pytest.mark.parametrize("phone", ["abc", "123", "+"])
async def test_an_invalid_phone_is_rejected(api_env, phone: str) -> None:
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(
        conversation,
        context,
        [
            ("text", "Abebe Bekele"),
            ("callback", "eth:gender:pick:0"),
            ("text", "27"),
            ("text", phone),
        ],
    )
    assert state == int(EthiopianTutorStates.PHONE)


@pytest.mark.asyncio
async def test_an_entrance_score_above_its_maximum_is_rejected(api_env) -> None:
    """612/700 is fine; 900/700 must not be storable."""
    conversation = eth.build_conversation()
    context = FakeContext()

    prefix = _COMPLETE_ANSWERS[: _COMPLETE_ANSWERS.index(("text", "612"))]
    state = await drive(conversation, context, prefix)
    assert state == int(EthiopianTutorStates.ENTRANCE_SCORE)

    state = await drive(conversation, context, [("text", "900")], state=state)
    assert state == int(EthiopianTutorStates.ENTRANCE_SCORE)
    assert eth.get_app(context).get("entrance_exam_score") is None


# ---------------------------------------------------------------------------
# optional steps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_voice_intro_can_be_recorded(api_env) -> None:
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS[:12])
    assert state == int(EthiopianTutorStates.ENGLISH_VOICE)

    state = await drive(
        conversation, context, [("callback", "eth:voice:record")], state=state
    )
    state = await drive(conversation, context, [("voice", "")], state=state)
    assert state == int(EthiopianTutorStates.EDUCATION_LEVEL)

    app = eth.get_app(context)
    assert app["english_voice_file_id"] == "voice-file-1"
    assert app["english_voice_message_id"] is not None


@pytest.mark.asyncio
async def test_the_voice_intro_is_optional(api_env) -> None:
    conversation = eth.build_conversation()
    context = FakeContext()

    # Answer 13 is the "continue" that skips the voice introduction.
    state = await drive(conversation, context, _COMPLETE_ANSWERS[:13])
    assert state == int(EthiopianTutorStates.EDUCATION_LEVEL)
    assert eth.get_app(context).get("english_voice_file_id") is None


@pytest.mark.asyncio
async def test_documents_are_required(api_env) -> None:
    """Transcript and entrance exam result are mandatory."""
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS[:_TO_DOCUMENTS])
    assert state == int(EthiopianTutorStates.DOCUMENTS)

    # The first file is stored as the university transcript by default.
    state = await drive(conversation, context, [("document", "")], state=state)
    assert state == int(EthiopianTutorStates.DOCUMENTS)

    documents = eth.get_app(context)["documents"]
    assert len(documents) == 1
    assert documents[0]["document_type"] == str(DocumentType.UNIVERSITY_TRANSCRIPT)
    assert documents[0]["telegram_file_id"] == "doc-file-1"

    # Done while the entrance exam result is still missing is refused.
    state = await drive(
        conversation, context, [("callback", "eth:documents:done")], state=state
    )
    assert state == int(EthiopianTutorStates.DOCUMENTS)

    # Once both required files are up, Done advances to the summary.
    state = await drive(conversation, context, [("document", "")], state=state)
    state = await drive(
        conversation, context, [("callback", "eth:documents:done")], state=state
    )
    assert state == int(EthiopianTutorStates.SUMMARY)


@pytest.mark.asyncio
async def test_a_disallowed_document_type_is_rejected(api_env) -> None:
    """Section 23: PDF and images only."""
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS[:_TO_DOCUMENTS])
    state = await drive(
        conversation,
        context,
        [("document", "")],
        state=state,
    )
    assert eth.get_app(context)["documents"], "a PDF is accepted"


@pytest.mark.asyncio
async def test_the_documents_done_button_advances(api_env) -> None:
    """The button's real callback payload must move the flow on.

    Regression: the keyboard sent the bare ``eth:done`` while the handler only
    matched ``eth:documents:done``, so tapping Done appeared to do nothing.
    """
    from app.bot.keyboards import tutor_onboarding as kb

    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS[:_TO_DOCUMENTS])
    state = await drive(conversation, context, [("document", "")], state=state)
    state = await drive(conversation, context, [("document", "")], state=state)

    # Read the payload off the keyboard the tutor actually sees.
    keyboard = kb.documents_keyboard(
        {"UNIVERSITY_TRANSCRIPT", "UNIVERSITY_ENTRANCE_RESULT"}
    )
    done = next(
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.text and button.text.startswith("✅ Done")
    )
    assert done == "eth:documents:done"

    state = await drive(conversation, context, [("callback", done)], state=state)
    assert state == int(EthiopianTutorStates.SUMMARY)


@pytest.mark.asyncio
async def test_the_bare_done_callback_also_advances(api_env) -> None:
    """Older chats can still hold keyboards built with ``CB_DONE``."""
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS[:_TO_DOCUMENTS])
    state = await drive(conversation, context, [("document", "")], state=state)
    state = await drive(conversation, context, [("document", "")], state=state)
    state = await drive(conversation, context, [("callback", "eth:done")], state=state)

    assert state == int(EthiopianTutorStates.SUMMARY)


@pytest.mark.asyncio
async def test_the_document_picker_labels_the_uploaded_file(api_env) -> None:
    """Tapping a type before sending marks the next file with that type."""
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS[:_TO_DOCUMENTS])
    state = await drive(
        conversation,
        context,
        [("callback", "eth:documents:pick:UNIVERSITY_ENTRANCE_RESULT")],
        state=state,
    )
    assert eth.get_app(context)["_pending_doc_type"] == "UNIVERSITY_ENTRANCE_RESULT"
    assert state == int(EthiopianTutorStates.DOCUMENTS)

    state = await drive(conversation, context, [("document", "")], state=state)
    documents = eth.get_app(context)["documents"]
    assert documents[-1]["document_type"] == "UNIVERSITY_ENTRANCE_RESULT"


@pytest.mark.asyncio
async def test_a_second_application_is_refused_not_overwritten(fresh_db, api_env) -> None:
    """Submitting twice used to hit the unique constraint and show the generic
    failure; the pre-check now reports the existing application instead."""
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS)
    state = await drive(
        conversation, context, [("callback", "eth:submit")], state=state
    )
    assert state == conversation.END

    # A brand new run by the same Telegram user is refused with a friendly note.
    context2 = FakeContext()
    state = await drive(conversation, context2, _COMPLETE_ANSWERS)
    assert state == int(EthiopianTutorStates.SUMMARY)
    state = await drive(
        conversation, context2, [("callback", "eth:submit")], state=state
    )
    assert state == conversation.END
    rendered = "\n".join(context2.bot.sent)
    assert "already have an application" in rendered
    assert "Something went wrong" not in rendered


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_summary_lists_every_collected_field(api_env) -> None:
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS)
    assert state == int(EthiopianTutorStates.SUMMARY)

    rendered = "\n".join(context.bot.sent)
    assert "Application Summary" in rendered
    assert "Abebe Bekele" in rendered
    assert "Ayat Tsebel" in rendered
    assert "Primary school (1-6)" in rendered
    assert "Mathematics" in rendered
    assert "AASTU" in rendered
    assert "9/10" in rendered
    # Options are shown by the label the tutor picked, not the stored enum key,
    # and exam scores are whole numbers rather than the float they are stored as.
    assert "University graduate" in rendered
    assert "UNIVERSITY_GRADUATE" not in rendered
    assert "Natural Sciences" in rendered
    assert "612/700" in rendered
    assert "612.0" not in rendered
    # The secondary results are listed by the label the tutor recognises.
    assert "Grade 8 exam 82" in rendered
    assert "Grade 12 86" in rendered


@pytest.mark.asyncio
async def test_every_step_prompts_with_the_right_counter(api_env) -> None:
    """The counter must cover the whole flow, not the first 16 of 22 steps."""
    numbered = [
        step
        for step in eth.STEPS
        if step.kind != "summary" and "Step" in step.prompt
    ]
    total = len(numbered)

    assert total == 27, "the flow collects more steps than the old /16 claimed"
    for index, step in enumerate(numbered, start=1):
        assert f"Step {index}/{total}" in step.prompt, step.field
        assert f"Step {index}/{total - 1}" not in step.prompt, step.field


@pytest.mark.asyncio
async def test_submitting_notifies_the_admin(api_env, fresh_db) -> None:
    """The admin must actually hear about a new application."""
    session = fresh_db()
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS)
    await drive(conversation, context, [("callback", "eth:submit")], state=state)

    assert any("New tutor application" in message for message in context.bot.sent)


@pytest.mark.asyncio
async def test_submitting_an_incomplete_form_is_refused(api_env, fresh_db) -> None:
    session = fresh_db()
    conversation = eth.build_conversation()
    context = FakeContext()

    # Jump straight to the summary without filling anything in.
    state = await drive(conversation, context, _COMPLETE_ANSWERS)
    eth.get_app(context).clear()
    state = await drive(conversation, context, [("callback", "eth:submit")], state=state)

    assert state == int(EthiopianTutorStates.SUMMARY)
    with session_scope() as check:
        assert tutor_service.get_tutor_by_telegram_id(check, 4242) is None


# ---------------------------------------------------------------------------
# secondary school results (ranking signals)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secondary_results_are_saved(api_env, fresh_db) -> None:
    """The Grade 8 ministry exam and each secondary year are stored."""
    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS)
    state = await drive(
        conversation, context, [("callback", "eth:submit")], state=state
    )
    assert state == conversation.END

    with session_scope() as check:
        tutor = tutor_service.get_tutor_by_telegram_id(check, 4242)
        assert tutor.grade8_exam_average == 82
        assert tutor.grade9_average == 78
        assert tutor.grade10_average == 80
        assert tutor.grade11_average == 84
        assert tutor.grade12_average == 86
        assert P.secondary_result_average(tutor) == 82.0


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["101", "-5", "abc"])
async def test_an_out_of_range_average_is_rejected(api_env, bad: str) -> None:
    conversation = eth.build_conversation()
    context = FakeContext()

    prefix = _COMPLETE_ANSWERS[: _COMPLETE_ANSWERS.index(("text", "82"))]
    state = await drive(conversation, context, prefix)
    assert state == int(EthiopianTutorStates.GRADE8_AVERAGE)

    state = await drive(conversation, context, [("text", bad)], state=state)
    assert state == int(EthiopianTutorStates.GRADE8_AVERAGE)
    assert eth.get_app(context).get("grade8_exam_average") is None


@pytest.mark.asyncio
async def test_every_secondary_result_can_be_skipped(api_env, fresh_db) -> None:
    """All five are optional; skipping them must not block submission."""
    conversation = eth.build_conversation()
    context = FakeContext()

    index = _COMPLETE_ANSWERS.index(("text", "82"))
    end = _COMPLETE_ANSWERS.index(("text", "3"))
    answers = (
        _COMPLETE_ANSWERS[:index]
        + [("callback", "eth:continue")] * (end - index)
        + _COMPLETE_ANSWERS[end:]
    )
    state = await drive(conversation, context, answers)
    state = await drive(
        conversation, context, [("callback", "eth:submit")], state=state
    )
    assert state == conversation.END

    with session_scope() as check:
        tutor = tutor_service.get_tutor_by_telegram_id(check, 4242)
        assert tutor.grade8_exam_average is None
        assert tutor.grade12_average is None
        assert P.secondary_result_average(tutor) is None


@pytest.mark.asyncio
async def test_a_partial_record_still_scores(api_env, fresh_db) -> None:
    """A tutor who sat some exams is ranked on those, not on absence."""
    conversation = eth.build_conversation()
    context = FakeContext()

    index = _COMPLETE_ANSWERS.index(("text", "82"))
    end = _COMPLETE_ANSWERS.index(("text", "3"))
    answers = (
        _COMPLETE_ANSWERS[:index]
        + [("text", "90")]
        + [("callback", "eth:continue")] * (end - index - 1)
        + _COMPLETE_ANSWERS[end:]
    )
    state = await drive(conversation, context, answers)
    await drive(conversation, context, [("callback", "eth:submit")], state=state)

    with session_scope() as check:
        tutor = tutor_service.get_tutor_by_telegram_id(check, 4242)
        assert tutor.grade8_exam_average == 90
        assert tutor.grade9_average is None
        assert P.secondary_result_average(tutor) == 90.0


# ---------------------------------------------------------------------------
# structural guarantees
# ---------------------------------------------------------------------------


def test_the_flow_never_asks_for_a_price() -> None:
    """Section 2: no hourly rate, no currency, no availability."""
    joined = " ".join(
        f"{step.field} {step.prompt}" for step in eth.STEPS
    ).casefold()
    for banned in ("rate", "usd", "etb", "birr", "availability", "payment", "schedule"):
        assert banned not in joined, f"the Ethiopian flow must not ask about {banned}"


def test_every_step_is_in_a_single_ordered_sequence() -> None:
    assert [int(step.state) for step in eth.STEPS] == eth.STATE_ORDER
    assert eth.STEPS[-1].kind == "summary"


def test_the_flow_matches_the_specified_order() -> None:
    fields = [step.field for step in eth.STEPS]
    assert fields == [
        "full_name",
        "gender",
        "age",
        "phone",
        "current_address",
        "locations",
        "grades",
        "subjects",
        "english_proficiency",
        "english_voice",
        "education_level",
        "university",
        "department",
        "university_year",
        "cgpa",
        "entrance_exam_max_score",
        "entrance_exam_score",
        "entrance_exam_year",
        "academic_stream",
        "grade8_exam_average",
        "grade9_average",
        "grade10_average",
        "grade11_average",
        "grade12_average",
        "teaching_experience_years",
        "teaching_experience_description",
        "documents",
        "summary",
    ]


def test_the_states_bands_do_not_overlap_the_other_flows() -> None:
    from app.bot.states import (
        AdminStates,
        JobPostStates,
        StudentStates,
        SupportStates,
        TutorStates,
    )

    bands = [
        {int(item) for item in enum}
        for enum in (
            TutorStates,
            EthiopianTutorStates,
            StudentStates,
            SupportStates,
            AdminStates,
            JobPostStates,
        )
    ]
    for index, left in enumerate(bands):
        for right in bands[index + 1 :]:
            assert not left & right


def test_every_step_has_a_prompt_except_the_summary() -> None:
    for step in eth.STEPS:
        if step.kind != "summary":
            assert step.prompt.strip(), step.field


def test_all_callback_data_fits_the_telegram_limit() -> None:
    from app.bot.keyboards import tutor_onboarding as kb

    keyboards = [
        kb.nav_keyboard(),
        kb.skip_keyboard(),
        kb.voice_keyboard(),
        kb.summary_keyboard(),
        kb.english_level_keyboard(),
        kb.choice_keyboard("gender", ["Male", "Female", "Other"]),
        kb.choice_keyboard("academic_stream", ["Natural Sciences", "Social Sciences"]),
        kb.documents_keyboard(["UNIVERSITY_TRANSCRIPT"], "UNIVERSITY_ENTRANCE_RESULT"),
        kb.multiselect_keyboard("grades", ["Grade 4", "Grade 5"], ["Grade 4"]),
    ]
    for keyboard in keyboards:
        for row in keyboard.inline_keyboard:
            for button in row:
                if button.callback_data is None:
                    continue
                assert len(button.callback_data.encode("utf-8")) <= 64, button.callback_data


def test_the_conversation_is_registered_in_the_application(api_env) -> None:
    from app.bot import build_application

    names = {getattr(handler, "name", None) for handler in build_application().handlers[0]}
    assert "ethiopian_tutor" in names
    # The legacy flow must remain registered alongside it.
    assert "tutor_application" in names


def test_the_legacy_tutor_flow_is_untouched(api_env) -> None:
    from telegram.ext import CallbackQueryHandler

    from app.bot.handlers import tutor as legacy
    from app.bot.states import TutorStates

    assert len(TutorStates) == 26
    assert len(legacy.STEP_SEQUENCE) == 26
    # Its own entry point is unchanged (patterns are compiled regexes).
    patterns = [
        handler.pattern.pattern
        for handler in legacy.build_tutor_conversation().entry_points
        if isinstance(handler, CallbackQueryHandler)
    ]
    assert "^menu:become_tutor$" in patterns


# ---------------------------------------------------------------------------
# enrichment (section 26)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_profile_lists_what_is_missing(api_env, fresh_db) -> None:
    from tests.factories import tutor_payload

    session = fresh_db()
    tutor_service.create_tutor(session, tutor_payload(4242))

    conversation = eth.build_conversation()
    context = FakeContext()
    state = await drive(conversation, context, [("command", "complete_profile")], start=False)

    # The command starts a run on the first outstanding step rather than
    # ending. This imported tutor has no gender on file either, and the plan
    # puts genuinely unknown personal details ahead of the section-26 fields.
    assert state == int(EthiopianTutorStates.GENDER)
    rendered = "\n".join(context.bot.sent)
    assert "Profile completion" in rendered
    assert "Tutoring areas" in rendered


@pytest.mark.asyncio
async def test_complete_profile_is_a_noop_when_complete(api_env, fresh_db) -> None:
    from tests.factories import tutor_payload

    session = fresh_db()
    tutor_service.create_tutor(session, tutor_payload(4242))
    tutor = tutor_service.get_tutor_by_telegram_id(session, 4242)
    P.set_locations(session, tutor, ["Bole"])
    P.set_grades(session, tutor, ["Grade 4"])
    P.set_subjects(session, tutor, ["Mathematics"])
    P.set_english_proficiency(session, tutor, 8)
    P.add_experience(session, tutor, {"years": 2})
    P.update_profile_fields(
        session,
        tutor,
        {
            "gender": "Male",
            "age": 26,
            "current_address": "Bole, Addis Ababa",
            "university": "AAU",
            "education_level": "UNIVERSITY_GRADUATE",
            "entrance_exam_max_score": 840,
            "entrance_exam_score": 500,
            "entrance_exam_year": 2016,
            "academic_stream": "NATURAL",
            "grade8_exam_average": 82,
            "grade9_average": 78,
            "grade10_average": 80,
            "grade11_average": 84,
            "grade12_average": 86,
        },
    )
    P.record_document(session, tutor, "UNIVERSITY_TRANSCRIPT", "f", file_name="t.pdf")
    session.commit()

    assert P.missing_profile_fields(tutor) == [], P.missing_profile_labels(tutor)

    conversation = eth.build_conversation()
    context = FakeContext()
    state = await drive(conversation, context, [("command", "complete_profile")], start=False)
    assert state == conversation.END
    assert "complete" in "\n".join(context.bot.sent).casefold()


@pytest.mark.asyncio
async def test_the_global_fallback_stays_quiet_during_onboarding(api_env) -> None:
    """Answering a question must not trigger "I did not recognise that".

    The fallback is a group-3 handler that silences itself while a flow is
    active. Onboarding had to register that flow marker itself, otherwise every
    answer was followed by a menu prompt.
    """
    from app.bot.handlers.common import active_flow
    from app.bot.handlers.start import unknown_message

    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS[:3])
    assert state != conversation.END
    assert active_flow(context) == "tutor", active_flow(context)

    # Whatever the tutor types mid-flow, the fallback must not answer.
    await unknown_message(text_update("anything at all", bot=context.bot), context)
    assert "did not recognise" not in "\n".join(context.bot.sent).casefold()

    # Once the run is over the fallback speaks again.
    await drive(conversation, context, [("callback", "eth:cancel")], state=state)
    assert active_flow(context) is None
    await unknown_message(text_update("hello?", bot=context.bot), context)
    assert "did not recognise" in "\n".join(context.bot.sent).casefold()


@pytest.mark.asyncio
async def test_submitting_clears_the_flow_marker(api_env, fresh_db) -> None:
    """The marker must not outlive the run, or the menu hint stays dead."""
    from app.bot.handlers.common import active_flow

    conversation = eth.build_conversation()
    context = FakeContext()

    state = await drive(conversation, context, _COMPLETE_ANSWERS)
    assert active_flow(context) == "tutor"
    await drive(conversation, context, [("callback", "eth:submit")], state=state)

    assert active_flow(context) is None
