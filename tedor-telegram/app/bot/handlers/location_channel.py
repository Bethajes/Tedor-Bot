"""Keep the location directory in step with the Telegram location channel.

The channel is where an administrator edits tutoring locations; this module
watches it and mirrors each post into the database that matching reads. It
handles **future** posts and edits, which arrive as ``channel_post`` and
``edited_channel_post`` updates — the Bot API has no method that returns a
channel's history, so posts made before the bot was added must come through
``app.import_locations`` from a channel export instead.

Security
--------

Only one chat is ever read, and it is the one in ``LOCATION_CHANNEL_ID``.
Nothing else in the bot is connected to this module: a location post cannot
start a conversation, cannot be mistaken for a tutor application, and cannot
reach a parent. The channel is not a command surface — it holds data only —
so an update from it is parsed and never executed.

Rejections are reported to administrators rather than into the channel, and
only when ``LOCATION_CHANNEL_NOTIFY_REJECTIONS`` is on: the channel is public,
so a stranger posting an unrelated message must not be able to make the bot
message an admin.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.config import settings
from app.database import session_scope
from app.services import location_channel_service as channel

logger = logging.getLogger(__name__)

__all__ = ["build_channel_post_handlers", "handle_edited_channel_post", "handle_channel_post"]


def _is_location_channel(chat_id: int | None) -> bool:
    """Whether this update came from the configured location channel.

    Compares as plain integers because Telegram may report a channel id as
    ``-100123`` while the environment carries it as a string.
    """
    if not settings.location_channel_id or chat_id is None:
        return False
    try:
        return int(chat_id) == int(settings.location_channel_id)
    except (TypeError, ValueError):  # pragma: no cover - misconfiguration
        return False


async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Send one message to each configured administrator, best effort.

    A failure to reach an admin is logged and swallowed: the import itself has
    already succeeded, and a notification problem must not turn a successful
    sync into an error the bot keeps retrying.
    """
    bot = context.bot
    for raw_id in settings.admin_user_ids or []:
        try:
            await bot.send_message(
                chat_id=int(raw_id),
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except (TelegramError, ValueError, TypeError):
            logger.warning("Could not notify an administrator about a sync", exc_info=True)


async def _apply(
    update: Update, context: ContextTypes.DEFAULT_TYPE, message: Any
) -> None:
    """Mirror one channel message into the database.

    Returns quietly when the message carries no text (a photo caption-only
    post, a service message) rather than reporting it as a rejection.
    """
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if not _is_location_channel(chat_id):
        return

    text = getattr(message, "text", None)
    if not isinstance(text, str) or not text.strip():
        return

    message_id = getattr(message, "message_id", None)
    if message_id is None:  # pragma: no cover - Telegram always sets it
        return

    try:
        with session_scope() as session:
            outcome = channel.sync_message(
                session, text, chat_id=int(chat_id), message_id=int(message_id)
            )
    except channel.RecordError as exc:
        logger.warning(
            "Rejected a channel location record",
            extra={"chat_id": chat_id, "message_id": message_id, "reason": exc.user_message},
        )
        if settings.location_channel_notify_rejections:
            await _notify_admins(
                context,
                "❌ <b>Location channel record rejected</b>\n"
                f"<code>{exc.user_message}</code>\n"
                f"<i>Chat {chat_id}, message {message_id}.</i>",
            )
        return
    except Exception:
        # Never let one unparseable post stop the bot from polling. The record
        # is simply not imported and the administrator sees it in the logs.
        logger.exception(
            "Could not import a channel location record",
            extra={"chat_id": chat_id, "message_id": message_id},
        )
        return

    logger.info(
        "Channel location record %s",
        outcome.action,
        extra={
            "location_code": outcome.record.id,
            "location_name": outcome.record.name,
            "sync_action": outcome.action,
        },
    )


async def handle_channel_post(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """A new post in the location channel."""
    await _apply(update, context, getattr(update, "channel_post", None))


async def handle_edited_channel_post(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """An edited post in the location channel.

    The same record code addresses the same row, so an administrator fixing a
    coordinate or a spelling updates exactly one place and every tutor and job
    already pointing at it sees the correction.
    """
    await _apply(update, context, getattr(update, "edited_channel_post", None))


def build_channel_post_handlers() -> list[Any]:
    """Handlers for channel updates, or an empty list when unconfigured.

    ``telegram.ext.filters.StatusUpdate`` is deliberately not used: it matches
    *service* messages (a member joined, a title changed) and does not match
    ``Update.channel_post`` at all, which is where an ordinary post in a
    channel arrives. These filters match the update fields themselves.

    The handlers are registered ahead of the ordinary message handlers on
    purpose: a channel post also satisfies ``filters.TEXT`` through
    ``Update.effective_message``, so without an earlier handler the generic
    "I did not understand that" reply would fire into the channel.
    """
    from telegram.ext import MessageHandler
    from telegram.ext.filters import BaseFilter

    if not settings.location_channel_id:
        logger.info(
            "LOCATION_CHANNEL_ID is not set; channel location sync is disabled"
        )
        return []

    class _ChannelPost(BaseFilter):
        name = "filters.channel_post"

        def filter(self, update: Update) -> bool:
            return getattr(update, "channel_post", None) is not None

    class _EditedChannelPost(BaseFilter):
        name = "filters.edited_channel_post"

        def filter(self, update: Update) -> bool:
            return getattr(update, "edited_channel_post", None) is not None

    logger.info(
        "Watching location channel %s for location records",
        settings.location_channel_id,
    )
    return [
        MessageHandler(filters=_ChannelPost(), callback=handle_channel_post),
        MessageHandler(filters=_EditedChannelPost(), callback=handle_edited_channel_post),
    ]