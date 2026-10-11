"""End-to-end conversation dispatch tests.

The handlers are driven through a faithful re-implementation of the
``ConversationHandler`` dispatch loop (first matching handler per state, the
update is re-dispatched to the next state) so every step of the tutor and
student flows is exercised without contacting Telegram.
"""

from __future__ import annotations

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from telegram import CallbackQuery, Chat, Document, Message, PhotoSize, Update, User

from app.bot.handlers import student as student_handler
from app.bot.handlers import tutor as tutor_handler
from app.bot.handlers.common import APP_KEY, FLOW_KEY
from app.bot.states import StudentStates, TutorStates
from app.database import session_scope
from app.enums import DocumentType, TutorStatus
from app.services import tutor_service

MAX_DISPATCH = 60


# ---------------------------------------------------------------------------
# fakes (real telegram objects + a stub bot)
# ---------------------------------------------------------------------------


def _user(user_id: int = 4242) -> User:
    return User(id=user_id, first_name="Test", last_name="User", username="tutor1", is_bot=False)


class FakeBot:
    """Stub bot: records everything the handlers try to send."""

    defaults = None
    # ``CommandHandler.check_update`` reads the bot's username.
    username = "tedor_tutors_test_bot"

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edited: list[str] = []
        self.answers: list[str] = []
        self.documents: list[str] = []
        self.photos: list[str] = []
        self.id = 1
        self._next = 900

    def _message(self, text: str) -> Message:
        return Message(
            message_id=self._bump(),
            date=datetime.now(tz=timezone.utc),
            chat=Chat(id=1, type=Chat.PRIVATE),
            text=text,
        )

    def _bump(self) -> int:
        self._next += 1
        return self._next

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> Message:
        self.sent.append(text)
        return self._message(text)

    async def edit_message_text(self, text: str = "", **kwargs: Any) -> Message:
        self.edited.append(text)
        return self._message(text)

    async def edit_message_reply_markup(self, **kwargs: Any) -> bool:
        return True

    async def answer_callback_query(self, text: str = "", **kwargs: Any) -> bool:
        self.answers.append(text)
        return True

    async def send_document(self, chat_id: int, document: Any, **kwargs: Any) -> Message:
        self.documents.append(str(document))
        return Message(
            message_id=self._bump(),
            date=datetime.now(tz=timezone.utc),
            chat=Chat(id=chat_id, type=Chat.PRIVATE),
            document=Document(file_id=f"stored-{document}", file_unique_id="du"),
        )

    async def send_photo(self, chat_id: int, photo: Any, **kwargs: Any) -> Message:
        self.photos.append(str(photo))
        return Message(
            message_id=self._bump(),
            date=datetime.now(tz=timezone.utc),
            chat=Chat(id=chat_id, type=Chat.PRIVATE),
            photo=[
                PhotoSize(
                    file_id=f"stored-{photo}",
                    file_unique_id="pu",
                    width=100,
                    height=100,
                )
            ],
        )


class FakeContext:
    def __init__(self, bot: FakeBot | None = None) -> None:
        self.user_data: dict[str, Any] = {}
        self.bot = bot or FakeBot()
        self.chat_data: dict[str, Any] = {}
        self.args: list[Any] = []


def _message(user_id: int, **kwargs: Any) -> Message:
    message = Message(
        message_id=1,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=user_id, type=Chat.PRIVATE),
        from_user=_user(user_id),
        **kwargs,
    )
    return message


def text_update(value: str, user_id: int = 4242, bot: FakeBot | None = None) -> Update:
    message = _message(user_id, text=value)
    message.set_bot(bot or FakeBot())
    return Update(update_id=1, message=message)


def photo_update(user_id: int = 4242, bot: FakeBot | None = None) -> Update:
    message = _message(
        user_id,
        photo=[
            PhotoSize(file_id="photo-file", file_unique_id="pu", width=100, height=100)
        ],
    )
    message.set_bot(bot or FakeBot())
    return Update(update_id=1, message=message)


def document_update(
    user_id: int = 4242, file_name: str = "cv.pdf", bot: FakeBot | None = None
) -> Update:
    message = _message(
        user_id,
        document=Document(
            file_id="doc-file", file_unique_id="du", file_name=file_name
        ),
    )
    message.set_bot(bot or FakeBot())
    return Update(update_id=1, message=message)


