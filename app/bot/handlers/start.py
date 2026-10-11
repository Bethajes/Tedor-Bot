"""Entry points and global navigation (Requirement 1.x)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot.handlers.common import (
    active_flow,
    answer,
    end_flow,
    safe_edit,
    safe_reply,
)
from app.bot.keyboards.main import help_keyboard, how_it_works_keyboard, main_menu_keyboard
from app.config import settings

logger = logging.getLogger(__name__)

WELCOME = (
    "👋 <b>Welcome to Tedor Tutors</b>\n\n"
    "I connect families with verified tutors and help teachers join our platform.\n\n"
    "What would you like to do?"
)

HOW_IT_WORKS = (
    "ℹ️ <b>How Tedor Tutors works</b>\n\n"
    "<b>For families 👨‍👩‍👧</b>\n"
    "1. Tap 🔎 Find a Tutor and answer a few questions.\n"
    "2. We show you verified tutors that match your subject, level and budget.\n"
    "3. Pick a tutor — we introduce you and follow up personally.\n\n"
    "<b>For teachers 👨‍🏫</b>\n"
    "1. Tap 👨‍🏫 Become a Tutor and complete the guided application.\n"
    "2. Send your CV and degree certificates.\n"
    "3. Our verification team reviews everything and you receive a TDR ID."
)

HELP_TEXT = (
    "ℹ️ <b>Available commands</b>\n\n"
    "/start — open the main menu\n"
    "/help — show this list\n"
    "/id — show your own Telegram user ID\n"
    "/cancel — stop the current form and return to the menu\n"
    "/apply — start the tutor application\n"
    "/my_application — check your tutor application status\n"
    "/complete_profile — fill in the details you skipped\n"
    "/support — contact the Tedor Tutors team\n"
    "/website — open the Tedor Tutors website\n\n"
    "Admins also have: /admin, /stats, /search_tutor, /match and /post."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply(update, WELCOME, main_menu_keyboard(), parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply(update, HELP_TEXT, help_keyboard(), parse_mode=ParseMode.HTML)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Requirement 1.3: leave any conversation and return to the menu."""
    from app.bot.handlers.student import cancel as student_cancel

    await student_cancel(update, context)
    end_flow(context)
    await safe_reply(update, WELCOME, main_menu_keyboard(), parse_mode=ParseMode.HTML)


async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from app.bot.handlers.support import start_support

    return await start_support(update, context)


async def website_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply(
        update,
        f"🌐 Visit us at {settings.website_url}",
        main_menu_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tell the sender their own Telegram user ID.

    Admin access is granted by numeric user ID only, and the usual mistake is
    putting a channel id (``-100…``) there. This makes the correct value easy to
    read off without leaving the chat.
    """
    user = update.effective_user
    if user is None:  # pragma: no cover - defensive
        return
    from app.bot.handlers.admin import is_admin

    configured = sorted({int(item) for item in settings.admin_user_ids})
    status = (
        "✅ You are an admin."
        if is_admin(user.id)
        else "❌ You are not an admin yet."
    )
    await safe_reply(
        update,
        "🪪 <b>Your Telegram details</b>\n\n"
        f"User ID: <code>{user.id}</code>\n"
        f"First name: {user.first_name}\n"
        f"Admin: {status}\n"
        f"Configured admin IDs: {len(configured)}",
        parse_mode=ParseMode.HTML,
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the main-menu inline buttons."""
    await answer(update)
    data = update.callback_query.data or ""
    if data == "menu:home" or data == "menu:become_tutor":
        # The tutor conversation owns ``menu:become_tutor``; only the home
        # button reaches this handler.
        await safe_edit(update, WELCOME, main_menu_keyboard(), parse_mode=ParseMode.HTML)
    elif data == "menu:help":
        await safe_edit(
            update,
            "ℹ️ Use /help to see every command.",
            how_it_works_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    elif data == "menu:how":
        await safe_edit(update, HOW_IT_WORKS, how_it_works_keyboard(), parse_mode=ParseMode.HTML)


async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Requirement 1.4: prompt the user back to the main menu.

    While a conversation is running the active handler is responsible for the
    reply, so the global fallback stays silent.
    """
    if active_flow(context):
        return
    await safe_reply(
        update,
        "🤔 I did not recognise that. Please use the menu below or type /start.",
        main_menu_keyboard(),
        parse_mode=ParseMode.HTML,
    )


__all__ = [
    "HELP_TEXT",
    "HOW_IT_WORKS",
    "WELCOME",
    "cancel",
    "help_command",
    "id_command",
    "menu_callback",
    "start",
    "support_command",
    "unknown_message",
    "id_command",
    "website_command",
]