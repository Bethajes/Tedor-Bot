"""Student / parent tutor request conversation (Requirements 6.x, 7.x)."""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers.common import (
    Step,
    ValidationError,
    answer,
    current_step,
    end_flow,
    html_escape,
    run_validator,
    safe_edit,
    safe_reply,
    set_step,
    start_flow,
    summary_lines,
)
from app.bot.keyboards.student import (
    CB_CANCEL,
    no_match_keyboard,
    student_navigation_keyboard,
    student_submit_keyboard,
    tutor_match_keyboard,
    view_tutor_keyboard,
)
from app.bot.keyboards.tutor import choice_keyboard, multi_select_keyboard
from app.bot.options import COUNTRIES, LEVELS, TEACHING_MODES
from app.bot.states import StudentStates
from app.database import session_scope
from app.enums import Currency
from app.services import notification_service, search_service, student_service, tutor_service
from app.services.currency import currency_for_country, is_ethiopia
from app.services.storage_service import TelegramStorageService

logger = logging.getLogger(__name__)

DATA_KEY = "student_request"
RESULTS_KEY = "student_matches"
STEP_KEY = "student_step"

STEPS: tuple[Step, ...] = (
    Step(
        StudentStates.PARENT_NAME,
        "parent_name",
        "👤 <b>Parent / guardian name</b>\n\nWho should we contact about this request?",
        kind="name",
    ),
    Step(
        StudentStates.STUDENT_NAME,
        "student_name",
        "🎒 <b>Student name</b>\n\nWhich student needs a tutor?",
        kind="name",
    ),
    Step(
        StudentStates.COUNTRY,
        "country",
        "🌍 <b>Country</b>\n\nWhere is the student based? This decides the currency.",
        kind="choice",
        options=COUNTRIES,
    ),
    Step(
        StudentStates.EDUCATION_LEVEL,
        "education_level",
        "🎓 <b>Education level</b>\n\nWhich level should be taught?",
        kind="choice",
        options=LEVELS,
    ),
    Step(
        StudentStates.SUBJECT,
        "subject",
        "📚 <b>Subject</b>\n\nWhich subject do you need help with?",
        kind="text",
    ),
    Step(
        StudentStates.LANGUAGE,
        "preferred_language",
        "🗣 <b>Preferred language</b>\n\nWhich language should the tutor teach in?",
        kind="text",
        skippable=True,
    ),
    Step(
        StudentStates.MODE,
        "mode",
        "🧑‍🏫 <b>Teaching mode preference</b>\n\nOnline, in person, or both?",
        kind="choice",
        options=TEACHING_MODES,
    ),
    Step(
        StudentStates.BUDGET,
        "budget",
        "💰 <b>Hourly budget</b>\n\nWhat is your comfortable hourly budget?",
        kind="float",
        skippable=True,
    ),
    Step(
        StudentStates.SCHEDULE,
        "schedule",
        "🗓 <b>Preferred schedule</b>\n\ne.g. Weekdays 16:00-18:00, Saturday mornings",
        kind="text",
    ),
    Step(
        StudentStates.REQUIREMENTS,
        "additional_requirements",
        "📝 <b>Additional requirements</b>\n\nAnything else we should know? Send - to skip.",
        kind="text",
        optional=True,
        skippable=True,
    ),
    Step(
        StudentStates.CONTACT,
        "contact",
        "📞 <b>Contact information</b>\n\nPhone number or Telegram username so tutors can reach you.",
        kind="text",
    ),
    Step(StudentStates.SUMMARY, "summary", "", kind="summary"),
)

STEPS_BY_STATE = {int(step.state): step for step in STEPS}
STATE_ORDER = [int(step.state) for step in STEPS]

SUMMARY_LABELS: dict[str, str] = {
    "parent_name": "Parent / guardian",
    "student_name": "Student",
    "country": "Country",
    "education_level": "Education level",
    "subject": "Subject",
    "preferred_language": "Preferred language",
    "mode": "Teaching mode",
    "budget": "Budget",
    "schedule": "Preferred schedule",
    "additional_requirements": "Additional requirements",
    "contact": "Contact",
}


