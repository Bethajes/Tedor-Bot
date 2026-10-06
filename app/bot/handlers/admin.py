"""Admin interface (Requirements 5.x, 9.x).

Every status changing action is gated by the configured admin Telegram IDs
(Requirements 5.6 and 9.1) — see :func:`is_admin` / :func:`require_admin`.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers.common import answer, safe_reply
from app.bot.keyboards.admin import (
    admin_menu_keyboard,
    job_list_keyboard,
    member_referral_keyboard,
    pending_tutors_keyboard,
    search_result_keyboard,
    status_button_keyboard,
    tutor_list_keyboard,
    tutor_status_filter_keyboard,
    verify_result_keyboard,
)
from app.bot.states import AdminStates
from app.config import settings
from app.database import session_scope
from app.enums import MemberReferralStatus, TutorFilters, TutorStatus
from app.models import SupportTicket, utcnow
from app.services import job_match_pipeline, notification_service, tutor_service
from app.services import tutor_profile_service
from app.services.storage_service import TelegramStorageService

logger = logging.getLogger(__name__)

ACCESS_DENIED = "⛔ Access denied — this command is restricted to Tedor Tutors administrators."


def access_denied_text(user_id: int | None) -> str:
    """Denial message that points at the number the operator needs to configure.

    Admin rights are keyed on the personal numeric Telegram ID, and the usual
    mistake is pasting a channel id (``-100…``). Echoing the caller's own ID
    makes the fix obvious without anyone having to ask.
    """
    if user_id is None:  # pragma: no cover - defensive
        return ACCESS_DENIED
    configured = sorted({int(item) for item in settings.admin_user_ids})
    shown = ", ".join(str(item) for item in configured) or "none"
    return (
        f"{ACCESS_DENIED}\n\n"
        f"Your Telegram user ID: <code>{int(user_id)}</code>\n"
        f"Currently configured admin IDs: <code>{shown}</code>\n"
        f"Add your ID to ADMIN_USER_IDS in .env, then restart the bot."
    )

SEARCH_KEY = "admin_search"
PENDING_INFO_KEY = "pending_admin_info"
PENDING_REJECT_KEY = "pending_reject_tdr"
PENDING_REASON_KEY = "pending_reject_reason"


# ---------------------------------------------------------------------------
# authorization
# ---------------------------------------------------------------------------


def is_admin(user_id: int | None) -> bool:
    """Requirement 5.6 / 9.1: whitelist check on the Telegram user ID."""
    if user_id is None:
        return False
    return int(user_id) in {int(item) for item in settings.admin_user_ids}


async def require_admin(update: Update) -> bool:
    """Send the denial message when the sender is not an admin."""
    user_id = getattr(update.effective_user, "id", None)
    if is_admin(user_id):
        return True
    logger.warning("Rejected unauthorised admin command attempt")
    text = access_denied_text(user_id)
    if update.effective_message is not None:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
    elif update.callback_query is not None:
        await update.callback_query.answer(
            "Access denied — admins only.", show_alert=True
        )
    return False


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_admin(update):
        return ConversationHandler.END
    await safe_reply(
        update,
        "🛠 <b>Tedor Tutors admin console</b>\n\nWhat would you like to do?",
        admin_menu_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return int(AdminStates.MENU)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Requirements 9.2 / 9.4."""
    if not await require_admin(update):
        return ConversationHandler.END
    await safe_reply(update, statistics_text(), parse_mode=ParseMode.HTML)
    return ConversationHandler.END


