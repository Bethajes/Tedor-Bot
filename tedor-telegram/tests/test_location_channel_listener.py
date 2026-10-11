"""The live channel listener.

The Bot API has no ``getChatHistory``, so the listener can only see posts that
arrive as ``channel_post`` / ``edited_channel_post`` updates — everything older
must come through the export backfill. These tests use mocked updates and a
mocked bot: nothing here reaches Telegram, and no test needs a token.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.bot.handlers import location_channel
from app.models import Location

CHAT = -1001234567890
OTHER_CHAT = -1009999999999

RECORD = (
    "ID: AA-0001\n"
    "Name: Arada Center\n"
    "Aliases: Arada, Piazza\n"
    "Latitude: 9.0200\n"
    "Longitude: 38.7500\n"
    "Status: verified\n"
)


class FakeBot:
    """Records what the bot was asked to send."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text})
        return SimpleNamespace(message_id=1)


def channel_update(text: str, *, chat_id: int = CHAT, message_id: int = 1, edited: bool = False):
    message = SimpleNamespace(
        message_id=message_id,
        text=text,
        chat=SimpleNamespace(id=chat_id),
    )
    key = "edited_channel_post" if edited else "channel_post"
    update = SimpleNamespace(effective_user=None, callback_query=None)
    setattr(update, key, message)
    return update


@pytest.fixture(autouse=True)
def _channel_configured(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "location_channel_id", CHAT)
    monkeypatch.setattr(settings, "admin_user_ids", [999])
    monkeypatch.setattr(settings, "location_channel_notify_rejections", True)
    return settings


@pytest.fixture
def context():
    return SimpleNamespace(bot=FakeBot())


def fetch(db, code: str) -> Location:
    return db.query(Location).filter(Location.location_code == code).one()


# ---------------------------------------------------------------------------
# handling a new post
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_channel_post_is_imported(db, context):
    await location_channel.handle_channel_post(
        channel_update(RECORD, message_id=42), context
    )
    stored = fetch(db, "AA-0001")
    assert stored.name == "Arada Center"
    assert stored.source_chat_id == CHAT
    assert stored.source_message_id == 42


@pytest.mark.asyncio
async def test_an_edited_post_updates_the_same_row(db, context):
    await location_channel.handle_channel_post(
        channel_update(RECORD, message_id=42), context
    )
    edited = RECORD.replace("9.0200", "9.0300")
    await location_channel.handle_edited_channel_post(
        channel_update(edited, message_id=42, edited=True), context
    )
    assert db.query(Location).filter(Location.location_code == "AA-0001").count() == 1
    assert fetch(db, "AA-0001").latitude == pytest.approx(9.03)


@pytest.mark.asyncio
async def test_a_post_from_another_channel_is_ignored(db, context):
    """The bot is an administrator of more than one channel; only one is data."""
    await location_channel.handle_channel_post(
        channel_update(RECORD, chat_id=OTHER_CHAT, message_id=5), context
    )
    assert db.query(Location).filter(Location.location_code == "AA-0001").count() == 0


@pytest.mark.asyncio
async def test_an_unparseable_post_notifies_an_administrator(db, context):
    await location_channel.handle_channel_post(
        channel_update("just a chat message, not a record", message_id=9), context
    )
    assert db.query(Location).count() == 0
    assert context.bot.sent, "the administrator should be told a record was rejected"
    assert "ID" in context.bot.sent[0]["text"]
    assert context.bot.sent[0]["chat_id"] == 999


@pytest.mark.asyncio
async def test_rejection_notifications_can_be_switched_off(db, context, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "location_channel_notify_rejections", False)
    await location_channel.handle_channel_post(
        channel_update("nonsense", message_id=9), context
    )
    assert context.bot.sent == []


@pytest.mark.asyncio
async def test_a_post_with_no_text_is_simply_skipped(db, context):
    update = channel_update("", message_id=3)
    await location_channel.handle_channel_post(update, context)
    assert context.bot.sent == []
    assert db.query(Location).count() == 0


@pytest.mark.asyncio
async def test_a_post_with_no_message_id_is_skipped(db, context):
    update = channel_update(RECORD, message_id=None)
    await location_channel.handle_channel_post(update, context)
    assert db.query(Location).count() == 0


@pytest.mark.asyncio
async def test_replaying_the_same_update_is_idempotent(db, context):
    for _ in range(3):
        await location_channel.handle_channel_post(
            channel_update(RECORD, message_id=42), context
        )
    assert db.query(Location).filter(Location.location_code == "AA-0001").count() == 1


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def test_handlers_are_registered_when_a_channel_is_configured():
    handlers = location_channel.build_channel_post_handlers()
    assert len(handlers) == 2


def test_nothing_is_registered_without_a_channel(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "location_channel_id", 0)
    assert location_channel.build_channel_post_handlers() == []


def test_the_channel_may_be_given_as_a_string(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "location_channel_id", str(CHAT))
    assert location_channel._is_location_channel(CHAT)
    assert location_channel._is_location_channel(str(CHAT))
    assert not location_channel._is_location_channel(OTHER_CHAT)


def test_the_listener_is_wired_into_the_application():
    """A channel post also matches ``filters.TEXT``, so the listener has to be
    registered ahead of the ordinary handlers or the bot would reply into the
    channel with "I did not understand that"."""
    from app.bot import build_application
    from app.config import settings

    original = settings.bot_token
    settings.bot_token = "123456:test-token"
    try:
        application = build_application()
    finally:
        settings.bot_token = original

    first = application.handlers[0]
    assert first is not None, "the channel listener must be registered first"
    assert first[0].callback is location_channel.handle_channel_post

# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------


def test_no_log_call_reserves_a_logrecord_attribute():
    """``extra={"name": ...}`` raises inside logging rather than logging.

    That made an otherwise-successful import look like a failure, with the
    exception escaping into the handler — so a correct record was silently
    dropped. Found the hard way; kept cheap so it cannot come back.

    Walked with :mod:`ast` rather than a line scan, because only the keys of a
    literal passed as ``extra=`` are at risk.
    """
    import ast
    import logging
    from pathlib import Path

    reserved = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)))
    reserved |= {"message", "asctime", "taskName"}

    root = Path(__file__).resolve().parent.parent / "app"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "extra":
                    continue
                if not isinstance(keyword.value, ast.Dict):
                    continue
                for key in keyword.value.keys:
                    if (
                        isinstance(key, ast.Constant)
                        and key.value in reserved
                    ):
                        offenders.append(f"{path.name}:{key.lineno} {key.value}")
    assert not offenders, f"reserved LogRecord keys used in extra: {offenders}"


@pytest.mark.asyncio
async def test_a_successful_import_logs_without_raising(db, context, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="app.bot.handlers.location_channel"):
        await location_channel.handle_channel_post(
            channel_update(RECORD, message_id=77), context
        )
    assert any(
        record.levelno == logging.INFO for record in caplog.records
    ), "a successful import should say so in the log"
