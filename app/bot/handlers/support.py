"""Customer support conversation (Requirement 8.x)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers.common import answer, end_flow, safe_reply, start_flow
from app.bot.keyboards.support import (
    CATEGORY_LABELS,
    support_category_keyboard,
    support_nav_keyboard,
)
from app.bot.states import SupportStates
from app.database import session_scope
from app.models import SupportTicket, utcnow
from app.services import notification_service
from app.services.storage_service import TelegramStorageService

logger = logging.getLogger(__name__)

CATEGORY_KEY = "support_category"
MESSAGE_KEY = "support_message"


async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Requirement 8.1: category selection."""
    start_flow(context, "support")
    context.user_data.pop(CATEGORY_KEY, None)
    context.user_data.pop(MESSAGE_KEY, None)
    await safe_reply(
        update,
        "📩 <b>Contact Tedor Tutors</b>\n\nChoose a topic and describe your issue:",
        support_category_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return int(SupportStates.CATEGORY)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(CATEGORY_KEY, None)
    context.user_data.pop(MESSAGE_KEY, None)
    end_flow(context)
    await safe_reply(update, "❌ Cancelled. Type /start to begin again.")
    return ConversationHandler.END


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await answer(update)
    data = update.callback_query.data or ""
    if data == "support:cancel":
        return await cancel(update, context)
    if data.startswith("support:cat:"):
        category = data.split(":")[-1]
        context.user_data[CATEGORY_KEY] = category
        label = CATEGORY_LABELS.get(category, category)
        await safe_reply(
            update,
            f"💬 <b>{label}</b>\n\nNow send your message — our team will reply here.",
            support_nav_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return int(SupportStates.MESSAGE)
    if data.startswith("support:reply:") or data.startswith("support:close:"):
        ticket_id = data.split(":")[-1]
        if data.startswith("support:close:"):
            with session_scope() as session:
                ticket = _find_ticket(session, ticket_id)
                if ticket is not None:
                    ticket.status = "CLOSED"
                    ticket.updated_at = utcnow()
            await safe_reply(update, f"✅ Ticket {ticket_id} marked as resolved.")
            return int(SupportStates.CATEGORY)
        context.user_data["admin_reply_ticket"] = ticket_id
        await safe_reply(
            update,
            f"↩️ Send your reply to ticket <code>{ticket_id}</code> — it will be "
            "relayed to the user immediately.",
        )
        return int(SupportStates.DONE)
    return int(SupportStates.CATEGORY)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Requirement 8.2/8.3: persist the ticket and notify the admin."""
    category = context.user_data.get(CATEGORY_KEY)
    if not category:
        return await start_support(update, context)

    message_text = (update.message.text or "").strip()
    if not message_text:
        await safe_reply(update, "⚠️ Please send a message.")
        return int(SupportStates.MESSAGE)

    user = update.effective_user
    with session_scope() as session:
        ticket = SupportTicket(
            public_ticket_id=_next_ticket_id(session),
            telegram_user_id=user.id,
            username=user.username or "",
            telegram_user_fullname=user.full_name or "",
            category=category,
            message=message_text,
            status="OPEN",
        )
        session.add(ticket)
        session.flush()
        ticket_id = ticket.public_ticket_id
        logger.info("Support ticket created")

    storage = TelegramStorageService(context.bot)
    result = await storage.store_support_ticket(ticket_id)

    with session_scope() as session:
        ticket = _find_ticket(session, ticket_id)
        if ticket is not None:
            if result.success:
                ticket.admin_chat_id = result.chat_id
                ticket.admin_message_id = result.record_message_id
            await notification_service.notify_support_ticket(context.bot, ticket)

    await safe_reply(
        update,
        "✅ <b>Message received</b>\n\n"
        f"Ticket <code>{ticket_id}</code> — our team will reply to you here.",
    )
    end_flow(context)
    return ConversationHandler.END


def _next_ticket_id(session) -> str:
    last = session.execute(
        select(SupportTicket.public_ticket_id).order_by(SupportTicket.id.desc()).limit(1)
    ).scalar_one_or_none()
    sequence = int(last.split("-")[1]) + 1 if last else 1
    return f"TKT-{sequence:06d}"


def _find_ticket(session, ticket_id: str) -> SupportTicket | None:
    return session.execute(
        select(SupportTicket).where(SupportTicket.public_ticket_id == str(ticket_id).upper())
    ).scalars().first()


async def admin_reply_relay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Requirement 8.4: relay an admin's reply back to the ticket owner."""
    message = update.effective_message
    if message is None or not getattr(message, "reply_to_message", None):
        return
    replied = message.reply_to_message
    with session_scope() as session:
        ticket = session.execute(
            select(SupportTicket).where(SupportTicket.admin_message_id == replied.message_id)
        ).scalars().first()
        if ticket is None:
            return
        try:
            await context.bot.send_message(
                chat_id=ticket.telegram_user_id,
                text=(
                    f"💬 <b>Tedor Tutors reply</b> "
                    f"(<code>{ticket.public_ticket_id}</code>)\n\n{message.text or ''}"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.error("Could not relay the support reply to the user")
            await message.reply_text("⚠️ Could not deliver that reply.")
            return
        ticket.status = "ANSWERED"
        ticket.updated_at = utcnow()


def build_support_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_support, pattern=r"^menu:contact$"),
        ],
        states={
            int(SupportStates.CATEGORY): [CallbackQueryHandler(callback_handler)],
            int(SupportStates.MESSAGE): [
                CallbackQueryHandler(callback_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler),
            ],
            int(SupportStates.DONE): [CallbackQueryHandler(callback_handler)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.COMMAND, cancel),
        ],
        name="support",
        allow_reentry=True,
    )


__all__ = [
    "admin_reply_relay",
    "build_support_conversation",
    "callback_handler",
    "message_handler",
    "start_support",
]