def get_request(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    data = context.user_data.get(DATA_KEY)
    if data is None:
        data = {}
        context.user_data[DATA_KEY] = data
    return data


def reset_request(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (DATA_KEY, RESULTS_KEY, STEP_KEY):
        context.user_data.pop(key, None)


def next_state(state: int | None) -> int:
    if state is None or state not in STATE_ORDER:
        return STATE_ORDER[0]
    index = STATE_ORDER.index(int(state))
    return STATE_ORDER[index + 1] if index + 1 < len(STATE_ORDER) else int(state)


def prev_state(state: int | None) -> int:
    if state is None or state not in STATE_ORDER:
        return STATE_ORDER[0]
    index = STATE_ORDER.index(int(state))
    return STATE_ORDER[index - 1] if index > 0 else STATE_ORDER[0]


def current_step_state(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """The student flow keeps its own step key."""
    return current_step(context, STEP_KEY)


async def _prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int) -> int:
    step = STEPS_BY_STATE[int(state)]
    data = get_request(context)
    set_step(context, state, STEP_KEY)

    if int(state) == int(StudentStates.SUMMARY):
        await _show_summary(update, context)
        return state

    if int(state) == int(StudentStates.BUDGET):
        currency = currency_for_country(data.get("country"))
        symbol = "ETB" if currency == Currency.ETB else "USD"
        text = f"{step.prompt}\n\nCurrency: <b>{symbol}</b>"
        await safe_reply(
            update, text, student_navigation_keyboard(), parse_mode="HTML"
        )
        return state

    await safe_reply(
        update, step.prompt, _keyboard_for(step, data), parse_mode="HTML"
    )
    return state


def _keyboard_for(step: Step, data: dict[str, Any]):
    prefix = f"student:{step.field}"
    if step.kind == "choice":
        options = [label for _code, label in step.options] if step.field == "mode" else list(step.options)
        return choice_keyboard(prefix, options)
    if step.kind == "multiselect":
        return multi_select_keyboard(prefix, list(step.options), data.get(step.field) or [])
    # A required step hides Continue until it has an answer, so the parent
    # cannot walk past it and end up with an empty value in the archive.
    answered = bool(str(data.get(step.field) or "").strip())
    return student_navigation_keyboard(show_continue=step.skippable or answered)


def _budget_view(data: dict[str, Any]) -> str:
    budget = data.get("budget")
    if budget is None:
        return "—"
    currency = currency_for_country(data.get("country"))
    return f"{budget:g} {currency.value}"


async def _show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = get_request(context)
    view = dict(data)
    view["budget"] = _budget_view(data)
    lines = summary_lines(view, SUMMARY_LABELS)
    lines.append("")
    lines.append("Submit when everything looks right.")
    await safe_reply(
        update, "\n".join(lines), student_submit_keyboard(), parse_mode="HTML"
    )


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


async def start_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_request(context)
    start_flow(context, "student")
    data = get_request(context)
    data["telegram_user_id"] = update.effective_user.id
    await safe_reply(
        update,
        "🔎 <b>Find a tutor</b>\n\n"
        "Answer a few questions and we will show you the best matching tutors.",
    )
    return await _prompt(update, context, STATE_ORDER[0])


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_request(context)
    end_flow(context)
    await safe_reply(update, "❌ Request cancelled. Type /start to begin again.")
    return ConversationHandler.END


async def _continue(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: int | None,
    step: Step | None,
    payload: dict[str, Any],
) -> int:
    """Advance, or insist on an answer when the step is not skippable.

    Without this, tapping ✅ Continue used to walk past every step and store
    empty values (a request with no contact information, for instance).
    """
    if (
        step is not None
        and not step.skippable
        and not str(payload.get(step.field) or "").strip()
    ):
        label = SUMMARY_LABELS.get(step.field, step.field)
        await safe_reply(
            update,
            f"⚠️ Please answer the <b>{html_escape(label)}</b> question before continuing.",
            parse_mode="HTML",
        )
        return await _prompt(update, context, int(step.state))
    return await _prompt(update, context, next_state(state))


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data or ""
    await answer(update)
    state = current_step_state(context)
    step = STEPS_BY_STATE.get(int(state)) if state is not None else None
    payload = get_request(context)

    if data == CB_CANCEL:
        return await cancel(update, context)

    if data == "student:back":
        return await _prompt(update, context, prev_state(state))
    if data == "student:continue":
        return await _continue(update, context, state, step, payload)
    if data == "student:edit":
        await safe_reply(
            update,
            "✏️ Use ⬅️ Back to change an earlier answer, or type /cancel and start again.",
        )
        return int(state or STATE_ORDER[0])
    if data == "student:submit":
        return await submit_request(update, context)

    parts = data.split(":")
    if len(parts) >= 3 and parts[-2] in {"pick", "toggle"} and step is not None:
        try:
            index = int(parts[-1])
        except ValueError:
            return int(step.state)
        options = list(step.options)
        if index < 0 or index >= len(options):
            return int(step.state)
        if parts[-2] == "pick":
            raw = options[index]
            payload[step.field] = str(raw[0] if isinstance(raw, tuple) else raw).strip()
            if step.field == "country":
                payload["currency"] = str(currency_for_country(payload[step.field]))
            return await _prompt(update, context, next_state(int(step.state)))
        value = options[index]
        selected = list(payload.get(step.field) or [])
        if value in selected:
            selected.remove(value)
        else:
            selected.append(value)
        payload[step.field] = selected
        await safe_edit(
            update,
            f"{step.prompt}\n\n<b>Selected:</b> {', '.join(selected) or 'none yet'}",
            _keyboard_for(step, payload),
            parse_mode="HTML",
        )
        return int(step.state)

    # result callbacks -----------------------------------------------------
    if data.startswith("stu:view:"):
        return await _show_tutor(update, context, parts[2], parts[3] if len(parts) > 3 else "")
    if data.startswith("stu:select:"):
        return await _select_tutor(update, context, parts[2], parts[3])
    if data.startswith("stu:more:"):
        return await _show_matches(update, context, parts[2] if len(parts) > 2 else "")
    if data.startswith("stu:support:"):
        return await _forward_to_support(update, context, parts[2])

    return int(state or STATE_ORDER[0])


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    state = current_step_state(context)
    step = STEPS_BY_STATE.get(int(state)) if state is not None else None
    if step is None or step.kind == "summary":
        return int(state or STATE_ORDER[0])

    payload = get_request(context)
    text = update.message.text or ""
    try:
        if text.strip() in {"-", "skip", "none"} and step.optional:
            payload[step.field] = ""
        elif step.kind == "choice":
            match = _match_option(step, text)
            if match is None:
                await safe_reply(update, "Please pick one of the options shown.")
                return int(step.state)
            payload[step.field] = match
            if step.field == "country":
                payload["currency"] = str(currency_for_country(match))
        else:
            payload[step.field] = run_validator(step.kind, text)
    except ValidationError as exc:
        await safe_reply(update, f"⚠️ {exc}")
        return int(step.state)

    return await _prompt(update, context, next_state(int(step.state)))


def _match_option(step: Step, text: str) -> str | None:
    needle = text.strip().casefold()
    for raw in step.options:
        label = raw[1] if isinstance(raw, tuple) else raw
        value = raw[0] if isinstance(raw, tuple) else raw
        if label.casefold() == needle or str(value).casefold() == needle:
            return str(value)
    if step.field == "country":
        return text.strip()
    if step.field == "subject":
        return text.strip()
    return None


# ---------------------------------------------------------------------------
# submission and results
# ---------------------------------------------------------------------------


async def submit_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Requirement 6.4/6.5: store, archive, confirm, then match tutors."""
    payload = get_request(context)
    telegram_user_id = update.effective_user.id
    record = dict(payload)
    record["telegram_user_id"] = telegram_user_id
    record.pop("currency", None)

    try:
        with session_scope() as session:
            request = student_service.create_student_request(session, record)
            request_id = request.public_request_id
            context.user_data["last_request_id"] = request_id
    except student_service.StudentRequestValidationError as exc:
        await safe_reply(update, f"⚠️ {exc}")
        return int(StudentStates.SUMMARY)
    except Exception:
        logger.error("Student request could not be stored")
        await safe_reply(
            update, "⚠️ We could not save your request. Please try again in a moment."
        )
        return int(StudentStates.SUMMARY)

    await safe_reply(
        update,
        "✅ <b>Request received</b>\n\n"
        f"Your request ID is <code>{request_id}</code>.\n"
        "Our team will contact you shortly.",
    )

    storage = TelegramStorageService(context.bot)
    await storage.store_student_request(request_id)

    with session_scope() as session:
        request = student_service.get_request_by_id(session, request_id)
        if request is not None:
            await notification_service.notify_admin_student_request(context.bot, request)

    return await _show_matches(update, context, request_id)


async def _load_matches(request_id: str) -> list[search_service.ScoredTutor]:
    with session_scope() as session:
        request = student_service.get_request_by_id(session, request_id)
        if request is None:
            return []
        return search_service.match_tutors(session, request, limit=5)


async def _show_matches(
    update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: str = ""
) -> int:
    request_id = request_id or _last_request_id(context)
    if not request_id:
        await safe_reply(update, "🔎 Let's start a new tutor search.")
        return await start_request(update, context)

    matches = await _load_matches(request_id)
    context.user_data[RESULTS_KEY] = {
        item.tutor.public_tutor_id: item.score for item in matches
    }
    if not matches:
        await safe_reply(
            update,
            "😔 <b>No matching tutors yet</b>\n\n"
            "We have forwarded your request to the support team and they will "
            "suggest a tutor manually.",
            no_match_keyboard(request_id),
        )
        return int(StudentStates.RESULTS)

    lines = ["🎯 <b>Recommended tutors</b>", ""]
    for index, item in enumerate(matches, start=1):
        lines.append(
            f"<b>{index}. {item.tutor.display_name}</b> ✅ "
            f"<code>{item.tutor.public_tutor_id}</code>\n"
            f"   📚 {', '.join(item.tutor.subject_list) or '—'}\n"
            f"   🎓 {', '.join(item.tutor.level_list) or '—'} | "
            f"🧑‍🏫 {item.tutor.teaching_mode} | ⭐ {item.score}/{search_service.MAX_SCORE}"
        )
    await safe_reply(
        update,
        "\n".join(lines),
        tutor_match_keyboard(matches, request_id),
        parse_mode="HTML",
    )
    return int(StudentStates.RESULTS)


def _last_request_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    value = context.user_data.get("last_request_id")
    return str(value) if value else ""


async def _show_tutor(
    update: Update, context: ContextTypes.DEFAULT_TYPE, tdr_id: str, request_id: str = ""
) -> int:
    """Requirement 7.3."""
    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
        if tutor is None:
            await safe_reply(update, "Tutor not found.")
            return int(StudentStates.RESULTS)
        text = _tutor_profile_text(tutor)
    await safe_reply(
        update, text, view_tutor_keyboard(tdr_id, request_id or _last_request_id(context)),
        parse_mode="HTML",
    )
    return int(StudentStates.TUTOR_DETAIL)


def _tutor_profile_text(tutor) -> str:
    rate = tutor.etb_rate if is_ethiopia(tutor.country) else tutor.usd_rate
    currency = "ETB" if is_ethiopia(tutor.country) else "USD"
    return (
        f"👤 <b>{tutor.display_name}</b> ✅ <code>{tutor.public_tutor_id}</code>\n\n"
        f"📍 {tutor.city}, {tutor.country}\n"
        f"📚 Subjects: {', '.join(tutor.subject_list) or '—'}\n"
        f"🎓 Levels: {', '.join(tutor.level_list) or '—'}\n"
        f"🗣 Languages: {', '.join(tutor.language_list) or '—'}\n"
        f"🧑‍🏫 Mode: {tutor.teaching_mode}\n"
        f"💼 Experience: {tutor.experience_years} year(s)\n"
        f"💰 Rate: {rate:g} {currency}/hr\n\n"
        f"{tutor.bio}"
    )


async def _select_tutor(
    update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: str, tdr_id: str
) -> int:
    """Requirement 7.4."""
    with session_scope() as session:
        request = student_service.get_request_by_id(session, request_id)
        tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
        if request is None or tutor is None:
            await safe_reply(update, "Sorry — that request or tutor is no longer available.")
            return int(StudentStates.RESULTS)
        student_service.assign_tutor(session, request, tdr_id)
        await notification_service.notify_admin_tutor_selected(context.bot, request, tutor)
    await safe_reply(
        update,
        "📩 <b>Great choice!</b>\n\n"
        f"We passed your request to tutor <code>{tdr_id}</code>. "
        "They will contact you shortly using the details you provided.",
    )
    return int(StudentStates.RESULTS)


async def _forward_to_support(
    update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: str
) -> int:
    with session_scope() as session:
        request = student_service.get_request_by_id(session, request_id)
        if request is None:
            await safe_reply(update, "Request not found.")
            return int(StudentStates.RESULTS)
        bot = context.bot
        text = (
            "🚨 <b>Unmatched tutor request forwarded to support</b>\n\n"
            f"Request: <code>{request.public_request_id}</code>\n"
            f"Country: {request.country}\n"
            f"Subject: {request.subject}\n"
            f"Level: {request.education_level}\n"
            f"Budget: {request.budget if request.budget is not None else '—'} {request.currency}"
        )
        try:
            await bot.send_message(chat_id=notification_service.admin_chat_id(), text=text)
        except Exception:
            logger.error("Could not forward unmatched request to support")
            await safe_reply(update, "⚠️ We could not forward your request. Please contact support.")
            return int(StudentStates.RESULTS)
    await safe_reply(update, "📩 Our support team has your request and will reply shortly.")
    return int(StudentStates.RESULTS)


# ---------------------------------------------------------------------------
# conversation wiring
# ---------------------------------------------------------------------------


def build_student_conversation() -> ConversationHandler:
    states: dict[int, list[Any]] = {}
    for state in STATE_ORDER:
        states[state] = [
            CallbackQueryHandler(callback_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler),
        ]
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_request, pattern=r"^menu:find_tutor$"),
        ],
        states=states,
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.COMMAND, cancel),
        ],
        name="student_request",
        allow_reentry=True,
    )


__all__ = [
    "DATA_KEY",
    "RESULTS_KEY",
    "STEPS",
    "build_student_conversation",
    "callback_handler",
    "cancel",
    "start_request",
    "submit_request",
    "text_handler",
]