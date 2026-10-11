"""The "Apply for This Job" workflow (specification sections 2-9).

Reached three ways, all of which land in the same place:

* the ``t.me`` deep link on the Apply button under a published job post;
* a ``japply:`` callback button sent inside the private chat;
* an admin re-opening a submitted application from the console.

What pressing Apply does, in order:

1. The job reference is validated and looked up on the server. Nothing in the
   callback data or the deep link is trusted as an identity — the tutor always
   comes from the Telegram user id.
2. The job's status is checked. A draft or closed post cannot be applied to.
3. Interest is recorded (:func:`~app.services.job_application_service.
   start_or_get_application`), which is idempotent: pressing Apply five times
   still yields one row.
4. The tutor's stored profile is assessed by
   :mod:`app.services.profile_completion_service`. A never-applied tutor enters
   the existing application form; an incomplete one enters the existing
   completion flow and is asked only for what is actually missing; a complete
   one goes straight to evaluation.
5. The tutor is evaluated against this specific job and shown a personalised,
   explained recommendation.
6. Only an explicit "Continue Application" confirms the submission.

Applying and submitting are deliberately different actions. The first says "I
am interested"; the second says "submit my candidacy for this job", and it is
the only thing that sets ``confirmed_submitted`` or notifies the admin.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, ContextTypes

from app.bot.handlers.common import (
    answer,
    html_escape,
    resolve_bot_username,
    safe_reply,
)
from app.bot.keyboards.job_apply import (
    CB_BACK,
    CB_CONTINUE,
    CB_GO,
    CB_PREFIX,
    CB_PROFILE,
    CB_STATUS,
    CB_WITHDRAW,
    apply_intro_keyboard,
    existing_application_keyboard,
    parse_callback_data,
    recommendation_keyboard,
)
from app.database import session_scope
from app.enums import JobApplicationStatus
from app.models import JobApplication
from app.services import job_application_service as applications
from app.services import notification_service, profile_completion_service, tutor_service
from app.services.job_post_service import JOB_POST_PATTERN

logger = logging.getLogger(__name__)

#: Admin actions reachable from :func:`admin_application_keyboard`.
ADMIN_ACTIONS: dict[str, JobApplicationStatus] = {
    "review": JobApplicationStatus.UNDER_REVIEW,
    "shortlist": JobApplicationStatus.SHORTLISTED,
    "reject": JobApplicationStatus.REJECTED,
}


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


async def apply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Every ``japply:*`` callback from the private chat."""
    await answer(update)
    parsed = parse_callback_data(update.callback_query.data or "")
    if parsed is None:  # pragma: no cover - the pattern already filtered these
        return
    action, job_reference = parsed

    # A press that arrives on the channel itself is handled as a hand-off to
    # the private chat, never as the workflow: nothing personal may be posted
    # where everyone can read it.
    if _from_channel(update):
        await _hand_off_from_channel(update, context, job_reference)
        return

    if action == CB_GO:
        await handle_apply(update, context, job_reference)
    elif action == CB_CONTINUE:
        await confirm_submission(update, context, job_reference)
    elif action == CB_BACK:
        await show_job(update, context, job_reference)
    elif action == CB_PROFILE:
        await update_profile(update, context, job_reference)
    elif action == CB_STATUS:
        await show_application_status(update, context, job_reference)
    elif action == CB_WITHDRAW:
        await withdraw_application(update, context, job_reference)
    elif action == "admin":
        await admin_application_callback(update, context, update.callback_query.data or "")


