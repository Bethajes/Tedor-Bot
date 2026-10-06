"""Student request contact-detail regression tests.

The Telegram flow collects the last step under the key ``contact``, while the
service stores ``phone`` / ``contact_handle``. These tests pin the mapping so a
parent's contact details can never be silently dropped again.
"""

from __future__ import annotations

import pytest

from app.bot.handlers import student
from app.database import session_scope
from app.services import student_service
from tests.factories import student_request_payload


def _stored(payload: dict):
    with session_scope() as session:
        return student_service.create_student_request(session, payload)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entered", "phone", "handle"),
    [
        ("+251911234567", "+251911234567", ""),
        ("0911234567", "0911234567", ""),
        ("@abebe_bekele", "", "@abebe_bekele"),
        ("abebe_bekele", "", "abebe_bekele"),
        ("+251911234567 @abebe_bekele", "+251911234567", "@abebe_bekele"),
        ("@abebe_bekele +251911234567", "+251911234567", "@abebe_bekele"),
        ("  +251911234567   @abebe_bekele  ", "+251911234567", "@abebe_bekele"),
        ("+251911234567, @abebe_bekele", "+251911234567", "@abebe_bekele"),
        ("", "", ""),
        (None, "", ""),
    ],
)
def test_contact_fields_splits_phone_and_handle(
    entered: str | None, phone: str, handle: str
) -> None:
    assert student_service.contact_fields(entered) == (phone, handle)


def test_explicit_keys_win_over_the_free_text_key() -> None:
    phone, handle = student_service.resolve_contact(
        {"phone": "+251900000000", "contact_handle": "@parent", "contact": "0911000000"}
    )
    assert (phone, handle) == ("+251900000000", "@parent")


def test_resolve_contact_fills_in_only_the_missing_side() -> None:
    assert student_service.resolve_contact({"contact": "@abebe"}) == ("", "@abebe")
    assert student_service.resolve_contact({"contact": "0911234567"}) == ("0911234567", "")


# ---------------------------------------------------------------------------
# the flow's own key is honoured
# ---------------------------------------------------------------------------


def test_the_flow_field_name_is_contact() -> None:
    """Guards the mismatch that caused the bug."""
    step = next(
        item for item in student.STEPS if item.state == student.StudentStates.CONTACT
    )
    assert step.field == "contact"


def test_contact_from_the_flow_is_stored_as_a_phone(fresh_db) -> None:
    session = fresh_db()
    request = _stored(
        student_request_payload(-4201, phone="", contact_handle="", contact="+251911234567")
    )
    assert request.phone == "+251911234567"
    assert request.contact_handle == ""
    assert student_service.contact_display(request) == "+251911234567"
    session.rollback()


def test_contact_from_the_flow_is_stored_as_a_handle(fresh_db) -> None:
    session = fresh_db()
    request = _stored(
        student_request_payload(-4202, phone="", contact_handle="", contact="@abebe_bekele")
    )
    assert request.contact_handle == "@abebe_bekele"
    assert request.phone == ""
    assert student_service.contact_display(request) == "@abebe_bekele"
    session.rollback()


def test_both_details_from_one_flow_answer_are_kept(fresh_db) -> None:
    session = fresh_db()
    request = _stored(
        student_request_payload(
            -4203, phone="", contact_handle="", contact="+251911234567 @abebe_bekele"
        )
    )
    assert request.phone == "+251911234567"
    assert request.contact_handle == "@abebe_bekele"
    assert student_service.contact_display(request) == "@abebe_bekele / +251911234567"
    session.rollback()


def test_contact_survives_the_archived_record(fresh_db) -> None:
    """The bug was visible in the archived Telegram record."""
    session = fresh_db()
    request = _stored(
        student_request_payload(
            -4204,
            phone="",
            contact_handle="",
            contact="+251911234567",
            parent_name="Asnake",
            student_name="9th",
        )
    )
    record = student_service.format_request_summary(request)
    assert "Contact: +251911234567" in record
    assert "Contact: —" not in record
    session.rollback()


