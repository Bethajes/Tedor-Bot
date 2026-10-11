"""Admin job-post generator.

Flow: ``/post`` collects exactly seven variables, renders the static template,
shows a preview with Edit / Cancel / Publish, and publishes only when the admin
taps Publish.

The Requirements block is never collected, never rendered from a placeholder and
never editable — it lives in :mod:`app.templates.tutor_job_post`.
"""

from __future__ import annotations

import logging
from typing import Any

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

from app.bot.handlers.admin import require_admin
from app.bot.handlers.common import (
    Step,
    ValidationError,
    answer,
    current_step,
    end_flow,
    safe_reply,
    set_step,
    start_flow,
    validate_text,
)
from app.bot.keyboards.job_post import (
    CB_BACK,
    CB_CANCEL,
    CB_EDIT,
    CB_PREVIEW,
    CB_PUBLISH,
    EDITABLE_FIELDS,
    post_choice_keyboard,
    post_edit_keyboard,
    post_nav_keyboard,
    post_preview_keyboard,
)
from app.bot.options import (
    JOB_POST_DURATIONS,
    JOB_POST_FREETEXT_HINT,
    JOB_POST_FREQUENCIES,
    JOB_POST_LOCATIONS,
    JOB_POST_STUDENT_LEVELS,
    JOB_POST_SUBJECTS,
    JOB_POST_TUTOR_CATEGORIES,
)
from app.bot.states import JobPostStates
from app.config import settings
from app.database import session_scope
from app.services import job_match_pipeline, job_post_service, notification_service
from app.templates.tutor_job_post import (
    JobPostTemplateError,
    location_hashtag,
    render_job_post,
    to_telegram_html,
)

logger = logging.getLogger(__name__)

DATA_KEY = "job_post"
STEP_KEY = "job_post_step"
EDITING_KEY = "job_post_editing"

FIELD_LABELS: dict[str, str] = dict(EDITABLE_FIELDS)

#: The seven collected variables. No requirements step exists.
STEPS: tuple[Step, ...] = (
    Step(
        JobPostStates.LOCATION,
        "location",
        "📍 <b>Step 1/7 — Location</b>\n\n"
        "Where is the tutoring opportunity?\n"
        "Send the place name, e.g. <code>Ayat Tsebel</code>.\n\n"
        "<i>I ask once and reuse it everywhere in the post.</i>",
        kind="location",
    ),
    Step(
        JobPostStates.STUDENT_LEVEL,
        "student_level",
        "🎓 <b>Step 2/7 — Student's Level</b>\n\nWhich grade or level is the student in?",
        kind="choice",
        options=JOB_POST_STUDENT_LEVELS,
    ),
    Step(
        JobPostStates.TUTOR_CATEGORY,
        "tutor_category",
        "👥 <b>Step 3/7 — Tutor Category</b>\n\nWho can apply for this post?",
        kind="choice",
        options=JOB_POST_TUTOR_CATEGORIES,
    ),
    Step(
        JobPostStates.DURATION,
        "duration",
        "⏱ <b>Step 4/7 — Duration</b>\n\n"
        f"How long per day? e.g. <code>1 hour/day</code>. {JOB_POST_FREETEXT_HINT}",
        kind="choice",
        options=JOB_POST_DURATIONS,
    ),
    Step(
        JobPostStates.FREQUENCY,
        "frequency",
        "🗓 <b>Step 5/7 — Frequency</b>\n\n"
        f"How many days per week? e.g. <code>3 days/week</code>. {JOB_POST_FREETEXT_HINT}",
        kind="choice",
        options=JOB_POST_FREQUENCIES,
    ),
    Step(
        JobPostStates.SUBJECTS,
        "subjects",
        "📚 <b>Step 6/7 — Target Subjects</b>\n\n"
        "Which subjects? Pick <code>Major Subjects</code> or name your own — "
        "I build the subject hashtags automatically.",
        kind="choice",
        options=JOB_POST_SUBJECTS,
    ),
    Step(
        JobPostStates.COMPENSATION,
        "compensation",
        "💰 <b>Step 7/7 — Compensation</b>\n\n"
        "What is the pay? e.g. <code>300 Birr / 1 hour</code>.",
        kind="text",
    ),
)

STEPS_BY_STATE = {int(step.state): step for step in STEPS}
STEPS_BY_FIELD = {step.field: step for step in STEPS}
STATE_ORDER = [int(step.state) for step in STEPS]