async def apply_deep_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle ``/start japply_JOB-000123``. Returns True when it was ours."""
    from app.bot.keyboards.job_apply import parse_start_payload

    args = context.args or []
    if not args:
        return False
    job_reference = parse_start_payload(args[0])
    if not job_reference:
        return False
    await handle_apply(update, context, job_reference)
    return True


def build_job_apply_handlers() -> list[Any]:
    """Handlers for the ``japply:`` namespace.

    Registered after every conversation but before the admin console, so this
    namespace is claimed here rather than absorbed by the admin handler's
    catch-all callback pattern.
    """
    return [CallbackQueryHandler(apply_callback, pattern=rf"^{CB_PREFIX}")]


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


def _from_channel(update: Update) -> bool:
    """Whether this update's button was pressed on a channel post."""
    query = getattr(update, "callback_query", None)
    chat = getattr(getattr(query, "message", None), "chat", None)
    return str(getattr(chat, "type", "")) == "channel"


def _invalid_reference(job_reference: str) -> bool:
    """Whether a job reference is not even shaped like one.

    Matched against the *raw* string on purpose. ``JOB_POST_PATTERN`` only
    accepts the canonical upper-case form, so a crafted ``job-000001`` is
    refused here rather than being normalised into a lookup for a job the tutor
    did not name. Shape is only a cheap filter though — the job is still looked
    up afterwards, and "no such job" stays a separate case.

    It is also a directory-traversal guard: ``rsplit`` on an arbitrary string
    would otherwise reach into unrelated keys.
    """
    return not JOB_POST_PATTERN.match(str(job_reference or "").strip())


async def _resolve_job_or_complain(update: Update, job_reference: str) -> Any:
    """Look the job up, explaining a bad or unknown reference to the tutor.

    Returns ``None`` after replying, so every caller can simply return.
    """
    if _invalid_reference(job_reference):
        await safe_reply(
            update,
            "⚠️ That job link is not valid. Please use the Apply button on the "
            "job post, or ask our team for the current openings.",
        )
        return None

    reference = str(job_reference).strip().upper()
    with session_scope() as session:
        post = applications.get_job(session, reference)
        is_open = post is not None and post.is_open and str(post.status) == "PUBLISHED"
        job_line = _job_line(post) if post is not None else reference

    if not is_open:
        await safe_reply(
            update,
            f"⚠️ <b>{html_escape(job_line)}</b> is not accepting applications.\n\n"
            "This opening has been closed or is no longer published. Our channel "
            "always carries the current opportunities.",
            parse_mode=ParseMode.HTML,
        )
        return None
    return reference


def _job_line(post: Any) -> str:
    """One-line description of a job, for a message header."""
    return (
        f"{post.public_post_id} — {post.student_level} {post.subjects} "
        f"in {post.location}"
    )


# ---------------------------------------------------------------------------
# the workflow
# ---------------------------------------------------------------------------


async def handle_apply(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_reference: str
) -> None:
    """Press Apply: validate, record interest, then route on the profile state.

    The application row is created *before* the profile is inspected, so a tutor
    who abandons the form halfway still has the row waiting for them when they
    come back. The ORM objects do not outlive the session that loaded them: the
    routing decisions are carried out as plain values, and the interest
    notification re-opens its own session.
    """
    reference = await _resolve_job_or_complain(update, job_reference)
    if reference is None:
        return

    telegram_id = update.effective_user.id
    try:
        with session_scope() as session:
            post = applications.get_job(session, reference)
            tutor = tutor_service.get_tutor_by_telegram_id(session, telegram_id)
            if post is None:  # pragma: no cover - deleted between calls
                return
            created = False
            already = None
            if tutor is not None:
                application, created = applications.start_or_get_application(
                    session, tutor, post
                )
                if applications.is_terminal(application.status) or (
                    application.confirmed_submitted
                ):
                    already = _application_state(application)
            assessment = profile_completion_service.assess(tutor)
            job_line = _job_line(post)
    except SQLAlchemyError:
        logger.exception("Could not record the application for %s", reference)
        await safe_reply(
            update,
            "⚠️ We could not start your application just now. Please try again "
            "in a moment — nothing was saved.",
        )
        return

    if created:
        await _notify_new_interest(context.bot, reference, telegram_id)

    if already is not None:
        await _show_existing(update, context, reference, already)
        return

    await _route_by_profile(update, context, reference, job_line, assessment)