def test_missing_contact_still_renders_an_em_dash(fresh_db) -> None:
    session = fresh_db()
    request = _stored(
        student_request_payload(-4205, phone="", contact_handle="", contact="")
    )
    assert student_service.contact_display(request) == "—"
    assert "Contact: —" in student_service.format_request_summary(request)
    session.rollback()


def test_legacy_explicit_keys_are_unchanged(fresh_db) -> None:
    session = fresh_db()
    request = _stored(student_request_payload(-4206))
    assert request.phone == "+251911000000"
    assert request.contact_handle == "@parent"
    assert student_service.contact_display(request) == "@parent / +251911000000"
    session.rollback()


# ---------------------------------------------------------------------------
# the contact step cannot be silently skipped
# ---------------------------------------------------------------------------


def _labels(keyboard) -> list[str]:
    return [button.text for row in keyboard.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_continue_cannot_walk_past_an_unanswered_contact_step(api_env) -> None:
    """Regression: ✅ Continue used to skip straight to the summary."""
    from app.bot.handlers.common import FLOW_KEY
    from tests.test_conversation_flow import FakeBot, FakeContext, callback_update, dispatch

    conversation = student.build_student_conversation()
    context = FakeContext()
    bot = context.bot
    state = await dispatch(
        conversation, callback_update("menu:find_tutor", bot=bot), context, None
    )

    answers = {
        "parent_name": "Asnake",
        "student_name": "9th",
        "subject": "math,physics",
        "preferred_language": "english",
        "budget": "400",
        "schedule": "Weekdays 16:00-18:00",
        "additional_requirements": "-",
    }
    choices = {
        "country": "Ethiopia",
        "education_level": "Grade 7-8",
        "mode": "🏠 In person",
    }

    for step in student.STEPS:
        if step.field in {"summary", "contact"}:
            break
        if state != int(step.state):
            continue
        if step.kind == "choice":
            raw = list(step.options)
            labels = [item[1] if isinstance(item, tuple) else item for item in raw]
            state = await dispatch(
                conversation,
                callback_update(
                    f"student:{step.field}:pick:{labels.index(choices[step.field])}",
                    bot=bot,
                ),
                context,
                state,
            )
        else:
            state = await dispatch(
                conversation, _text(answers[step.field], bot), context, state
            )

    # now sitting on the contact step with nothing typed
    assert state == int(student.StudentStates.CONTACT)
    assert "contact" not in context.user_data["student_request"]

    # pressing Continue is refused
    state = await dispatch(
        conversation, callback_update("student:continue", bot=bot), context, state
    )
    assert state == int(student.StudentStates.CONTACT)
    assert any("before continuing" in text for text in bot.sent)
    assert "contact" not in context.user_data["student_request"]

    # typing an answer does move on
    state = await dispatch(
        conversation, _text("+251911234567", bot), context, state
    )
    assert state == int(student.StudentStates.SUMMARY)
    assert context.user_data["student_request"]["contact"] == "+251911234567"
    assert FLOW_KEY in context.user_data


@pytest.mark.asyncio
async def test_unanswered_required_step_hides_the_continue_button(api_env) -> None:
    context = type("Ctx", (), {"user_data": {}})()
    step = next(s for s in student.STEPS if s.field == "contact")
    assert "✅ Continue" not in _labels(student._keyboard_for(step, {}))
    assert "✅ Continue" in _labels(student._keyboard_for(step, {"contact": "+251"}))
    # explicitly skippable steps always offer it
    optional = next(s for s in student.STEPS if s.field == "budget")
    assert "✅ Continue" in _labels(student._keyboard_for(optional, {}))


def _text(value: str, bot):
    from datetime import datetime, timezone

    from telegram import Chat, Message, Update, User

    message = Message(
        message_id=1,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=4242, type=Chat.PRIVATE),
        from_user=User(id=4242, first_name="T", is_bot=False),
        text=value,
    )
    message.set_bot(bot)
    return Update(update_id=1, message=message)