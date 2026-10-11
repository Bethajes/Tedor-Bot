"""Bot application wiring (Requirement 13.4)."""

from __future__ import annotations

import logging
from typing import Any

from telegram import BotCommand, BotCommandScopeChat
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers.admin import (
    build_admin_conversation,
    support_reply_relay,
    tutor_reply_relay,
)
from app.bot.handlers.job_post import build_job_post_conversation
from app.bot.handlers.matching import build_match_handlers
from app.bot.handlers.tutor_onboarding import build_conversation as build_ethiopian_conversation
from app.bot.handlers.start import (
    cancel,
    help_command,
    id_command,
    menu_callback,
    start,
    support_command,
    unknown_message,
    website_command,
)
from app.bot.handlers.student import build_student_conversation
from app.bot.handlers.support import build_support_conversation
from app.bot.handlers.tutor import build_tutor_conversation
from app.config import settings
from app.database import init_db
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


async def error_handler(update: object, context: Any) -> None:
    """Requirement 13.4: log and keep serving other users."""
    logger.error(
        "Unhandled update error: %s",
        getattr(context, "error", None) or "unknown",
    )


def build_application() -> Application:
    """Create the configured ``telegram.ext.Application``."""
    configure_logging(settings.log_level)

    if not settings.bot_token:
        raise RuntimeError(
            "BOT_TOKEN is not configured — copy .env.example to .env and fill it in."
        )

    builder = ApplicationBuilder().token(settings.bot_token)
    if settings.database_url.startswith("sqlite"):
        builder = builder.post_init(_init_database)

    application = builder.build()

    # Group 0 — conversations. Order matters: the new Ethiopian onboarding flow
    # is registered first so its ``menu:become_tutor_ethiopia`` entry point
    # clearly owns the primary "Become a Tutor" button. The legacy 26-step
    # tutor conversation stays registered afterwards for existing sessions.
    application.add_handler(build_ethiopian_conversation())
    application.add_handler(build_tutor_conversation())
    application.add_handler(build_student_conversation())
    application.add_handler(build_support_conversation())
    application.add_handler(build_job_post_conversation())
    # The match commands/callbacks must be checked before the admin console so
    # their own namespace (``match:``) and the ``/match`` command are not
    # absorbed by the admin conversation's fallback callback handler.
    for handler in build_match_handlers():
        application.add_handler(handler)
    application.add_handler(build_admin_conversation())

    # Group 1 — commands and global navigation.
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("support", support_command))
    application.add_handler(CommandHandler("website", website_command))
    application.add_handler(
        CallbackQueryHandler(menu_callback, pattern=r"^menu:(home|help|how)$")
    )

    # Group 2 — replies relayed between users and the admin chat.
    if settings.tutor_storage_chat_id:
        application.add_handler(
            MessageHandler(
                filters.REPLY
                & filters.TEXT
                & filters.Chat(chat_id=settings.tutor_storage_chat_id),
                support_reply_relay,
            ),
            group=2,
        )
    application.add_handler(
        MessageHandler(filters.REPLY & filters.TEXT, tutor_reply_relay), group=2
    )

    # Group 3 — friendly fallback for anything else.
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_message), group=3
    )

    application.add_error_handler(error_handler)
    return application


async def _init_database(application: Application) -> None:
    init_db()
    await _publish_admin_commands(application)
    logger.info("Bot initialised")


async def _publish_admin_commands(application: Application) -> None:
    """Advertise the admin commands in each admin's slash menu.

    Without this Telegram shows no command list at all, so ``/post`` looked like
    it did not exist. Scoped per admin chat, so regular users never see it.
    Best effort only — a network failure must not stop the bot.
    """
    admin_ids = {int(item) for item in settings.admin_user_ids}
    if not admin_ids:
        return
    commands = [
        BotCommand("post", "Create and publish a tutor job opening"),
        BotCommand("match", "Show or re-run tutor matches for a job"),
        BotCommand("admin", "Open the admin console"),
        BotCommand("stats", "Show platform statistics"),
        BotCommand("search_tutor", "Search stored tutors"),
        BotCommand("cancel", "Stop the current form"),
    ]
    for admin_id in admin_ids:
        try:
            await application.bot.set_my_commands(
                commands=commands, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except TelegramError:
            logger.warning("Could not publish the command list", extra={"chat_id": admin_id})


async def run_bot() -> None:
    """Start long polling (used by ``run.py`` and ``python -m app.main``)."""
    application = build_application()
    logger.info("Starting Telegram bot polling")
    await application.initialize()
    await application.start()
    try:
        await application.updater.start_polling(drop_pending_updates=True)
        await asyncio_event()
    finally:
        if application.updater is not None:
            await application.updater.stop()
        await application.stop()
        await application.shutdown()


async def asyncio_event() -> None:  # pragma: no cover - long running
    import asyncio

    while True:
        await asyncio.sleep(3600)


__all__ = ["build_application", "error_handler", "run_bot"]