def statistics_text() -> str:
    """Section 28 counters, additive to the original view."""
    with session_scope() as session:
        stats = job_match_pipeline.platform_statistics(session)
    return (
        "📊 <b>Tedor Tutors statistics</b>\n\n"
        "<b>Tutors</b>\n"
        f"Total tutors: {stats['total_tutors']}\n"
        f"Pending: {stats.get('PENDING', 0)}\n"
        f"Documents submitted: {stats.get('DOCUMENTS_SUBMITTED', 0)}\n"
        f"Under review: {stats.get('UNDER_REVIEW', 0)}\n"
        f"Verified: {stats.get('VERIFIED', 0)}\n"
        f"Rejected: {stats.get('REJECTED', 0)}\n"
        f"Suspended: {stats.get('SUSPENDED', 0)}\n"
        f"Incomplete profiles: {stats['incomplete_profiles']}\n\n"
        "<b>Jobs &amp; matching</b>\n"
        f"Total job posts: {stats['total_job_posts']}\n"
        f"Open jobs: {stats['open_jobs']}\n"
        f"Recommended candidates: {stats['job_candidates']}\n"
        f"Shortlisted: {stats['shortlisted_candidates']}\n"
        f"Selected tutors: {stats['selected_tutors']}\n\n"
        "<b>Support</b>\n"
        f"Student requests: {stats['total_student_requests']}\n"
        f"Support tickets: {stats['total_support_tickets']}"
    )