def _application_state(application: Any) -> dict[str, Any]:
    """Everything the notifications and the review view need, as plain values.

    Reading it off the ORM object here — while the session is still open — keeps
    the object from being touched after that session is closed, where a lazy
    relationship would raise.
    """
    return {
        "id": application.id,
        "job_id": application.job_id,
        "tdr_id": application.public_tutor_id,
        "status": str(application.status),
        "match_score": float(application.match_score or 0.0),
        "confirmed_submitted": bool(application.confirmed_submitted),
        "recommendation": application.recommendation or "",
        "submitted_at": application.submitted_at,
        "display_name": application.tutor.display_name,
        "tutor_status": str(application.tutor.status),
        "telegram_user_id": int(application.tutor.telegram_user_id),
    }


async def _notify_new_interest(bot: Any, reference: str, telegram_id: int) -> None:
    """Tell the admin about a *new* press, exactly once per job and tutor.

    The caller only invokes this when it created the row, so repeated presses
    are silent.
    """
    try:
        with session_scope() as session:
            post = applications.get_job(session, reference)
            tutor = tutor_service.get_tutor_by_telegram_id(session, telegram_id)
            if post is None or tutor is None:  # pragma: no cover - defensive
                return
            await notification_service.notify_admin_job_apply_interest(
                bot, tutor, post
            )
    except Exception:  # pragma: no cover - never block the tutor's flow
        logger.exception("Interest notification failed for %s", reference)


async def _route_by_profile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    reference: str,
    job_line: str,
    assessment: Any,
) -> None:
    """Send the tutor to the right next step for their stored profile."""
    from app.bot.handlers.tutor_onboarding import (
        set_resume_job,
        start_application,
    )

    if assessment.state is profile_completion_service.ProfileState.NOT_STARTED:
        set_resume_job(context, reference)
        await safe_reply(
            update,
            f"👋 <b>Applying for {html_escape(job_line)}</b>\n\n"
            "Before we can assess you for this job we need your tutor profile. "
            "It takes about three minutes and you will only be asked for things "
            "we do not already have.",
            parse_mode=ParseMode.HTML,
        )
        await start_application(update, context)
        return

    if assessment.is_blocked:
        await safe_reply(
            update,
            f"⛔ <b>Cannot apply for {html_escape(job_line)}</b>\n\n"
            f"{html_escape(assessment.reason)}\n\n"
            "If you think this is a mistake, contact us with /support and we "
            "will take another look.",
            parse_mode=ParseMode.HTML,
        )
        return

    if assessment.needs_input:
        await _announce_incomplete(
            update, context, reference, job_line, assessment, update.effective_user.id
        )
        return

    # Complete, or awaiting verification with nothing outstanding: evaluate.
    await show_recommendation(update, context, reference)


async def _announce_incomplete(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    reference: str,
    job_line: str,
    assessment: profile_completion_service.ProfileAssessment,
    telegram_id: int,
) -> None:
    """Name exactly what is missing, then enter the existing completion flow."""
    from app.bot.handlers.tutor_onboarding import start_profile_completion

    lines = [
        "✏️ <b>Your tutor profile is almost complete</b>",
        "",
        f"To evaluate you for <b>{html_escape(job_line)}</b> we still need:",
        "",
    ]
    lines.extend(f"• {html_escape(item)}" for item in assessment.outstanding)
    lines.extend(
        f"• {profile_completion_service.document_label(kind)}"
        for kind in assessment.missing_documents
    )
    lines.extend(
        [
            "",
            "You are at <b>{}%</b>. We will only ask for what is missing — "
            "everything you have already sent stays on your profile.".format(
                assessment.percentage
            ),
        ]
    )
    await safe_reply(update, "\n".join(lines), parse_mode=ParseMode.HTML)
    await _mark_profile_incomplete(reference, telegram_id)
    await start_profile_completion(update, context, reference)