#: Callback namespaces that accept ``<field>:pick:<index>``.
PICK_FIELDS: frozenset[str] = frozenset({step.field for step in STEPS})

PREVIEW_STATE = int(JobPostStates.PREVIEW)
EDIT_MENU_STATE = int(JobPostStates.EDIT_MENU)

CANCEL_TEXT = "❌ Job post cancelled. Nothing was published."
PREVIEW_HEADER = "👁 <b>Preview</b> — this is exactly what will be published."


# ---------------------------------------------------------------------------
# conversation data
# ---------------------------------------------------------------------------


def get_post(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    data = context.user_data.get(DATA_KEY)
    if data is None:
        data = {}
        context.user_data[DATA_KEY] = data
    return data


def reset_post(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (DATA_KEY, STEP_KEY, EDITING_KEY):
        context.user_data.pop(key, None)


def next_state(state: int | None) -> int:
    """The state after ``state``; the last field hands over to the preview."""
    if state is None or int(state) not in STATE_ORDER:
        return STATE_ORDER[0]
    index = STATE_ORDER.index(int(state))
    return STATE_ORDER[index + 1] if index + 1 < len(STATE_ORDER) else PREVIEW_STATE


def prev_state(state: int | None) -> int:
    if state is None or int(state) not in STATE_ORDER:
        return STATE_ORDER[0]
    index = STATE_ORDER.index(int(state))
    return STATE_ORDER[index - 1] if index > 0 else STATE_ORDER[0]


def current_step_state(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    return current_step(context, STEP_KEY)


def _state_for_field(field: str) -> int | None:
    for step in STEPS:
        if step.field == field:
            return int(step.state)
    return None


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def preview_text(data: dict[str, Any]) -> str:
    """The exact text that publishing would send."""
    return render_job_post(
        location=str(data.get("location") or ""),
        student_level=str(data.get("student_level") or ""),
        tutor_category=str(data.get("tutor_category") or ""),
        duration=str(data.get("duration") or ""),
        frequency=str(data.get("frequency") or ""),
        subjects=str(data.get("subjects") or ""),
        compensation=str(data.get("compensation") or ""),
    )


def missing_fields(data: dict[str, Any]) -> list[str]:
    return [label for key, label in EDITABLE_FIELDS if not str(data.get(key) or "").strip()]


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def _keyboard_for(step: Step, data: dict[str, Any]):
    show_back = int(step.state) != STATE_ORDER[0]
    if step.kind == "choice":
        return post_choice_keyboard(step.field, list(step.options), show_back=show_back)
    if step.kind == "location":
        return post_choice_keyboard(
            "location", list(JOB_POST_LOCATIONS), show_back=show_back
        )
    return post_nav_keyboard(show_back=show_back)


async def _advance(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: int
) -> int:
    """Move to the next step, or to the preview after the last field."""
    following = next_state(state)
    if following == PREVIEW_STATE:
        return await _show_preview(update, context)
    return await _prompt(update, context, following)


async def _prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int) -> int:
    step = STEPS_BY_STATE[int(state)]
    set_step(context, state, STEP_KEY)
    if step.kind == "location":
        prompt = (
            f"{step.prompt}\n\n"
            f"📌 Common: {', '.join(JOB_POST_LOCATIONS[:-1])}."
        )
    else:
        prompt = step.prompt
    await safe_reply(
        update,
        prompt,
        _keyboard_for(step, get_post(context)),
        parse_mode=ParseMode.HTML,
    )
    return int(state)


async def _show_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Requirement: preview, never publish automatically."""
    data = get_post(context)
    missing = missing_fields(data)
    set_step(context, JobPostStates.PREVIEW, STEP_KEY)
    context.user_data.pop(EDITING_KEY, None)

    if missing:
        await safe_reply(
            update,
            "⚠️ Still missing: " + ", ".join(missing) + "\n\nTap ✏️ Edit to fill it in.",
            post_edit_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return PREVIEW_STATE

    try:
        text = preview_text(data)
    except JobPostTemplateError as exc:  # pragma: no cover - defensive
        await safe_reply(update, f"⚠️ {exc}")
        return PREVIEW_STATE

    await safe_reply(
        update,
        f"{PREVIEW_HEADER}\n\n{to_telegram_html(text)}",
        post_preview_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return PREVIEW_STATE


async def start_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """``/post`` — admin only."""
    if not await require_admin(update):
        return ConversationHandler.END
    reset_post(context)
    start_flow(context, "job_post")
    get_post(context)["telegram_user_id"] = update.effective_user.id
    await safe_reply(
        update,
        "📰 <b>New job post</b>\n\n"
        "Seven quick questions, then I show you the finished post.\n"
        "The requirements are fixed — I write them for you every time.",
        parse_mode=ParseMode.HTML,
    )
    return await _prompt(update, context, STATE_ORDER[0])


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_post(context)
    end_flow(context)
    await safe_reply(update, CANCEL_TEXT)
    return ConversationHandler.END


def _validate(step: Step, text: str) -> str:
    value = validate_text(text, FIELD_LABELS.get(step.field, "this field"))
    if step.kind == "location" and not location_hashtag(value):
        raise ValidationError(
            "That location has no letters or numbers I can turn into a hashtag. "
            "Please send the place name, e.g. Ayat Tsebel."
        )
    return value


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    state = current_step_state(context)
    step = STEPS_BY_STATE.get(int(state)) if state is not None else None
    if step is None:
        return int(state) if state is not None else ConversationHandler.END

    payload = get_post(context)
    try:
        payload[step.field] = _validate(step, update.message.text or "")
    except ValidationError as exc:
        await safe_reply(update, f"⚠️ {exc}")
        return int(step.state)

    if context.user_data.get(EDITING_KEY) == step.field:
        # The admin is correcting one answer — go straight back to the preview.
        return await _show_preview(update, context)
    return await _advance(update, context, int(step.state))


async def _show_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    set_step(context, JobPostStates.EDIT_MENU, STEP_KEY)
    await safe_reply(
        update,
        "✏️ <b>Edit which field?</b>\n\n"
        "Changing the location updates every place it appears. "
        "The requirements wording stays exactly as it is.",
        post_edit_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return EDIT_MENU_STATE


async def _start_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, field: str) -> int:
    state = _state_for_field(field)
    if state is None:  # pragma: no cover - guarded by the keyboard
        return await _show_edit_menu(update, context)
    # Force a fresh answer for this field only.
    get_post(context).pop(field, None)
    context.user_data[EDITING_KEY] = field
    return await _prompt(update, context, state)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = update.callback_query.data or ""
    await answer(update)
    state = current_step_state(context)
    payload = get_post(context)

    if data == CB_CANCEL:
        return await cancel(update, context)
    if data == CB_PREVIEW:
        return await _show_preview(update, context)
    if data == CB_EDIT:
        return await _show_edit_menu(update, context)
    if data == CB_PUBLISH:
        return await publish_post(update, context)
    if data == CB_BACK:
        if state == PREVIEW_STATE:
            return await _show_preview(update, context)
        return await _prompt(update, context, prev_state(state))

    parts = data.split(":")
    if len(parts) == 4 and parts[1] in PICK_FIELDS and parts[2] == "pick":
        # The callback carries its own field, so a button rendered for one step
        # is honoured even if the conversation moved on.
        target = STEPS_BY_FIELD[parts[1]]
        try:
            index = int(parts[3])
        except ValueError:  # pragma: no cover - defensive
            return int(state) if state is not None else EDIT_MENU_STATE
        options = list(target.options) or list(JOB_POST_LOCATIONS)
        if index < 0 or index >= len(options):  # pragma: no cover - defensive
            return int(state) if state is not None else EDIT_MENU_STATE
        payload[target.field] = str(options[index]).strip()
        if context.user_data.get(EDITING_KEY) == target.field:
            return await _show_preview(update, context)
        return await _advance(update, context, int(target.state))

    if len(parts) == 3 and parts[1] == "editfield" and parts[2]:
        return await _start_edit(update, context, parts[2])

    if state == PREVIEW_STATE:
        return PREVIEW_STATE
    return int(state) if state is not None else EDIT_MENU_STATE


# ---------------------------------------------------------------------------
# publishing
# ---------------------------------------------------------------------------


def publish_chat_id(update: Update) -> int:
    """Configured job channel, falling back to the admin's own chat."""
    return int(settings.job_post_chat_id or update.effective_chat.id)


async def publish_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Persist the post, send it, then record the Telegram message ID."""
    payload = dict(get_post(context))
    payload["telegram_user_id"] = update.effective_user.id

    missing = missing_fields(payload)
    if missing:
        await safe_reply(
            update,
            "⚠️ Cannot publish yet — still missing: " + ", ".join(missing),
            post_edit_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return PREVIEW_STATE

    try:
        with session_scope() as session:
            post = job_post_service.create_job_post(session, payload)
            post_id = post.public_post_id
            text = post.generated_text
    except job_post_service.JobPostValidationError as exc:
        await safe_reply(update, f"⚠️ {exc}")
        return PREVIEW_STATE
    except Exception:
        logger.error("Job post could not be stored")
        await safe_reply(update, "⚠️ We could not save the post. Please try again.")
        return PREVIEW_STATE

    chat_id = publish_chat_id(update)
    try:
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=to_telegram_html(text),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        logger.error("Job post delivery failed")
        await safe_reply(
            update,
            f"⚠️ Saved as <code>{post_id}</code> but publishing failed. "
            "Check the channel and tap 📢 Publish again.",
            parse_mode=ParseMode.HTML,
        )
        return PREVIEW_STATE

    with session_scope() as session:
        stored = job_post_service.get_job_post_by_id(session, post_id)
        if stored is not None:
            job_post_service.mark_published(
                session, stored, chat_id, getattr(message, "message_id", None)
            )

    reset_post(context)
    end_flow(context)
    await safe_reply(
        update,
        f"✅ <b>Job post published</b>\n\nReference: <code>{post_id}</code>",
        parse_mode=ParseMode.HTML,
    )

    # Automatic matching (sections 17, 20): score every VERIFIED tutor, rank
    # them, persist the top N as candidate rows, and report to the admin.
    await _run_match_pipeline(context, post_id, update)
    return ConversationHandler.END


async def _run_match_pipeline(context, post_id: str, update: Update) -> list:
    """Run the matching engine after a publish and notify the admin.

    A failure here must never unpublish the job or lose the publish confirmation,
    so every problem is logged and surfaced to the admin rather than raised.
    """
    try:
        with session_scope() as session:
            post = job_match_pipeline.get_job(session, post_id)
            if post is None:
                return []
            results = job_match_pipeline.run_match_for_job(session, post)
        report = job_match_pipeline.format_report(
            post, results, threshold_note=_match_threshold_note()
        )
        await notification_service.notify_admin_job_matches(
            context.bot, report, post_id, len(results)
        )
        await notification_service.notify_admin_match_candidates(
            context.bot, results, post_id
        )
        if update is not None:
            await safe_reply(
                update,
                f"🎯 Matching complete — {len(results)} candidate(s) sent to the admin chat.",
            )
        return results
    except Exception:
        logger.exception("Automatic matching failed for %s", post_id)
        if update is not None:
            await safe_reply(
                update,
                "⚠️ Matching could not run for this job. Use /match to retry.",
            )
        return []


def _match_threshold_note() -> str:
    return f"Minimum score for a report: {settings.match_min_score:g}% • New-match alert at ≥{settings.match_notification_threshold:g}%"


# ---------------------------------------------------------------------------
# conversation wiring
# ---------------------------------------------------------------------------


def _state_handlers() -> dict[int, list[Any]]:
    handlers: dict[int, list[Any]] = {}
    for state in [*STATE_ORDER, PREVIEW_STATE, EDIT_MENU_STATE]:
        handlers[state] = [
            CallbackQueryHandler(callback_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler),
        ]
    return handlers


def build_job_post_conversation() -> ConversationHandler:
    """Assemble the job-post ConversationHandler.

    Registered before the admin console so the ``admin:post_job`` button is
    claimed by this flow rather than swallowed by the admin conversation.
    """
    return ConversationHandler(
        entry_points=[
            CommandHandler("post", start_post),
            CallbackQueryHandler(start_post, pattern=r"^admin:post_job$"),
        ],
        states=_state_handlers(),
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.COMMAND, cancel),
        ],
        name="job_post",
        allow_reentry=True,
        conversation_timeout=None,
    )


__all__ = [
    "DATA_KEY",
    "EDITING_KEY",
    "FIELD_LABELS",
    "STEPS",
    "build_job_post_conversation",
    "callback_handler",
    "cancel",
    "get_post",
    "missing_fields",
    "next_state",
    "prev_state",
    "preview_text",
    "publish_chat_id",
    "publish_post",
    "reset_post",
    "start_post",
    "text_handler",
]