def callback_update(
    data: str, user_id: int = 4242, bot: FakeBot | None = None
) -> Update:
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


# ---------------------------------------------------------------------------
# dispatch emulator
# ---------------------------------------------------------------------------


def _handler_for(handlers, update: Update):
    """Mirror ``ConversationHandler.check_update``: skip None/False results."""
    for handler in handlers:
        check = handler.check_update(update)
        if check is not None and check is not False:
            return handler
    return None


async def dispatch(conversation, update: Update, context: FakeContext, state: int | None):
    """Run one update through the conversation.

    ``python-telegram-bot`` v20+ selects exactly one handler per update, so the
    emulator does the same: entry points when there is no state (or when re-entry
    is allowed), then the current state's handlers, then the fallbacks.
    """
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
# tutor application flow
# ---------------------------------------------------------------------------


TEXT_KINDS = {"text", "name", "phone", "email", "int", "float", "year"}

TEXT_ANSWERS = {
    "full_name": "Abebe Bekele",
    "display_name": "Abebe B.",
    "phone": "+251911234567",
    "email": "abebe@example.com",
    "city": "Addis Ababa",
    "experience_years": "5",
    "bio": "I teach mathematics and physics with care.",
    "edu_institution": "Addis Ababa University",
    "edu_degree": "BSc",
    "edu_field": "Mathematics",
    "edu_year": "2016",
    "availability_times": "09:00-12:00, 16:00-18:00",
    "etb_rate": "350",
    "usd_rate": "5",
}

CHOICE_ANSWERS = {
    "country": "Ethiopia",
    "teaching_mode": "💻 Online",
    "timezone": "EAT (UTC+3)",
}

MULTI_ANSWERS = {
    "subjects": "Mathematics",
    "levels": "Grade 9-10",
    "languages": "Amharic",
    "availability_days": "Monday",
}


@pytest.mark.asyncio
async def test_full_tutor_application_flow(api_env, fresh_db) -> None:
    """Drive all 26 steps and submit the application."""
    session = fresh_db()
    conversation = tutor_handler.build_tutor_conversation()
    context = FakeContext()

    state = await dispatch(conversation, callback_update("menu:become_tutor"), context, None)
    assert state == int(TutorStates.FULL_NAME)

    for step in tutor_handler.STEP_SEQUENCE:
        state_value = int(step.state)
        if state != state_value:
            continue

        if step.field == "summary":
            state = await dispatch(
                conversation, callback_update("tutor:submit"), context, state
            )
            break

        if step.kind in TEXT_KINDS:
            update = text_update(TEXT_ANSWERS[step.field])
        elif step.kind == "choice":
            raw = list(step.options)
            options = [item[1] if isinstance(item, tuple) else item for item in raw]
            index = options.index(CHOICE_ANSWERS[step.field])
            update = callback_update(f"tutor:{step.field}:pick:{index}")
        elif step.kind == "multiselect":
            options = list(step.options)
            index = options.index(MULTI_ANSWERS[step.field])
            update = callback_update(f"tutor:{step.field}:toggle:{index}")
        elif step.kind == "photo":
            update = photo_update()
        elif step.kind == "document":
            update = document_update()
        else:  # pragma: no cover - defensive
            raise AssertionError(f"unhandled step kind {step.kind}")

        state = await dispatch(conversation, update, context, state)
        if step.kind == "multiselect":
            # multiselect steps need an explicit confirmation
            state = await dispatch(
                conversation, callback_update(f"tutor:{step.field}:done"), context, state
            )
        if step.state == int(TutorStates.DOC_EXTRA):
            # optional documents stay on the step until the user continues
            state = await dispatch(
                conversation, callback_update("tutor:continue"), context, state
            )

    assert state == conversation.END

    with session_scope() as check:
        tutor = tutor_service.get_tutor_by_telegram_id(check, 4242)
        assert tutor is not None
        assert tutor.public_tutor_id.startswith("TDR-")
        assert tutor.status == TutorStatus.PENDING
        assert tutor.subject_list == ["Mathematics"]
        assert tutor.level_list == ["Grade 9-10"]
        assert tutor.etb_rate is not None, "Ethiopian tutors must have an ETB rate"
        assert {doc.document_type for doc in tutor.documents} >= {
            str(DocumentType.CV),
            str(DocumentType.DEGREE),
            str(DocumentType.PROFILE_PHOTO),
        }
        assert tutor.storage_status == "STORED"
    session.rollback()