# ---------------------------------------------------------------------------
# menu / listings
# ---------------------------------------------------------------------------


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_admin(update):
        return ConversationHandler.END

    await answer(update)
    data = update.callback_query.data or ""
    state = _current_state(context)

    if data == "admin:menu":
        await safe_reply(
            update,
            "🛠 <b>Admin console</b>\n\nWhat would you like to do?",
            admin_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return int(AdminStates.MENU)
    if data == "admin:stats":
        await safe_reply(update, statistics_text(), parse_mode=ParseMode.HTML)
        return int(AdminStates.MENU)
    if data == "admin:tutors":
        return await _show_tutors(update, context)
    if data.startswith("admin:tutors:filter:"):
        return await _show_tutors(update, context, data.rsplit(":", 1)[-1])
    if data == "admin:matches":
        return await _show_jobs(update, context)
    if data.startswith("admin:matches:"):
        return await _show_job_candidates(update, context, data.rsplit(":", 1)[-1])
    if data == "admin:verification":
        return await _show_pending(update, context)
    if data.startswith("admin:referral:"):
        _, _action, status, tdr_id = data.split(":")
        return await _set_member_referral(update, context, tdr_id, status)
    if data == "admin:pending":
        return await _show_pending(update, context)
    if data == "admin:search":
        return await _start_search(update, context)
    if data.startswith("admin:review:"):
        return await _show_tutor_detail(update, context, data.split(":")[-1])
    if data.startswith("admin:verify:"):
        return await _set_status(update, context, data.split(":")[-1], TutorStatus.VERIFIED)
    if data.startswith("admin:reject:"):
        return await _request_reject_reason(update, context, data.split(":")[-1])
    if data.startswith("admin:reject_confirm:"):
        reason = context.user_data.get(PENDING_REASON_KEY) or "Not specified"
        context.user_data.pop(PENDING_REASON_KEY, None)
        return await _set_status(
            update,
            context,
            data.split(":")[-1],
            TutorStatus.REJECTED,
            reason=reason,
        )
    if data.startswith("admin:under_review:"):
        return await _set_status(
            update, context, data.split(":")[-1], TutorStatus.UNDER_REVIEW
        )
    if data.startswith("admin:reopen:"):
        return await _set_status(update, context, data.split(":")[-1], TutorStatus.PENDING)
    if data.startswith("admin:request_info:"):
        return await _request_more_info(update, context, data.split(":")[-1])
    if data.startswith("admin:set:"):
        _, _action, status, tdr_id = data.split(":")
        return await _set_status(update, context, tdr_id, status)
    return state


def _current_state(context: ContextTypes.DEFAULT_TYPE) -> int:
    value = context.user_data.get("admin_state")
    return int(value) if value is not None else int(AdminStates.MENU)


async def _show_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Requirement 9.5."""
    with session_scope() as session:
        tutors = tutor_service.list_tutors(session, None)
        tutors = [
            tutor
            for tutor in tutors
            if tutor.status in (TutorStatus.PENDING, TutorStatus.UNDER_REVIEW)
        ]
        if not tutors:
            await safe_reply(
                update,
                "✅ No tutors are waiting for verification.",
                admin_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return int(AdminStates.MENU)
        lines = ["⏳ <b>Pending verification</b>", ""]
        for tutor in tutors:
            completion = tutor_profile_service.profile_completion_percentage(tutor)
            lines.append(
                f"• <code>{tutor.public_tutor_id}</code> — {tutor.display_name} "
                f"({tutor.status})\n"
                f"   📚 {', '.join(tutor.subject_list) or '—'} | "
                f"📍 {', '.join(tutor.location_list) or tutor.city or '—'} | "
                f"✅ {completion}% complete"
            )
    await safe_reply(
        update,
        "\n".join(lines),
        pending_tutors_keyboard(tutors),
        parse_mode=ParseMode.HTML,
    )
    return int(AdminStates.PENDING_LIST)


async def _show_tutors(
    update: Update, context: ContextTypes.DEFAULT_TYPE, status: str | None = None
) -> int:
    """The 👨‍🏫 Tutors dashboard section."""
    if status is None:
        await safe_reply(
            update,
            "👨‍🏫 <b>Tutors</b>\n\nFilter by status:",
            tutor_status_filter_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return int(AdminStates.MENU)

    with session_scope() as session:
        tutors = tutor_service.list_tutors(session, status)
        if not tutors:
            await safe_reply(
                update,
                f"No tutors with status <b>{status}</b>.",
                tutor_status_filter_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return int(AdminStates.MENU)
        lines = [f"👨‍🏫 <b>Tutors — {status}</b> ({len(tutors)})", ""]
        for tutor in tutors[:40]:
            completion = tutor_profile_service.profile_completion_percentage(tutor)
            lines.append(
                f"• <code>{tutor.public_tutor_id}</code> — {tutor.display_name} "
                f"✅ {completion}%"
            )
        if len(tutors) > 40:
            lines.append(f"\n<i>…and {len(tutors) - 40} more.</i>")
        keyboard = tutor_list_keyboard(tutors[:20])

    await safe_reply(update, "\n".join(lines), keyboard, parse_mode=ParseMode.HTML)
    return int(AdminStates.TUTOR_LIST)


async def _show_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """The 🎯 Job Matches dashboard section: open jobs and their candidates."""
    with session_scope() as session:
        posts = job_match_pipeline.open_jobs(session)
        if not posts:
            await safe_reply(
                update,
                "📭 There are no open jobs right now.",
                admin_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return int(AdminStates.MENU)
        lines = ["🎯 <b>Open jobs</b>", ""]
        for post in posts:
            count = len(job_match_pipeline.get_job_candidates(session, post.public_post_id))
            lines.append(
                f"• <code>{post.public_post_id}</code> — {post.student_level} "
                f"{post.subjects}\n"
                f"   📍 {post.location} | 💰 {post.compensation} | "
                f"👥 {count} candidate(s)"
            )
        keyboard = job_list_keyboard(posts[:20])

    await safe_reply(update, "\n".join(lines), keyboard, parse_mode=ParseMode.HTML)
    return int(AdminStates.JOB_MATCHES_LIST)


async def _show_job_candidates(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: str
) -> int:
    """The explainable candidate list for one job (section 17/18)."""
    with session_scope() as session:
        post = job_match_pipeline.get_job(session, job_id)
        if post is None:
            await safe_reply(update, "Job not found.", admin_menu_keyboard())
            return int(AdminStates.MENU)
        rows = job_match_pipeline.get_job_candidates(session, job_id)
        if not rows:
            await safe_reply(
                update,
                f"No candidates stored for <code>{job_id}</code> yet.\n\n"
                "Run <code>/match " + post.public_post_id + "</code> to score the "
                "verified tutors.",
                parse_mode=ParseMode.HTML,
            )
            return int(AdminStates.MENU)

        lines = [
            f"🎯 <code>{post.public_post_id}</code> — {post.student_level} "
            f"{post.subjects}",
            f"📍 {post.location} | 💰 {post.compensation}",
            "",
        ]
        for row in rows:
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(row.rank, "•")
            lines.append(
                f"{medal} <code>{row.tutor.public_tutor_id}</code> — "
                f"{row.match_score:g}% ({row.status})"
            )
            details = job_match_pipeline.candidate_details(row)
            for note in details.get("strengths", [])[:3]:
                lines.append(f"   ✓ {note}")
            for note in details.get("weaknesses", [])[:2]:
                lines.append(f"   ⚠ {note}")
            for note in details.get("missing", [])[:2]:
                lines.append(f"   ⚠ {note}")
            lines.append("")

    await safe_reply(
        update,
        "\n".join(lines).rstrip(),
        admin_menu_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return int(AdminStates.JOB_CANDIDATES)


async def _set_member_referral(
    update: Update, context: ContextTypes.DEFAULT_TYPE, tdr_id: str, status: str
) -> int:
    """Advance the separate 50-member onboarding condition (section 7).

    Admin-confirmed only. This never touches the verification status and never
    influences matching — it is an onboarding requirement, not a teaching
    quality signal.
    """
    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
        if tutor is None:
            await safe_reply(update, "Tutor not found.", admin_menu_keyboard())
            return int(AdminStates.MENU)
        try:
            tutor_profile_service.set_member_referral_status(
                session, tutor, status, verified_by=update.effective_user.id
            )
        except tutor_profile_service.ProfileValidationError as exc:
            await safe_reply(update, f"⚠️ {exc}", admin_menu_keyboard())
            return int(AdminStates.MENU)
        current = tutor.member_referral_status

    await safe_reply(
        update,
        f"✅ <code>{tdr_id}</code> 50-member requirement is now "
        f"<b>{current.replace('_', ' ').title()}</b>.\n"
        "<i>Recorded as an onboarding condition only — it does not affect "
        "verification or matching.</i>",
        member_referral_keyboard(tdr_id, current),
        parse_mode=ParseMode.HTML,
    )
    return int(AdminStates.REVIEW_DETAIL)


async def _show_tutor_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, tdr_id: str
) -> int:
    """The full admin view of one tutor, including what is still missing."""
    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
        if tutor is None:
            await safe_reply(update, "Tutor not found.", admin_menu_keyboard())
            return int(AdminStates.MENU)
        report = tutor_profile_service.completion_report(tutor)
        text = (
            f"👤 <b>{tutor.display_name}</b> (<code>{tutor.public_tutor_id}</code>)\n"
            f"Name: {tutor.name}\n"
            f"Status: <b>{tutor.status}</b>\n"
            f"Archive: {tutor.storage_status}\n"
            f"✅ Profile completeness: <b>{report['percentage']}%</b>\n\n"
            f"📍 {', '.join(tutor.location_list) or tutor.city or '—'}\n"
            f"📚 {', '.join(tutor.subject_list) or '—'}\n"
            f"🎓 {', '.join(tutor.grade_list) or ', '.join(tutor.level_list) or '—'}\n"
            f"🇬🇧 English: {f'{tutor.english_proficiency}/10' if tutor.english_proficiency is not None else '—'}"
            f"{' · 🎤 recording on file' if tutor.has_english_voice else ''}\n"
            f"🏫 {tutor.university or '—'}"
            f"{f' — {tutor.department}' if tutor.department else ''}\n"
            f"🎓 Entrance exam: "
            f"{f'{tutor.entrance_exam_score:g}/{tutor.entrance_exam_max_score:g}' if tutor.entrance_exam_score is not None else '—'}"
            f"{f' ({tutor.entrance_exam_type})' if tutor.entrance_exam_type else ''}\n"
            f"🧑‍🏫 {tutor.teaching_mode}"
            f"{f' · {tutor.teaching_experience_years} yr experience' if tutor.teaching_experience_years else ''}\n"
            f"📄 Documents: {len(tutor.documents)}\n"
            f"👥 50-member requirement: {tutor.member_referral_status.replace('_', ' ').title()}"
        )
        if report["missing_labels"]:
            text += "\n\n⚠ <b>Missing:</b>\n• " + "\n• ".join(report["missing_labels"])
        keyboard = status_button_keyboard(tdr_id)
        referral_keyboard = member_referral_keyboard(tdr_id, tutor.member_referral_status)

    await safe_reply(update, text, keyboard, parse_mode=ParseMode.HTML)
    await safe_reply(
        update,
        "👥 <b>50-member requirement</b>\n\n"
        "An onboarding condition you confirm separately. It is never used to "
        "verify a tutor or to rank them in matching.",
        referral_keyboard,
        parse_mode=ParseMode.HTML,
    )
    return int(AdminStates.REVIEW_DETAIL)


async def _set_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tdr_id: str,
    status: TutorStatus | str,
    reason: str | None = None,
) -> int:
    """Requirements 5.2, 5.3, 9.5 — status change + archive + notification."""
    if not await require_admin(update):
        return ConversationHandler.END

    try:
        value = TutorStatus(str(status).upper())
    except ValueError:
        await safe_reply(update, f"Unknown status: {status}", admin_menu_keyboard())
        return int(AdminStates.MENU)

    with session_scope() as session:
        tutor = tutor_service.update_tutor_status(session, tdr_id, value)
        if tutor is None:
            await safe_reply(update, "Tutor not found.", admin_menu_keyboard())
            return int(AdminStates.MENU)
        tutor_name = tutor.display_name
        etb_rate, usd_rate = tutor.etb_rate, tutor.usd_rate

    storage = TelegramStorageService(context.bot)
    await storage.update_tutor_record_status(tdr_id)

    if value == TutorStatus.VERIFIED:
        with session_scope() as session:
            tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
            if tutor is not None:
                await notification_service.notify_tutor_verified(context.bot, tutor)
                await _notify_strong_matches_for_new_tutor(session, context, tutor)
    elif value == TutorStatus.REJECTED:
        with session_scope() as session:
            tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
            if tutor is not None:
                await notification_service.notify_tutor_rejected(
                    context.bot, tutor, reason
                )
    elif value == TutorStatus.UNDER_REVIEW:
        with session_scope() as session:
            tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
            if tutor is not None:
                await notification_service.notify_tutor_under_review(context.bot, tutor)

    await safe_reply(
        update,
        f"✅ <code>{tdr_id}</code> ({tutor_name}) is now <b>{value}</b>.\n"
        f"ETB: {etb_rate} • USD: {usd_rate}",
        verify_result_keyboard(tdr_id),
        parse_mode=ParseMode.HTML,
    )
    return int(AdminStates.REVIEW_DETAIL)


async def _notify_strong_matches_for_new_tutor(
    session, context: ContextTypes.DEFAULT_TYPE, tutor
) -> None:
    """§22: with a new VERIFIED tutor, flag open jobs they match strongly.

    Deliberately a heads-up to the admin, not an automatic recommendation and
    never a match notification spam — only positions at or above the
    configured threshold are reported, and never to the candidate tutor.
    """
    from app.enums import CandidateStatus
    from app.models import JobCandidate
    from app.services import job_match_pipeline, matching_service

    open_jobs = job_match_pipeline.open_jobs(session)
    strong = matching_service.match_for_new_tutor(session, tutor, open_jobs)
    for post, result in strong:
        # Persist the relationship so any later shortlist/contact action works.
        existing = session.query(JobCandidate).filter_by(
            job_post_id=post.id, tutor_id=tutor.id
        ).one_or_none()
        if existing is None:
            from sqlalchemy import func

            max_rank = session.query(func.max(JobCandidate.rank)).filter_by(
                job_post_id=post.id
            ).scalar()
            session.add(
                JobCandidate(
                    job_post_id=post.id,
                    tutor_id=tutor.id,
                    match_score=float(result.percentage),
                    rank=(max_rank or 0) + 1,
                    status=str(CandidateStatus.RECOMMENDED),
                    details="",
                )
            )
        await notification_service.notify_admin_new_tutor_match(
            context.bot, tutor, post, result.percentage
        )
    session.flush()


async def _request_reject_reason(
    update: Update, context: ContextTypes.DEFAULT_TYPE, tdr_id: str
) -> int:
    context.user_data[PENDING_REJECT_KEY] = tdr_id
    await safe_reply(
        update,
        f"📝 Send the rejection reason for <code>{tdr_id}</code>. "
        "It will be delivered to the tutor.",
        parse_mode=ParseMode.HTML,
    )
    return int(AdminStates.REJECT_REASON)


async def _request_more_info(
    update: Update, context: ContextTypes.DEFAULT_TYPE, tdr_id: str
) -> int:
    """Requirement 5.4: the admin types a message that is sent to the tutor."""
    context.user_data[PENDING_INFO_KEY] = tdr_id
    await safe_reply(
        update,
        f"📩 Type the message you want to send to <code>{tdr_id}</code>. "
        "It will be delivered as Tedor Tutors.",
    )
    return int(AdminStates.REQUEST_INFO_INPUT)


# ---------------------------------------------------------------------------
# admin text input flows
# ---------------------------------------------------------------------------


async def reject_reason_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_admin(update):
        return ConversationHandler.END
    message_text = (update.effective_message.text or "").strip()
    if not message_text:
        await safe_reply(update, "⚠️ Please type a reason, or use /cancel.")
        return int(AdminStates.REJECT_REASON)
    tdr_id = context.user_data.pop(PENDING_REJECT_KEY, None)
    context.user_data[PENDING_REASON_KEY] = message_text
    if tdr_id is None:
        await safe_reply(update, "No rejection is pending.", admin_menu_keyboard())
        return int(AdminStates.MENU)
    return await _set_status(
        update, context, str(tdr_id), TutorStatus.REJECTED, reason=message_text
    )


async def request_info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_admin(update):
        return ConversationHandler.END
    message_text = (update.effective_message.text or "").strip()
    tdr_id = _tdr_from_pending(context, PENDING_INFO_KEY)
    if not message_text:
        await safe_reply(update, "⚠️ Please type the message to send.")
        return int(AdminStates.REQUEST_INFO_INPUT)
    if tdr_id is None:
        await safe_reply(update, "No tutor is selected.", admin_menu_keyboard())
        return int(AdminStates.MENU)

    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
        if tutor is None:
            await safe_reply(update, "Tutor not found.", admin_menu_keyboard())
            return int(AdminStates.MENU)
        sent = await notification_service.request_more_info(context.bot, tutor, message_text)
    if sent:
        await safe_reply(update, f"✅ Message sent to <code>{tdr_id}</code>.", admin_menu_keyboard(), parse_mode=ParseMode.HTML)
    else:
        await safe_reply(update, "⚠️ The tutor could not be reached. Please try again.")
    return int(AdminStates.MENU)


def _tdr_from_pending(context: ContextTypes.DEFAULT_TYPE, key: str) -> str | None:
    value = context.user_data.get(key)
    return str(value) if value else None


# ---------------------------------------------------------------------------
# search flow (Requirement 9.3)
# ---------------------------------------------------------------------------


async def _start_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data[SEARCH_KEY] = {"subject": None, "country": None, "status": None}
    await safe_reply(
        update,
        "🔎 <b>Search tutors</b>\n\nSend the subject name (or - to skip):",
    )
    return int(AdminStates.SEARCH_SUBJECT)


async def search_subject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = (update.effective_message.text or "").strip()
    search = context.user_data.setdefault(SEARCH_KEY, {})
    search["subject"] = None if value == "-" else value
    await safe_reply(update, "🌍 Send the country (or - to skip):")
    return int(AdminStates.SEARCH_COUNTRY)


async def search_country_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = (update.effective_message.text or "").strip()
    search = context.user_data.setdefault(SEARCH_KEY, {})
    search["country"] = None if value == "-" else value
    await safe_reply(
        update,
        "🏷 Send the status (PENDING, UNDER_REVIEW, VERIFIED, REJECTED, SUSPENDED or - to skip):",
    )
    return int(AdminStates.SEARCH_STATUS)


async def search_status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = (update.effective_message.text or "").strip()
    search = context.user_data.setdefault(SEARCH_KEY, {})
    status = None if value == "-" else value.upper()
    if status and status not in {item.value for item in TutorStatus}:
        await safe_reply(
            update, f"'{value}' is not a valid status. Send PENDING, VERIFIED… or - to skip."
        )
        return int(AdminStates.SEARCH_STATUS)
    search["status"] = status

    with session_scope() as session:
        tutors = tutor_service.search_tutors(session, TutorFilters(**search))
        if not tutors:
            await safe_reply(
                update,
                "🔎 No tutors matched. Try a different search.",
                admin_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return int(AdminStates.MENU)
        lines = ["🔎 <b>Search results</b>", ""]
        for tutor in tutors[:20]:
            lines.append(
                f"• <code>{tutor.public_tutor_id}</code> — {tutor.display_name} "
                f"({tutor.status}) • {tutor.country}"
            )
    await safe_reply(
        update,
        "\n".join(lines),
        search_result_keyboard(tutors[:20]),
        parse_mode=ParseMode.HTML,
    )
    return int(AdminStates.SEARCH_RESULTS)


# ---------------------------------------------------------------------------
# inbound relays
# ---------------------------------------------------------------------------


async def tutor_reply_relay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Requirement 5.5: forward a tutor's reply to the admin chat."""
    message = update.effective_message
    if message is None or not getattr(message, "reply_to_message", None):
        return
    replied = message.reply_to_message
    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_telegram_id(session, update.effective_user.id)
        if tutor is None:
            return
        try:
            await context.bot.send_message(
                chat_id=notification_service.admin_chat_id(),
                text=(
                    "↩️ <b>Tutor reply</b>\n"
                    f"Reference: <code>{tutor.public_tutor_id}</code>\n"
                    f"Message: {message.text or ''}"
                ),
                parse_mode=ParseMode.HTML,
                reply_to_message_id=replied.message_id,
            )
        except TelegramError:
            logger.error("Could not forward the tutor reply to the admin chat")


async def support_reply_relay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
                    f"💬 <b>Tedor Tutors reply</b> (<code>{ticket.public_ticket_id}</code>)\n\n"
                    f"{message.text or ''}"
                ),
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            logger.error("Could not relay the support reply")
            return
        ticket.status = "ANSWERED"
        ticket.updated_at = utcnow()