async def _mark_profile_incomplete(reference: str, telegram_id: int) -> None:
    """Record that this applicant still owes profile information.

    Never downgrades a submitted application: once the tutor has confirmed, a
    later change to their profile does not drag the application backwards.
    """
    try:
        with session_scope() as session:
            tutor = tutor_service.get_tutor_by_telegram_id(session, telegram_id)
            application = (
                applications.get_application(session, reference, tutor)
                if tutor is not None
                else None
            )
            if application is not None:
                applications.mark_profile_incomplete(session, application)
    except SQLAlchemyError:  # pragma: no cover - never block the tutor's flow
        logger.exception("Could not mark %s profile-incomplete", reference)


async def show_recommendation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_reference: str
) -> None:
    """Evaluate the tutor for this job and show the explained recommendation."""
    try:
        with session_scope() as session:
            post = applications.get_job(session, job_reference)
            tutor = tutor_service.get_tutor_by_telegram_id(
                session, update.effective_user.id
            )
            if post is None or tutor is None:
                await safe_reply(
                    update,
                    "⚠️ We could not find that job or your profile. Please press "
                    "Apply on the job post again.",
                )
                return

            blocked = applications.hard_block(tutor, post)
            if blocked:
                await safe_reply(
                    update,
                    f"⛔ <b>{html_escape(blocked)}</b>",
                    parse_mode=ParseMode.HTML,
                )
                return

            application, _ = applications.start_or_get_application(
                session, tutor, post
            )
            evaluation = applications.evaluate_and_store(
                session, tutor, post, application
            )
            score = evaluation.percentage
            text = evaluation.text
    except SQLAlchemyError:
        logger.exception("Could not evaluate the application for %s", job_reference)
        await safe_reply(
            update,
            "⚠️ We could not work out your fit for this job just now. Please "
            "try again in a moment — your application has not been submitted.",
        )
        return

    await safe_reply(
        update,
        f"{text}\n\n<b>Fit score: {score:.0f}%</b>\n"
        "Would you like to continue and submit your application for this job?",
        recommendation_keyboard(job_reference),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# confirming, backing out, editing
# ---------------------------------------------------------------------------


async def confirm_submission(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_reference: str
) -> None:
    """The explicit "Continue Application" — the only thing that submits."""
    reference = await _resolve_job_or_complain(update, job_reference)
    if reference is None:
        return

    # Re-validate everything on the server: the button is a hint, not proof.
    telegram_id = update.effective_user.id
    try:
        with session_scope() as session:
            post = applications.get_job(session, reference)
            tutor = tutor_service.get_tutor_by_telegram_id(session, telegram_id)
            if post is None or tutor is None:
                await safe_reply(
                    update,
                    "⚠️ We could not find that job or your profile.",
                )
                return

            application = applications.get_application(session, reference, tutor)
            if application is None:
                application, _ = applications.start_or_get_application(
                    session, tutor, post
                )

            assessment = profile_completion_service.assess(tutor)
            if assessment.needs_input:
                await _announce_incomplete(
                    update,
                    context,
                    reference,
                    _job_line(post),
                    assessment,
                    telegram_id,
                )
                return
            if assessment.is_blocked:
                await safe_reply(
                    update,
                    f"⛔ <b>{html_escape(assessment.reason)}</b>",
                    parse_mode=ParseMode.HTML,
                )
                return

            blocked = applications.hard_block(tutor, post)
            if blocked:
                await safe_reply(
                    update, f"⛔ <b>{html_escape(blocked)}</b>", parse_mode=ParseMode.HTML
                )
                return

            # Re-score at confirmation time: the tutor may have edited their
            # profile between seeing the recommendation and pressing the button.
            evaluation = applications.evaluate_and_store(
                session, tutor, post, application
            )
            application, confirmed = applications.submit(session, application)
            score = evaluation.percentage
            job_line = _job_line(post)
            state = _application_state(application)
    except SQLAlchemyError:
        logger.exception("Could not submit the application for %s", reference)
        await safe_reply(
            update,
            "⚠️ We could not submit your application. Please try again — "
            "nothing was submitted and no duplicate was created.",
        )
        return

    if confirmed:
        await notification_service.notify_admin_job_application(context.bot, state)
        await notification_service.notify_tutor_application_submitted(
            context.bot,
            state["tdr_id"],
            state["telegram_user_id"],
            state,
        )

    await safe_reply(
        update,
        "✅ <b>Your application has been submitted.</b>\n\n"
        f"Job: {html_escape(job_line)}\n"
        f"Fit score: <b>{score:.0f}%</b>\n\n"
        "Our team reviews every application. Submitting is not a guarantee of "
        "an interview, and you will hear from us either way.",
        parse_mode=ParseMode.HTML,
    )


async def _show_existing(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    reference: str,
    state: dict[str, Any],
) -> None:
    """Tell a tutor who already applied what their status is."""
    label = applications.status_label(state["status"])
    confirmed = bool(state["confirmed_submitted"])
    lines = [
        "📌 <b>You have already applied for this job</b>",
        "",
        f"Status: <b>{html_escape(label)}</b>",
    ]
    if state["match_score"]:
        lines.append(f"Fit score: <b>{100 * state['match_score']:.0f}%</b>")
    lines.append("")
    if confirmed:
        lines.append(
            "Your candidacy is with our team and you do not need to do anything "
            "else. Use /support if you need to add information."
        )
    else:
        lines.append(
            "Your interest is recorded but you have not submitted your "
            "application yet."
        )
    await safe_reply(
        update,
        "\n".join(lines),
        existing_application_keyboard(reference, state["status"], confirmed=confirmed),
        parse_mode=ParseMode.HTML,
    )


async def show_application_status(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_reference: str
) -> None:
    """Show the current application for a job, if there is one."""
    reference = await _resolve_job_or_complain(update, job_reference)
    if reference is None:
        return
    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_telegram_id(
            session, update.effective_user.id
        )
        application = (
            applications.get_application(session, reference, tutor)
            if tutor is not None
            else None
        )
    if application is None:
        await safe_reply(
            update,
            f"You have not applied for <code>{html_escape(reference)}</code> yet.",
            parse_mode=ParseMode.HTML,
        )
        return
    await _show_existing(update, context, reference, application)


async def show_job(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_reference: str
) -> None:
    """"Back to Job": restate the opening so the tutor can re-read it."""
    reference = str(job_reference or "").strip().upper()
    if _invalid_reference(reference):
        await safe_reply(update, "⚠️ That job link is not valid.")
        return
    with session_scope() as session:
        post = applications.get_job(session, reference)
        text = post.generated_text if post is not None else ""
        status = str(post.status) if post is not None else "UNKNOWN"
        job_line = _job_line(post) if post is not None else reference
    if not text:
        await safe_reply(
            update, f"⚠️ We can no longer find <code>{html_escape(reference)}</code>."
        )
        return

    from app.templates.tutor_job_post import to_telegram_html

    header = (
        f"📌 <b>{html_escape(job_line)}</b>\nStatus: {html_escape(status)}\n\n"
        if status != "PUBLISHED"
        else f"📌 <b>{html_escape(job_line)}</b>\n\n"
    )
    await safe_reply(
        update,
        header + to_telegram_html(text),
        recommendation_keyboard(reference),
        parse_mode=ParseMode.HTML,
    )


async def update_profile(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_reference: str
) -> None:
    """"Update My Profile": enter the completion flow, scoped to this job."""
    reference = await _resolve_job_or_complain(update, job_reference)
    if reference is None:
        return
    from app.bot.handlers.tutor_onboarding import start_profile_completion

    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_telegram_id(
            session, update.effective_user.id
        )
    if tutor is None:
        from app.bot.handlers.tutor_onboarding import (
            set_resume_job,
            start_application,
        )

        set_resume_job(context, reference)
        await safe_reply(
            update,
            "You will need a tutor profile before you can apply. Let's start one.",
        )
        await start_application(update, context)
        return
    await start_profile_completion(update, context, reference)


async def withdraw_application(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_reference: str
) -> None:
    """The tutor pulls out of a job they applied for."""
    reference = str(job_reference or "").strip().upper()
    if _invalid_reference(reference):
        await safe_reply(update, "⚠️ That job link is not valid.")
        return
    try:
        with session_scope() as session:
            tutor = tutor_service.get_tutor_by_telegram_id(
                session, update.effective_user.id
            )
            application = (
                applications.get_application(session, reference, tutor)
                if tutor is not None
                else None
            )
            if application is None:
                await safe_reply(update, "There is no application to withdraw.")
                return
            applications.withdraw(session, application)
    except SQLAlchemyError:
        logger.exception("Could not withdraw the application for %s", reference)
        await safe_reply(
            update, "⚠️ We could not withdraw your application. Please try again."
        )
        return
    await safe_reply(
        update,
        f"✅ Your application for <code>{html_escape(reference)}</code> has been "
        "withdrawn. You can apply for it again at any time.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# channel hand-off and resume
# ---------------------------------------------------------------------------


async def _hand_off_from_channel(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_reference: str
) -> None:
    """A press on the channel post: continue in private, expose nothing here.

    The callback cannot edit the channel message reliably — the bot may not be an
    administrator, and any edit would race other readers — so the only thing
    that happens on the channel is the acknowledgement spinner.
    """
    username = await resolve_bot_username(context.bot, context)
    await safe_reply(
        update,
        "💬 <b>Continue in our private chat</b>\n\n"
        "Applications are handled privately so your details are never posted in "
        "a public channel. Tap the button below and we will pick up exactly "
        "where you left off.",
        apply_intro_keyboard(job_reference, username),
        parse_mode=ParseMode.HTML,
    )


async def resume_after_profile(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_reference: str
) -> int:
    """Return a tutor to the job they applied for once their profile is saved.

    Called by the onboarding flow's submission handlers, which read the pending
    job reference out of ``user_data`` before clearing their run.
    """
    reference = await _resolve_job_or_complain(update, job_reference)
    if reference is None:
        return -1
    await show_recommendation(update, context, reference)
    return -1


# ---------------------------------------------------------------------------
# admin review
# ---------------------------------------------------------------------------


async def admin_application_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: str
) -> None:
    """``japply:admin:<action>:<application id>`` — admin-only status moves."""
    from app.bot.handlers.admin import require_admin

    if not await require_admin(update):
        return

    parts = data.split(":")
    action = parts[2] if len(parts) > 2 else ""
    new_status = ADMIN_ACTIONS.get(action)
    if new_status is None:
        await safe_reply(update, "⚠️ Unknown action.")
        return
    try:
        application_id = int(parts[3])
    except (IndexError, TypeError, ValueError):
        await safe_reply(update, "⚠️ Unknown application.")
        return

    with session_scope() as session:
        application = session.get(JobApplication, application_id)
        if application is None:
            await safe_reply(update, "⚠️ That application no longer exists.")
            return
        applications.set_status(session, application, new_status)
        tdr_id = application.public_tutor_id
        job_reference = application.job_id
        label = applications.status_label(new_status)

    await safe_reply(
        update,
        f"✅ <code>{html_escape(tdr_id)}</code> — application for "
        f"<code>{html_escape(job_reference)}</code> marked <b>{html_escape(label)}</b>.",
        parse_mode=ParseMode.HTML,
    )
    await notification_service.notify_tutor_application_status(
        context.bot, tdr_id, job_reference, new_status
    )


__all__ = [
    "ADMIN_ACTIONS",
    "admin_application_callback",
    "apply_callback",
    "apply_deep_link",
    "build_job_apply_handlers",
    "confirm_submission",
    "handle_apply",
    "resume_after_profile",
    "show_application_status",
    "show_job",
    "show_recommendation",
    "update_profile",
    "withdraw_application",
]