@pytest.mark.asyncio
async def test_back_button_preserves_entered_data(api_env, fresh_db) -> None:
    conversation = tutor_handler.build_tutor_conversation()
    context = FakeContext()

    state = await dispatch(conversation, callback_update("menu:become_tutor"), context, None)
    state = await dispatch(conversation, text_update("Abebe Bekele"), context, state)
    assert state == int(TutorStates.DISPLAY_NAME)

    state = await dispatch(conversation, callback_update("tutor:back"), context, state)
    assert state == int(TutorStates.FULL_NAME)
    assert tutor_handler.get_application(context)["full_name"] == "Abebe Bekele"

    state = await dispatch(conversation, callback_update("tutor:continue"), context, state)
    assert state == int(TutorStates.DISPLAY_NAME)


@pytest.mark.asyncio
async def test_invalid_input_keeps_the_user_on_the_step(api_env) -> None:
    conversation = tutor_handler.build_tutor_conversation()
    context = FakeContext()
    state = await dispatch(
        conversation, callback_update("menu:become_tutor", bot=context.bot), context, None
    )
    state = await dispatch(
        conversation,
        callback_update("tutor:editfield:email", bot=context.bot),
        context,
        state,
    )
    assert state == int(TutorStates.EMAIL)

    for _ in range(3):
        state = await dispatch(
            conversation, text_update("not-an-email", bot=context.bot), context, state
        )
        assert state == int(TutorStates.EMAIL)
    assert "email" not in tutor_handler.get_application(context)
    assert context.bot.sent[-1].startswith("\u26a0")


@pytest.mark.asyncio
async def test_edit_menu_jumps_to_the_chosen_field(api_env) -> None:
    conversation = tutor_handler.build_tutor_conversation()
    context = FakeContext()
    state = await dispatch(
        conversation, callback_update("menu:become_tutor", bot=context.bot), context, None
    )
    state = await dispatch(
        conversation, text_update("Abebe Bekele", bot=context.bot), context, state
    )
    assert tutor_handler.get_application(context)["full_name"] == "Abebe Bekele"

    # the edit menu lists every field and jumps to the chosen one
    state = await dispatch(
        conversation, callback_update("tutor:edit", bot=context.bot), context, state
    )
    assert state == int(TutorStates.DISPLAY_NAME)

    state = await dispatch(
        conversation,
        callback_update("tutor:editfield:full_name", bot=context.bot),
        context,
        state,
    )
    assert state == int(TutorStates.FULL_NAME)
    assert "full_name" not in tutor_handler.get_application(context)

    state = await dispatch(
        conversation, text_update("Abebe New Name", bot=context.bot), context, state
    )
    assert state == int(TutorStates.DISPLAY_NAME)
    assert tutor_handler.get_application(context)["full_name"] == "Abebe New Name"


@pytest.mark.asyncio
async def test_cancel_clears_the_conversation(api_env) -> None:
    conversation = tutor_handler.build_tutor_conversation()
    context = FakeContext()
    state = await dispatch(conversation, callback_update("menu:become_tutor"), context, None)
    state = await dispatch(conversation, callback_update("tutor:cancel"), context, state)
    assert state == conversation.END
    assert APP_KEY not in context.user_data
    assert FLOW_KEY not in context.user_data


@pytest.mark.asyncio
async def test_duplicate_submission_is_rejected(api_env, fresh_db) -> None:
    """Requirement: a second application from the same Telegram user is refused."""
    session = fresh_db()
    with session_scope() as create:
        tutor_service.create_tutor(
            create,
            {
                "telegram_user_id": 4242,
                "name": "Existing",
                "display_name": "Existing",
                "phone": "+251900000000",
                "email": "existing@example.com",
                "country": "Ethiopia",
                "city": "Addis Ababa",
                "teaching_mode": "ONLINE",
            },
        )

    context = FakeContext()
    tutor_handler.get_application(context).update(
        {
            "full_name": "Existing",
            "display_name": "Existing",
            "phone": "+251900000000",
            "email": "existing@example.com",
            "country": "Ethiopia",
            "city": "Addis Ababa",
            "subjects": ["Mathematics"],
            "levels": ["Grade 9-10"],
            "teaching_mode": "ONLINE",
            "languages": ["English"],
            "experience_years": 3,
            "bio": "Bio text",
            "edu_institution": "AAU",
            "edu_degree": "BSc",
            "edu_field": "Maths",
            "edu_year": 2016,
            "availability_days": ["Monday"],
            "availability_times": "09:00-12:00",
            "timezone": "EAT (UTC+3)",
            "etb_rate": 300.0,
            "documents": [
                {"document_type": str(DocumentType.CV), "telegram_file_id": "f"},
                {"document_type": str(DocumentType.DEGREE), "telegram_file_id": "f"},
            ],
        }
    )
    state = await tutor_handler.submit_application(
        callback_update("tutor:submit", bot=context.bot), context
    )
    assert state == tutor_handler.ConversationHandler.END
    assert any("already have an application" in text for text in context.bot.sent)
    session.rollback()