# ---------------------------------------------------------------------------
# conversation wiring
# ---------------------------------------------------------------------------


def build_admin_conversation() -> ConversationHandler:
    states: dict[int, list[Any]] = {
        int(AdminStates.MENU): [CallbackQueryHandler(callback_handler)],
        int(AdminStates.PENDING_LIST): [CallbackQueryHandler(callback_handler)],
        int(AdminStates.TUTOR_LIST): [CallbackQueryHandler(callback_handler)],
        int(AdminStates.JOB_MATCHES_LIST): [CallbackQueryHandler(callback_handler)],
        int(AdminStates.JOB_CANDIDATES): [CallbackQueryHandler(callback_handler)],
        int(AdminStates.SEARCH_RESULTS): [CallbackQueryHandler(callback_handler)],
        int(AdminStates.REVIEW_DETAIL): [CallbackQueryHandler(callback_handler)],
        int(AdminStates.REJECT_REASON): [
            CallbackQueryHandler(callback_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, reject_reason_handler),
        ],
        int(AdminStates.REQUEST_INFO_INPUT): [
            CallbackQueryHandler(callback_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, request_info_handler),
        ],
        int(AdminStates.SEARCH_SUBJECT): [
            MessageHandler(filters.TEXT & ~filters.COMMAND, search_subject_handler)
        ],
        int(AdminStates.SEARCH_COUNTRY): [
            MessageHandler(filters.TEXT & ~filters.COMMAND, search_country_handler)
        ],
        int(AdminStates.SEARCH_STATUS): [
            MessageHandler(filters.TEXT & ~filters.COMMAND, search_status_handler)
        ],
    }
    return ConversationHandler(
        entry_points=[
            CommandHandler("admin", admin_command),
            CommandHandler("stats", stats_command),
            CommandHandler("search_tutor", _start_search),
        ],
        states=states,
        fallbacks=[
            CallbackQueryHandler(callback_handler),
            MessageHandler(filters.COMMAND, lambda *_: ConversationHandler.END),
        ],
        name="admin",
        allow_reentry=True,
    )


__all__ = [
    "ACCESS_DENIED",
    "admin_command",
    "build_admin_conversation",
    "callback_handler",
    "is_admin",
    "require_admin",
    "statistics_text",
    "stats_command",
]