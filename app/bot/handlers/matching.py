"""Matching callbacks and /match command (Requirements 19, 21).

Candidate actions are the admin's way of driving a recommendation forward:
shortlist, view the profile, pull the documents or the English recording,
contact or select. Every action is admin-only and every one ends with a status
change on the persisted candidate row — never an automatic assignment.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)

from app.bot.handlers.admin import is_admin, require_admin
from app.bot.handlers.common import answer, safe_reply
from app.bot.keyboards.admin import candidate_shortlist_keyboard
from app.config import settings
from app.database import session_scope
from app.enums import CandidateStatus
from app.services import job_match_pipeline as pipeline
from app.services import notification_service

logger = logging.getLogger(__name__)


def build_match_handlers() -> list[Any]:
    """Global handlers for the match: namespace (group 4, after fallbacks)."""
    return [
        CommandHandler("match", match_command),
        CallbackQueryHandler(match_callback, pattern=r"^match:"),
    ]


async def match_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """``/match <job_id>`` — rerun matching for a job (Requirement 21)."""
    if not await require_admin(update):
        return ConversationHandler.END
    args = context.args or []
    if not args:
        await safe_reply(
            update,
            "Usage: <code>/match &lt;job_id&gt;</code>, e.g. <code>/match JOB-000001</code>.",
            parse_mode="HTML",
        )
        return ConversationHandler.END
    reference = args[0].strip().upper()
    with session_scope() as session:
        post = pipeline.get_job(session, reference)
        if post is None:
            await safe_reply(update, f"No job found for <code>{reference}</code>.", parse_mode="HTML")
            return ConversationHandler.END
        results = pipeline.run_match_for_job(session, post)
        post_id = post.public_post_id
    report = pipeline.format_report(post, results, threshold_note=_threshold_note())
    await notification_service.notify_admin_job_matches(
        context.bot, report, post_id, len(results)
    )
    await safe_reply(
        update,
        f"✅ Matching re-run for <code>{post_id}</code> — {len(results)} candidate(s). "
        "The report was sent to the admin chat.",
        parse_mode="HTML",
    )
    return ConversationHandler.END


def _threshold_note() -> str:
    return f"Minimum score for a report: {settings.match_min_score:g}% • Notified at ≥{settings.match_notification_threshold:g}%"


# ---------------------------------------------------------------------------
# callback actions
# ---------------------------------------------------------------------------


async def match_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """All ``match:*`` callback actions."""
    if not await require_admin(update):
        return ConversationHandler.END
    await answer(update)
    data = update.callback_query.data or ""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "shortlist_menu":
        return await _shortlist_menu(update, context, parts[2] if len(parts) > 2 else "")
    if action == "shortlist":
        return await _set_status(update, context, parts[2], parts[3], CandidateStatus.SHORTLISTED)
    if action == "ignore":
        return await _set_status(update, context, parts[2], parts[3], CandidateStatus.REJECTED)
    if action == "select":
        return await _set_status(update, context, parts[2], parts[3], CandidateStatus.SELECTED)
    if action == "contact":
        return await _contact(update, context, parts[2], parts[3])
    if action == "view":
        return await _view(update, context, parts[2], parts[3])
    if action == "docs":
        return await _documents(update, context, parts[2], parts[3])
    if action == "voice":
        return await _voice(update, context, parts[2], parts[3])
    if action == "rerun":
        return await _rerun(update, context, parts[2] if len(parts) > 2 else "")
    if action == "back":
        return await _back_to_report(update, context, parts[2] if len(parts) > 2 else "")
    return ConversationHandler.END


async def _set_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    job_id: str,
    tutor_id: str,
    new_status: CandidateStatus,
) -> int:
    with session_scope() as session:
        candidate = pipeline.set_candidate_status(session, job_id, tutor_id, new_status)
        if candidate is None:
            await safe_reply(update, "⚠️ That candidate is no longer in the results.")
            return ConversationHandler.END
        tutor_name = candidate.tutor.display_name

    labels = {
        CandidateStatus.SHORTLISTED: "⭐ shortlisted",
        CandidateStatus.REJECTED: "❌ ignored",
        CandidateStatus.SELECTED: "✅ selected",
        CandidateStatus.CONTACTED: "📩 contacted",
        CandidateStatus.RECOMMENDED: "recommended",
    }
    await safe_reply(
        update,
        f"✅ <code>{tutor_id}</code> ({tutor_name}) marked {labels.get(new_status, new_status)} for <code>{job_id}</code>.",
        parse_mode="HTML",
    )

    if new_status == CandidateStatus.SELECTED:
        await _notify_tutor(update, context, tutor_id, job_id)
    elif new_status == CandidateStatus.SHORTLISTED:
        await _notify_tutor(update, context, tutor_id, job_id, shortlisted=True)
    return ConversationHandler.END


async def _notify_tutor(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tutor_id: str,
    job_id: str,
    *,
    shortlisted: bool = False,
) -> None:
    with session_scope() as session:
        candidate = pipeline.candidate_for(session, job_id, tutor_id)
        if candidate is None:
            return
        tutor = candidate.tutor
        post = candidate.job_post
        if shortlisted:
            await notification_service.notify_tutor_shortlisted(context.bot, tutor, post)
        else:
            await notification_service.notify_tutor_selected(context.bot, tutor, post)


async def _contact(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: str, tutor_id: str
) -> int:
    with session_scope() as session:
        candidate = pipeline.candidate_for(session, job_id, tutor_id)
        if candidate is None:
            await safe_reply(update, "⚠️ Candidate not found.")
            return ConversationHandler.END
        contact = candidate.tutor.phone or "—"
        handle = candidate.tutor.email or "—"
        text = (
            f"📩 Contact details for <code>{tutor_id}</code>\n\n"
            f"Name: {candidate.tutor.display_name}\n"
            f"Phone: {contact}\n"
            f"Email: {handle}\n\n"
            "Reach out directly — the bot does not forward messages on your behalf."
        )
        candidate.status = str(CandidateStatus.CONTACTED)
    await safe_reply(update, text, parse_mode="HTML")
    return ConversationHandler.END


async def _view(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: str, tutor_id: str
) -> int:
    with session_scope() as session:
        candidate = pipeline.candidate_for(session, job_id, tutor_id)
        if candidate is None:
            await safe_reply(update, "⚠️ Candidate not found.")
            return ConversationHandler.END
        tutor = candidate.tutor
        text = (
            f"👤 <b>{tutor.display_name}</b> (<code>{tutor.public_tutor_id}</code>)\n"
            f"Status: {tutor.status} • Match: {candidate.match_score:g}%\n"
            f"📍 Location: {', '.join(tutor.location_list) or tutor.city or '—'}\n"
            f"🎓 Grades: {', '.join(tutor.grade_list[:5]) or ', '.join(tutor.level_list[:5]) or '—'}\n"
            f"📚 Subjects: {', '.join(tutor.subject_list[:5]) or '—'}\n"
            f"🇬🇧 English: {tutor.english_proficiency or '—'}/10\n"
            f"🏫 {tutor.university or '—'}"
        )
    await safe_reply(update, text, parse_mode="HTML")
    return ConversationHandler.END


async def _documents(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: str, tutor_id: str
) -> int:
    with session_scope() as session:
        candidate = pipeline.candidate_for(session, job_id, tutor_id)
        if candidate is None:
            await safe_reply(update, "⚠️ Candidate not found.")
            return ConversationHandler.END
        docs = candidate.tutor.documents
        if not docs:
            await safe_reply(update, "No academic documents on file.")
            return ConversationHandler.END
        lines = [f"📄 Documents for <code>{tutor_id}</code>", ""]
        for doc in docs:
            lines.append(f"• {doc.document_type} — msg {doc.telegram_message_id or '?'}")
    await safe_reply(update, "\n".join(lines), parse_mode="HTML")
    return ConversationHandler.END


async def _voice(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: str, tutor_id: str
) -> int:
    with session_scope() as session:
        candidate = pipeline.candidate_for(session, job_id, tutor_id)
        if candidate is None:
            await safe_reply(update, "⚠️ Candidate not found.")
            return ConversationHandler.END
        tutor = candidate.tutor
        if not tutor.english_voice_file_id:
            await safe_reply(update, "No English voice recording on file.")
            return ConversationHandler.END
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                f"🎤 English voice introduction for <code>{tutor_id}</code>\n\n"
                f"Telegram file reference: <code>{tutor.english_voice_file_id}</code>\n"
                "Supporting evidence only — please review it yourself before relying on it."
            ),
            parse_mode="HTML",
        )
    return ConversationHandler.END


async def _shortlist_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: str
) -> int:
    with session_scope() as session:
        candidates = pipeline.get_job_candidates(session, job_id)
        if not candidates:
            await safe_reply(update, "No candidates to shortlist.")
            return ConversationHandler.END
        await safe_reply(
            update,
            "Pick a candidate to shortlist:",
            candidate_shortlist_keyboard(job_id, candidates),
        )
    return ConversationHandler.END


async def _rerun(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: str) -> int:
    with session_scope() as session:
        post = pipeline.get_job(session, job_id)
        if post is None:
            await safe_reply(update, "⚠️ Job not found.")
            return ConversationHandler.END
        results = pipeline.run_match_for_job(session, post)
        count = len(results)
    await safe_reply(
        update, f"🔄 Re-ran matching for <code>{job_id}</code> — {count} candidate(s).", parse_mode="HTML"
    )
    return ConversationHandler.END


async def _back_to_report(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: str
) -> int:
    await safe_reply(update, "Use /match <job_id> to view the full candidate list again.", parse_mode="HTML")
    return ConversationHandler.END


__all__ = ["build_match_handlers", "match_command", "match_callback"]