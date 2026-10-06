"""Outbound notifications to admins, tutors and support channels."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.bot.keyboards.admin import (
    candidate_actions_keyboard,
    match_result_keyboard,
    new_tutor_match_keyboard,
    tutor_review_keyboard,
)
from app.bot.keyboards.support import support_ticket_keyboard
from app.config import settings
from app.models import JobPost, StudentRequest, SupportTicket, Tutor
from app.services.currency import format_rate, is_ethiopia
from app.enums import Currency

logger = logging.getLogger(__name__)


def admin_chat_id() -> int:
    """Operations chat used for admin notifications."""
    return settings.tutor_storage_chat_id


async def _safe_send(bot: Bot | None, chat_id: int | None, text: str, **kwargs: Any) -> bool:
    """Send a message, never raising (Requirement 13.4)."""
    if bot is None or not chat_id:
        logger.warning("Notification skipped: no bot or chat id configured")
        return False
    try:
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        return True
    except TelegramError as exc:
        logger.error("Notification delivery failed: %s", type(exc).__name__)
        return False


async def notify_admin_new_tutor(
    bot: Bot | None, tutor: Tutor, storage_chat_id: int | None = None
) -> bool:
    """Requirement 5.1: application summary with inline review actions."""
    rate = format_rate(
        tutor.etb_rate if is_ethiopia(tutor.country) else tutor.usd_rate,
        Currency.ETB if is_ethiopia(tutor.country) else Currency.USD,
    )
    text = (
        "🆕 <b>New tutor application</b>\n\n"
        f"TDR ID: <code>{tutor.public_tutor_id}</code>\n"
        f"Name: {tutor.name}\n"
        f"Display name: {tutor.display_name}\n"
        f"Subjects: {', '.join(tutor.subject_list) or '—'}\n"
        f"Country: {tutor.country} ({tutor.city})\n"
        f"Teaching mode: {tutor.teaching_mode}\n"
        f"Rate: {rate}\n"
        f"Status: {tutor.status}"
    )
    sent = await _safe_send(
        bot,
        storage_chat_id or admin_chat_id(),
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=tutor_review_keyboard(tutor.public_tutor_id),
    )
    if sent:
        logger.info("Admin notified about new tutor", extra={"tdr_id": tutor.public_tutor_id})
    return sent


async def notify_tutor_verified(bot: Bot | None, tutor: Tutor) -> bool:
    text = (
        "🎉 <b>Congratulations — you are verified!</b>\n\n"
        f"Your TDR ID is <code>{tutor.public_tutor_id}</code>.\n"
        "Students can now find you through the Tedor Tutors platform."
    )
    sent = await _safe_send(
        bot, tutor.telegram_user_id, text, parse_mode=ParseMode.HTML
    )
    if sent:
        logger.info("Tutor verification notice sent", extra={"tdr_id": tutor.public_tutor_id})
    return sent


async def notify_tutor_rejected(
    bot: Bot | None, tutor: Tutor, reason: str | None = None
) -> bool:
    text = (
        "😔 <b>Application update</b>\n\n"
        f"Your application ({tutor.public_tutor_id}) could not be approved at this time."
    )
    if reason:
        text += f"\n\nReason: {reason}"
    text += "\n\nYou are welcome to apply again later."
    sent = await _safe_send(bot, tutor.telegram_user_id, text, parse_mode=ParseMode.HTML)
    if sent:
        logger.info("Tutor rejection notice sent", extra={"tdr_id": tutor.public_tutor_id})
    return sent


async def notify_tutor_under_review(bot: Bot | None, tutor: Tutor) -> bool:
    text = (
        "⏳ <b>Your application is under review</b>\n\n"
        f"Reference: <code>{tutor.public_tutor_id}</code>\n"
        "Our team is checking your documents and will get back to you shortly."
    )
    return await _safe_send(bot, tutor.telegram_user_id, text, parse_mode=ParseMode.HTML)


async def request_more_info(bot: Bot | None, tutor: Tutor, message: str) -> bool:
    """Requirement 5.4: send the admin's message to the tutor."""
    text = (
        "📩 <b>Tedor Tutors needs more information</b>\n\n"
        f"{message}\n\n"
        f"Reference: <code>{tutor.public_tutor_id}</code>\n"
        "Reply to this message and your answer goes straight to our team."
    )
    sent = await _safe_send(bot, tutor.telegram_user_id, text, parse_mode=ParseMode.HTML)
    if sent:
        logger.info(
            "More-information request sent", extra={"tdr_id": tutor.public_tutor_id}
        )
    return sent


async def notify_support_ticket(
    bot: Bot | None,
    ticket: SupportTicket,
    admin_chat: int | None = None,
) -> bool:
    """Requirement 8.3: forward the ticket and notify the admin chat."""
    text = (
        "🛠 <b>New support ticket</b>\n\n"
        f"Ticket: <code>{ticket.public_ticket_id}</code>\n"
        f"Category: {ticket.category}\n"
        f"From: {ticket.telegram_user_fullname or ticket.username or 'unknown'}\n\n"
        f"{ticket.message}"
    )
    sent = await _safe_send(
        bot,
        admin_chat or admin_chat_id(),
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=support_ticket_keyboard(ticket.public_ticket_id),
    )
    if sent:
        logger.info("Support ticket notification sent")
    return sent


