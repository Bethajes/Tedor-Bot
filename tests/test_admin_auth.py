"""Admin authorization (Property 11 for the Telegram side).

Feature: tedor-telegram-bot, Property 11: Admin authorization
Validates: Requirements 5.6, 9.1
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings as hypothesis_settings, strategies as st

from app.bot.handlers import admin as admin_handler
from app.services import notification_service, tutor_service
from tests.factories import create_tutor

PROFILE = hypothesis_settings(max_examples=100, deadline=None)

ADMIN_IDS = [111, 222]


@pytest.fixture(autouse=True)
def _admin_config(api_env):
    return api_env


def _update(user_id: int | None):
    message = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message,
        callback_query=None,
        _message=message,
    )


@given(candidate=st.integers(min_value=-10**9, max_value=10**9))
@PROFILE
def test_property_unauthorised_ids_are_denied(candidate: int) -> None:
    """Feature: tedor-telegram-bot, Property 11: Admin authorization.

    Every Telegram ID outside the configured admin list must be denied.
    """
    authorised = candidate in ADMIN_IDS
    assert admin_handler.is_admin(candidate) is authorised


@given(candidate=st.integers(min_value=-10**9, max_value=10**9))
@PROFILE
def test_property_require_admin_denies_unknown_users(candidate: int) -> None:
    update = _update(candidate)
    allowed = asyncio.run(admin_handler.require_admin(update))
    assert allowed is (candidate in ADMIN_IDS)
    if not allowed:
        update._message.reply_text.assert_awaited_once()
        assert "denied" in update._message.reply_text.await_args[0][0].lower()


def test_none_user_is_never_admin() -> None:
    assert admin_handler.is_admin(None) is False


@pytest.mark.asyncio
async def test_status_change_handler_denies_non_admin(fresh_db) -> None:
    """Requirement 5.6: only admins may change a tutor status."""
    session = fresh_db()
    tutor = create_tutor(session, -6001)
    session.commit()
    tdr_id = tutor.public_tutor_id

    update = _update(999999)
    state = await admin_handler.callback_handler(update, SimpleNamespace(user_data={}))
    assert state == admin_handler.ConversationHandler.END
    session.expire_all()
    assert tutor_service.get_tutor_by_tdr_id(session, tdr_id).status == "PENDING"


@pytest.mark.asyncio
async def test_verification_notifies_the_tutor(fresh_db) -> None:
    """Requirements 5.2 and 7.1 (admin → tutor notification)."""
    session = fresh_db()
    tutor = create_tutor(session, -6100)
    session.commit()
    tdr_id = tutor.public_tutor_id

    sent: list[tuple[int, str]] = []

    class Bot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))
            return SimpleNamespace(message_id=1)

    context = SimpleNamespace(user_data={}, bot=Bot())
    update = _update(111)
    await admin_handler._set_status(update, context, tdr_id, "VERIFIED")

    session.expire_all()
    stored = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
    assert stored.status == "VERIFIED"
    assert any(chat_id == tutor.telegram_user_id for chat_id, _ in sent)
    assert any(tdr_id in text for _chat, text in sent)


def test_statistics_text_lists_every_counter(fresh_db) -> None:
    session = fresh_db()
    create_tutor(session, -6200)
    create_tutor(session, -6201, status="VERIFIED")
    session.commit()
    text = admin_handler.statistics_text()
    for label in (
        "Total tutors",
        "Pending",
        "Verified",
        "Rejected",
        "Suspended",
        "Student requests",
        "Support tickets",
    ):
        assert label in text


def test_notification_helpers_never_raise_without_a_bot(fresh_db) -> None:
    session = fresh_db()
    tutor = create_tutor(session, -6300)
    session.commit()

    assert asyncio.run(notification_service.notify_admin_new_tutor(None, tutor)) is False
    assert asyncio.run(notification_service.notify_tutor_verified(None, tutor)) is False
    assert asyncio.run(notification_service.notify_tutor_rejected(None, tutor, "x")) is False
    assert asyncio.run(notification_service.request_more_info(None, tutor, "hi")) is False