# ---------------------------------------------------------------------------
# student request flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_student_request_flow_matches_tutors(api_env, fresh_db) -> None:
    from tests.factories import create_tutor

    session = fresh_db()
    create_tutor(
        session,
        -8001,
        status=TutorStatus.VERIFIED,
        subjects=["Mathematics"],
        levels=["Grade 9-10"],
        languages=["English"],
        teaching_mode="ONLINE",
        country="Ethiopia",
        etb_rate=200.0,
    )
    session.commit()

    conversation = student_handler.build_student_conversation()
    context = FakeContext()
    state = await dispatch(conversation, callback_update("menu:find_tutor"), context, None)
    assert state == int(StudentStates.PARENT_NAME)

    answers = {
        "parent_name": "Parent Name",
        "student_name": "Student Name",
        "subject": "Mathematics",
        "preferred_language": "English",
        "budget": "500",
        "schedule": "Weekdays 16:00-18:00",
        "additional_requirements": "-",
        "contact": "+251911000000",
    }
    choices = {
        "country": "Ethiopia",
        "education_level": "Grade 9-10",
        "mode": "💻 Online",
    }

    for step in student_handler.STEPS:
        if step.field == "summary":
            state = await dispatch(conversation, callback_update("student:submit"), context, state)
            break
        if step.kind == "choice":
            raw = list(step.options)
            options = [item[1] if isinstance(item, tuple) else item for item in raw]
            update = callback_update(
                f"student:{step.field}:pick:{options.index(choices[step.field])}"
            )
        else:
            update = text_update(answers[step.field])
        state = await dispatch(conversation, update, context, state)

    assert state == int(StudentStates.RESULTS)
    assert context.user_data["last_request_id"].startswith("REQ-")

    request_id = context.user_data["last_request_id"]
    view = callback_update(f"stu:view:TDR-000001:{request_id}")
    state = await student_handler.callback_handler(view, context)
    assert state == int(StudentStates.TUTOR_DETAIL)

    select = callback_update(f"stu:select:{request_id}:TDR-000001")
    state = await student_handler.callback_handler(select, context)
    assert state == int(StudentStates.RESULTS)

    with session_scope() as check:
        from app.services import student_service

        stored = student_service.get_request_by_id(check, request_id)
        assert stored is not None
        assert stored.matched_tutor_id == "TDR-000001"
        assert stored.etb_budget == 500
    session.rollback()


@pytest.mark.asyncio
async def test_student_no_match_offers_support(api_env, fresh_db) -> None:
    from tests.factories import create_tutor

    session = fresh_db()
    create_tutor(session, -8100, status=TutorStatus.VERIFIED, subjects=["Chemistry"])
    session.commit()

    conversation = student_handler.build_student_conversation()
    context = FakeContext()
    state = await dispatch(conversation, callback_update("menu:find_tutor"), context, None)
    state = await student_handler.callback_handler(
        callback_update("student:cancel"), context
    )
    assert state == conversation.END

    payload = {
        "parent_name": "Parent",
        "student_name": "Student",
        "country": "Ethiopia",
        "education_level": "Grade 9-10",
        "subject": "Mathematics",
        "preferred_language": "English",
        "mode": "ONLINE",
        "budget": 100,
        "schedule": "Mornings",
        "additional_requirements": "",
        "contact": "+251911000000",
    }
    student_handler.get_request(context).update(payload)
    state = await student_handler.submit_request(text_update("submit"), context)
    assert state == int(StudentStates.RESULTS)
    session.rollback()