async def notify_admin_tutor_selected(
    bot: Bot | None, request: StudentRequest, tutor: Tutor
) -> bool:
    """Requirement 7.4: admin is told which tutor a parent picked."""
    text = (
        "🙋 <b>Student tutor selection</b>\n\n"
        f"Request: <code>{request.public_request_id}</code>\n"
        f"Parent: {request.parent_name}\n"
        f"Student: {request.student_name}\n"
        f"Subject: {request.subject}\n"
        f"Selected tutor: {tutor.public_tutor_id} — {tutor.display_name}"
    )
    return await _safe_send(bot, admin_chat_id(), text, parse_mode=ParseMode.HTML)


async def notify_admin_student_request(
    bot: Bot | None, request: StudentRequest
) -> bool:
    budget = "—"
    if request.budget is not None:
        budget = f"{request.budget:g} {request.currency}"
    text = (
        "🔎 <b>New tutor request</b>\n\n"
        f"Request: <code>{request.public_request_id}</code>\n"
        f"Parent: {request.parent_name}\n"
        f"Country: {request.country}\n"
        f"Subject: {request.subject}\n"
        f"Level: {request.education_level}\n"
        f"Mode: {request.mode}\n"
        f"Budget: {budget}"
    )
    return await _safe_send(bot, admin_chat_id(), text, parse_mode=ParseMode.HTML)


async def notify_user_no_match(bot: Bot | None, request: StudentRequest) -> bool:
    text = (
        "😔 <b>No matching tutors yet</b>\n\n"
        f"Reference: <code>{request.public_request_id}</code>\n"
        "Our team has been notified and will contact you shortly."
    )
    return await _safe_send(bot, request.telegram_user_id, text, parse_mode=ParseMode.HTML)


async def notify_admin_match_candidates(
    bot: Bot | None,
    results: list,
    post_id: str,
) -> None:
    """Send one message per candidate with its action keyboard (§19)."""
    from app.services.job_match_pipeline import format_candidate_card

    for result in results:
        await _safe_send(
            bot,
            admin_chat_id(),
            format_candidate_card(result),
            parse_mode=ParseMode.HTML,
            reply_markup=candidate_actions_keyboard(post_id, result.public_tutor_id),
        )


async def notify_admin_job_matches(
    bot: Bot | None,
    report_text: str,
    post_id: str,
    count: int,
) -> bool:
    """Deliver the explainable top-N report to the admin (sections 17, 18)."""
    text = report_text
    if len(text) > 4000:
        text = report_text[:3950] + "\n\n…"
    sent = await _safe_send(
        bot,
        admin_chat_id(),
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=match_result_keyboard(post_id, count),
    )
    if sent:
        logger.info("Admin notified of job matches", extra={"post_id": post_id})
    return sent


async def notify_admin_new_tutor_match(
    bot: Bot | None,
    tutor: Tutor,
    post: JobPost,
    percentage: int,
) -> bool:
    """A freshly verified tutor is a strong match for an open job (§22)."""
    text = (
        "🆕 <b>New strong tutor match found</b>\n\n"
        f"Job: <code>{post.public_post_id}</code> — {post.student_level} {post.subjects}\n"
        f"Tutor: <code>{tutor.public_tutor_id}</code> — {tutor.display_name}\n"
        f"Score: <b>{percentage}%</b>"
    )
    return await _safe_send(
        bot,
        admin_chat_id(),
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=new_tutor_match_keyboard(tutor.public_tutor_id, post.public_post_id),
    )


async def notify_tutor_selected(bot: Bot | None, tutor: Tutor, post: JobPost) -> bool:
    text = (
        "🎉 <b>Good news!</b>\n\n"
        f"You were selected for the tutoring job <code>{post.public_post_id}</code> "
        f"({post.student_level}, {post.subjects}).\n"
        "Our team will contact you with the details."
    )
    return await _safe_send(bot, tutor.telegram_user_id, text, parse_mode=ParseMode.HTML)


async def notify_tutor_shortlisted(bot: Bot | None, tutor: Tutor, post: JobPost) -> bool:
    text = (
        "⭐ <b>You are shortlisted!</b>\n\n"
        f"You were shortlisted for job <code>{post.public_post_id}</code>. "
        "Our team may contact you soon."
    )
    return await _safe_send(bot, tutor.telegram_user_id, text, parse_mode=ParseMode.HTML)


def find_ticket_by_admin_message(
    session: Session, message_id: int | None
) -> SupportTicket | None:
    """Locate the ticket behind an admin chat message (for reply relay)."""
    if message_id is None:
        return None
    return session.execute(
        select(SupportTicket).where(SupportTicket.admin_message_id == message_id)
    ).scalars().first()


def find_tutor_by_admin_message(session: Session, message_id: int | None) -> Tutor | None:
    """Locate the tutor behind an admin notification message."""
    if message_id is None:
        return None
    tdr = session.execute(
        select(Tutor).where(Tutor.telegram_storage_message_id == message_id)
    ).scalars().first()
    return tdr


__all__ = [
    "admin_chat_id",
    "find_ticket_by_admin_message",
    "find_tutor_by_admin_message",
    "notify_admin_new_tutor",
    "notify_admin_student_request",
    "notify_admin_tutor_selected",
    "notify_support_ticket",
    "notify_tutor_rejected",
    "notify_tutor_under_review",
    "notify_tutor_verified",
    "notify_user_no_match",
    "request_more_info",
]