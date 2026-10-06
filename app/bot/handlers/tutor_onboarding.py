"""Ethiopian tutor onboarding conversation.

A condensed, button-first version of the tutor application. The older
26-step flow (``app.bot.handlers.tutor``) is untouched and remains in place
for non-Ethiopian tutors — this flow handles the structured Ethiopian
onboarding described in sections 2-8 of the specification.

At each step we expose Back / Edit / Cancel / Continue (section 6), and at the
end a full summary with Submit / Back / Cancel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from telegram import Document, InlineKeyboardButton, InlineKeyboardMarkup, Update, Voice
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.bot.keyboards.tutor_onboarding import (
    CB_BACK,
    CB_CANCEL,
    CB_CONTINUE,
    CB_DONE,
    CB_EDIT,
    CB_SUBMIT,
    choice_keyboard,
    english_level_keyboard,
    multiselect_keyboard,
    nav_keyboard,
    skip_keyboard,
    summary_keyboard,
    voice_keyboard,
)
from app.bot.options import (
    ETH_EDUCATION_LEVELS,
    ETH_ENGLISH_LEVELS,
    ETH_ENTRANCE_EXAM_TYPES,
    ETH_GRADES,
    ETH_LOCATIONS,
    SUBJECTS,
)
from app.bot.states import CANCEL_TEXT, EthiopianTutorStates
from app.database import session_scope
from app.enums import DocumentType
from app.services import tutor_profile_service as profile_service
from app.services import tutor_service
from app.services.notification_service import notify_admin_new_tutor

logger = logging.getLogger(__name__)

APP_KEY = "ethiopian_tutor_app"
STEP_KEY = "ethiopian_tutor_step"
DOC_QUEUE_KEY = "ethiopian_tutor_doc_queue"

CANCEL_TEXT_LOCAL = (
    "❌ Application cancelled. Type /start or tap Become a Tutor to begin again."
)


@dataclass
class Step:
    state: int
    field: str
    prompt: str
    kind: str  # text | choice | multiselect | english | voice | documents | summary
    optional: bool = False
    skippable: bool = False


# ---------------------------------------------------------------------------
# Sections 2-8: the collected fields, in the order they are asked.
# ---------------------------------------------------------------------------

STEPS: tuple[Step, ...] = (
    Step(EthiopianTutorStates.FULL_NAME, "full_name",
         "👤 <b>Step 1/16 — Full name</b>\n\nYour full name as it appears on your national ID.", "name"),
    Step(EthiopianTutorStates.GENDER, "gender",
         "👤 <b>Step 2/16 — Gender</b>\n\nTap your choice.", "choice"),
    Step(EthiopianTutorStates.AGE, "age",
         "🎂 <b>Step 3/16 — Age</b>\n\nYour age in years (18 or older).", "int"),
    Step(EthiopianTutorStates.PHONE, "phone",
         "📞 <b>Step 4/16 — Phone number</b>\n\nInclude your country code, e.g. <code>+251911234567</code>.", "phone"),
    Step(EthiopianTutorStates.CURRENT_ADDRESS, "current_address",
         "📍 <b>Step 5/16 — Current address</b>\n\nWhere you live now, e.g. <code>Bole, Addis Ababa</code>.", "address"),
    Step(EthiopianTutorStates.LOCATIONS, "locations",
         "📍 <b>Step 6/16 — Tutoring locations</b>\n\nWhich areas are you available to tutor in? Tap to toggle, then Done.", "multiselect"),
    Step(EthiopianTutorStates.GRADES, "grades",
         "🎓 <b>Step 7/16 — Grades you can tutor</b>\n\nSelect all that apply.", "multiselect"),
    Step(EthiopianTutorStates.SUBJECTS, "subjects",
         "📚 <b>Step 8/16 — Subjects you can tutor</b>\n\nSelect all that apply.", "multiselect"),
    Step(EthiopianTutorStates.ENGLISH, "english_proficiency",
         "🇬🇧 <b>Step 9/16 — English communication skill</b>\n\n1–10. This is your own rating; an optional recording helps our admin verify it.", "english"),
    Step(EthiopianTutorStates.ENGLISH_VOICE, "english_voice",
         "🎤 <b>Step 10/16 — English voice introduction (optional)</b>\n\nSend a 30–60 second voice message in English, or skip.\n\n<i>This is supporting evidence only — it is reviewed by an admin, not auto-scored.</i>", "voice", optional=True),
    Step(EthiopianTutorStates.EDUCATION_LEVEL, "education_level",
         "🎓 <b>Step 11/16 — Current / highest education</b>", "edu_level"),
    Step(EthiopianTutorStates.UNIVERSITY, "university",
         "🏫 <b>Step 12/16 — University / institution</b>\n\nWhich university or college did you attend?", "text"),
    Step(EthiopianTutorStates.DEPARTMENT, "department",
         "🧪 <b>Step 13/16 — Department</b>\n\nYour department or field of study.", "text"),
    Step(EthiopianTutorStates.UNIVERSITY_YEAR, "university_year",
         "📅 <b>Step 14/16 — Year</b>\n\nYour graduation year (or current year of study).", "year"),
    Step(EthiopianTutorStates.CGPA, "cgpa",
         "📈 <b>Step 15/16 — CGPA (if applicable)</b>\n\nYour current/last CGPA, e.g. <code>3.75</code>.", "cgpa", optional=True),
    Step(EthiopianTutorStates.ENTRANCE_TYPE, "entrance_exam_type",
         "📝 <b>Step 16a — Admission exam type</b>", "exam_type"),
    Step(EthiopianTutorStates.ENTRANCE_MAX_SCORE, "entrance_exam_max_score",
         "⚖️ <b>Step 16b — Maximum score for that exam</b>\n\nE.g. <code>840</code> or <code>100</code>. Stored so scores from different scales are normalised.", "max_score"),
    Step(EthiopianTutorStates.ENTRANCE_SCORE, "entrance_exam_score",
         "🏅 <b>Step 16c — Your entrance exam score</b>\n\nE.g. <code>612</code>.", "exam_score"),
    Step(EthiopianTutorStates.ENTRANCE_YEAR, "entrance_exam_year",
         "📆 <b>Step 16d — Exam year</b>\n\nThe year you took that exam, e.g. <code>2016</code>.", "year"),
    Step(EthiopianTutorStates.EXPERIENCE_YEARS, "teaching_experience_years",
         "💼 <b>Teaching experience</b>\n\nHow many years of tutoring/teaching experience do you have? Whole number.", "int", optional=True),
    Step(EthiopianTutorStates.EXPERIENCE_DESCRIPTION, "teaching_experience_description",
         "🧑‍🏫 <b>Experience details</b>\n\nBriefly describe your experience — subjects taught, grades, and where (e.g. private tutoring, school).", "text", optional=True),
    Step(EthiopianTutorStates.DOCUMENTS, "documents",
         "📄 <b>Documents</b>\n\nUpload any academic certificates (PDF or image). Tap Skip when you are done, or send them now.", "documents", optional=True),
    Step(EthiopianTutorStates.SUMMARY, "summary", "", "summary"),
)

STEPS_BY_STATE = {int(step.state): step for step in STEPS}
STATE_ORDER = [int(step.state) for step in STEPS]
STEP_INDEX_BY_STATE = {int(step.state): index for index, step in enumerate(STEPS)}


def _is_optional_state(state: int) -> bool:
    step = STEPS_BY_STATE.get(int(state))
    return step is not None and step.optional


def get_app(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    data = context.user_data.get(APP_KEY)
    if data is None:
        data = {}
        context.user_data[APP_KEY] = data
    return data


def set_step(context: ContextTypes.DEFAULT_TYPE, state: int | None) -> None:
    if state is None:
        context.user_data.pop(STEP_KEY, None)
    else:
        context.user_data[STEP_KEY] = int(state)


def current_step_state(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    value = context.user_data.get(STEP_KEY)
    return int(value) if value is not None else None


def next_state(state: int) -> int:
    index = STATE_ORDER.index(int(state))
    return STATE_ORDER[index + 1] if index + 1 < len(STATE_ORDER) else int(state)


def prev_state(state: int) -> int:
    index = STATE_ORDER.index(int(state))
    return STATE_ORDER[index - 1] if index > 0 else STATE_ORDER[0]


def _next_to_skip_optional(state: int, target: int) -> int:
    """Advance from ``state`` to ``target`` and skip optional steps?

    Optional steps still ask; we use them only as a *destination*, not an
    auto-skip. The skip is explicit via their keyboards.
    """
    return target


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------


async def _prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int) -> int:
    set_step(context, state)
    step = STEPS_BY_STATE[state]

    if step.kind == "summary":
        return await _show_summary(update, context)

    await _safe_reply(update, step.prompt, _keyboard_for(step, get_app(context)))
    return state


def _keyboard_for(step: Step, app: dict[str, Any]):
    if step.kind == "choice":
        if step.field == "education_level":
            return choice_keyboard("education_level", [label for _k, label in ETH_EDUCATION_LEVELS])
        if step.field == "entrance_exam_type":
            return choice_keyboard("entrance_exam_type", [label for _k, label in ETH_ENTRANCE_EXAM_TYPES])
        if step.field == "gender":
            return choice_keyboard("gender", ["Male", "Female", "Other"])
        return choice_keyboard(step.field, [])
    if step.kind == "multiselect":
        options = {
            "locations": list(ETH_LOCATIONS),
            "grades": list(ETH_GRADES),
            "subjects": list(SUBJECTS),
        }.get(step.field, [])
        return multiselect_keyboard(step.field, options, app.get(step.field) or [])
    if step.kind == "english":
        return english_level_keyboard()
    if step.kind == "summary":
        return summary_keyboard()
    if step.kind == "voice":
        return voice_keyboard()
    if step.kind == "documents":
        return skip_keyboard()
    return nav_keyboard()


async def _safe_reply(update: Update, text: str, keyboard=None) -> None:
    from telegram import InlineKeyboardMarkup

    if keyboard is None:
        keyboard = InlineKeyboardMarkup([])
    try:
        if update.callback_query is not None:
            await update.callback_query.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception:
        # A message may be too long or contain characters Telegram rejects; the
        # prompt is retried on the next update anyway.
        logger.exception("Failed to render step prompt")


async def _show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    app = get_app(context)
    lines = _summary_lines(app)
    text = "\n".join(lines)
    try:
        if update.callback_query is not None:
            await update.callback_query.message.reply_text(text, reply_markup=summary_keyboard(), parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, reply_markup=summary_keyboard(), parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Failed to render summary")
    return int(EthiopianTutorStates.SUMMARY)


def _summary_lines(app: dict[str, Any]) -> list[str]:
    def fmt(value: Any, default: str = "—") -> str:
        if value in (None, "", []):
            return default
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    lines = [
        "📋 <b>Application Summary</b>",
        "",
        f"👤 <b>Name:</b> {fmt(app.get('full_name'))}",
        f"👤 <b>Gender:</b> {fmt(app.get('gender'))}",
        f"🎂 <b>Age:</b> {fmt(app.get('age'))}",
        f"📞 <b>Phone:</b> {fmt(app.get('phone'))}",
        f"📍 <b>Address:</b> {fmt(app.get('current_address'))}",
        f"📍 <b>Tutoring areas:</b> {fmt(app.get('locations'))}",
        f"🎓 <b>Grades:</b> {fmt(app.get('grades'))}",
        f"📚 <b>Subjects:</b> {fmt(app.get('subjects'))}",
        f"🇬🇧 <b>English:</b> {fmt(app.get('english_proficiency'))}/10",
        f"🎤 <b>English voice:</b> {'✅ received' if app.get('english_voice_file_id') else '—'}",
        f"🎓 <b>Education level:</b> {fmt(app.get('education_level'))}",
        f"🏫 <b>Institution:</b> {fmt(app.get('university'))}",
        f"🧪 <b>Department:</b> {fmt(app.get('department'))}",
        f"📅 <b>Year:</b> {fmt(app.get('university_year'))}",
        f"📈 <b>CGPA:</b> {fmt(app.get('cgpa'))}",
        f"📝 <b>Entrance exam:</b> {fmt(app.get('entrance_exam_type'))} — {fmt(app.get('entrance_exam_score'))}/{fmt(app.get('entrance_exam_max_score'))} ({fmt(app.get('entrance_exam_year'))})",
        f"💼 <b>Experience:</b> {fmt(app.get('teaching_experience_years'))} yr(s)",
        f"🧑‍🏫 <b>Details:</b> {fmt(app.get('teaching_experience_description'))}",
        f"📄 <b>Documents:</b> {fmt([d.get('document_type') for d in app.get('documents', [])])}",
    ]
    return lines


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def _validate_name(value: str) -> str:
    text = " ".join(value.split())
    if len(text) < 2:
        raise ValueError("Please enter your full name.")
    return text


def _validate_age(value: str) -> int:
    return profile_service.validate_age(value) or 0


def _validate_phone(value: str) -> str:
    return profile_service.validate_phone(value)


def _validate_text(value: str, label: str) -> str:
    out = profile_service.validate_text(value, label)
    if out is None:
        raise ValueError(f"Please provide {label}.")
    return out


def _validate_year(value: str) -> int:
    out = profile_service.validate_year(value, "year")
    if out is None:
        raise ValueError("Please enter a valid year, e.g. 2019.")
    return out


def _validate_cgpa(value: str) -> float | None:
    return profile_service.validate_cgpa(value)


def _validate_int(value: str) -> int:
    out = profile_service._optional_int(value)
    if out is None:
        raise ValueError("Please enter a whole number.")
    return out


def _validate_max_score(value: str) -> float:
    out = profile_service.validate_entrance_exam_max_score(value)
    if out is None:
        raise ValueError("Please enter the maximum score as a number above zero.")
    return out


def _validate_exam_score(value: str, max_score: Any) -> float:
    out = profile_service.validate_entrance_exam_score(value, max_score)
    if out is None:
        raise ValueError("Please enter your score as a number.")
    return out


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------


def _choice_value(step: Step, index_text: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Map a pick index back to the stored value."""
    try:
        index = int(index_text)
    except ValueError:
        raise ValueError("Please use the buttons to choose.")

    if step.field == "gender":
        options = ["Male", "Female", "Other"]
        return options[index]
    if step.field == "education_level":
        return ETH_EDUCATION_LEVELS[index][0]
    if step.field == "entrance_exam_type":
        return ETH_ENTRANCE_EXAM_TYPES[index][0]
    if step.kind == "english":
        return str(ETH_ENGLISH_LEVELS[index][0])
    raise ValueError("Unsupported choice field")


async def _advance(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int) -> int:
    target = next_state(state)
    return await _prompt(update, context, target)


# ---------------------------------------------------------------------------
# callback & message handlers
# ---------------------------------------------------------------------------


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data or ""
    await query.answer()
    app = get_app(context)
    state = current_step_state(context)
    if state is None:
        return ConversationHandler.END
    step = STEPS_BY_STATE[state]

    if data == CB_CANCEL:
        return await _cancel(update, context)
    if data == CB_BACK:
        return await _prompt(update, context, prev_state(state))
    if data == CB_CONTINUE:
        if step is not None and step.optional and not app.get(step.field):
            # Continue on an optional step means "skip it" — both acceptable.
            return await _advance(update, context, state)
        return await _advance(update, context, state)
    if data == CB_EDIT:
        # Inline edit menu listing every section, so the user can jump straight
        # to the step they want to correct.
        rows = []
        for s in STEPS:
            if s.kind == "summary":
                continue
            rows.append(
                [
                    InlineKeyboardButton(
                        f"📝 {s.field.replace('_', ' ').title()}",
                        callback_data=f"eth:editfield:{s.state}",
                    )
                ]
            )
        await _safe_reply(update, "✏️ Tap a section to edit it:", InlineKeyboardMarkup(rows))
        return state
    if data == CB_SUBMIT:
        return await submit_application(update, context)

    parts = data.split(":")
    # eth:editfield:<state>
    if len(parts) == 3 and parts[0] == "eth" and parts[1] == "editfield":
        target = int(parts[2])
        return await _prompt(update, context, target)

    # eth:<field>:pick:<n>
    if len(parts) == 4 and parts[0] == "eth" and parts[2] == "pick":
        try:
            value = _choice_value(step, parts[3], context)
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return state
        app[step.field] = value
        if step.kind == "english":
            app["english_proficiency"] = int(value)
        return await _advance(update, context, state)

    # eth:<field>:toggle:<n> / eth:<field>:done
    if len(parts) == 4 and parts[0] == "eth" and parts[2] == "toggle":
        options = {
            "locations": list(ETH_LOCATIONS),
            "grades": list(ETH_GRADES),
            "subjects": list(SUBJECTS),
        }[step.field]
        try:
            option = options[int(parts[3])]
        except (ValueError, IndexError):
            return state
        selected: list[str] = list(app.get(step.field) or [])
        if option in selected:
            selected.remove(option)
        else:
            selected.append(option)
        app[step.field] = selected
        try:
            await query.edit_message_reply_markup(
                reply_markup=multiselect_keyboard(step.field, options, selected)
            )
        except Exception:
            pass
        return state

    if len(parts) == 3 and parts[0] == "eth" and parts[2] == "done":
        selected = app.get(step.field) or []
        if not selected:
            await query.answer("Please select at least one option.", show_alert=True)
            return state
        return await _advance(update, context, state)

    # eth:voice:record — set a flag, then wait for the voice message.
    if data == "eth:voice:record":
        app["_awaiting_voice"] = True
        await _safe_reply(
            update,
            "🎤 Go ahead — send your 30–60 second English introduction as a voice message.",
            skip_keyboard(),
        )
        return state

    return state


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    state = current_step_state(context)
    if state is None:
        return ConversationHandler.END
    step = STEPS_BY_STATE[state]
    app = get_app(context)
    text = (update.message.text or "").strip()

    try:
        if step.field == "full_name":
            app["full_name"] = _validate_name(text)
        elif step.field == "age":
            app["age"] = _validate_age(text)
        elif step.field == "phone":
            app["phone"] = _validate_phone(text)
        elif step.field == "current_address":
            app["current_address"] = _validate_text(text, "your current address")
        elif step.field == "university":
            app["university"] = _validate_text(text, "the institution")
        elif step.field == "department":
            app["department"] = _validate_text(text, "the department")
        elif step.field == "university_year":
            app["university_year"] = _validate_year(text)
        elif step.field == "cgpa":
            app["cgpa"] = _validate_cgpa(text)
        elif step.field == "entrance_exam_max_score":
            app["entrance_exam_max_score"] = _validate_max_score(text)
        elif step.field == "entrance_exam_score":
            app["entrance_exam_score"] = _validate_exam_score(text, app.get("entrance_exam_max_score"))
        elif step.field == "entrance_exam_year":
            app["entrance_exam_year"] = _validate_year(text)
        elif step.field == "teaching_experience_years":
            app["teaching_experience_years"] = _validate_int(text)
        elif step.field == "teaching_experience_description":
            app["teaching_experience_description"] = _validate_text(text, "a short description of your experience")
        else:
            # Choice/multiselect steps are keyboard-driven; a typed answer is
            # informational only and keeps the user on the same step.
            await update.message.reply_text("Please use the buttons above for this step.")
            return state
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return state

    return await _advance(update, context, state)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    state = current_step_state(context)
    if state is None:
        return ConversationHandler.END
    step = STEPS_BY_STATE[state]
    app = get_app(context)

    voice: Voice | None = update.message.voice
    if voice is None or state != int(EthiopianTutorStates.ENGLISH_VOICE):
        return state

    app["english_voice_file_id"] = voice.file_id
    app["english_voice_message_id"] = update.message.message_id
    app["_awaiting_voice"] = False
    await update.message.reply_text("🎤 Voice introduction received — thank you!")
    return await _advance(update, context, state)


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    state = current_step_state(context)
    if state is None:
        return ConversationHandler.END
    step = STEPS_BY_STATE.get(state)
    app = get_app(context)

    if state != int(EthiopianTutorStates.DOCUMENTS):
        await update.message.reply_text("⚠️ Please upload documents at the Documents step.")
        return state

    document = update.message.document
    photo = update.message.photo[-1] if update.message.photo else None
    if document is not None:
        file_id = document.file_id
        file_unique = document.file_unique_id
        file_name = document.file_name
        mime = document.mime_type
    elif photo is not None:
        file_id = photo.file_id
        file_unique = photo.file_unique_id
        file_name = "photo.jpg"
        mime = "image/jpeg"
    else:
        await update.message.reply_text("⚠️ Please send a PDF or an image.")
        return state

    try:
        profile_service.validate_document_file(file_name, mime)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return state

    app.setdefault("documents", []).append(
        {
            "document_type": str(DocumentType.SUPPORTING),
            "telegram_file_id": file_id,
            "telegram_file_unique_id": file_unique,
            "telegram_message_id": update.message.message_id,
            "file_name": file_name,
            "mime_type": mime,
        }
    )
    await update.message.reply_text("📄 Document received. You can send another, or tap Skip/Done when finished.")
    return state


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


async def submit_application(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    app = get_app(context)
    missing = _missing_fields(app)
    if missing:
        await _safe_reply(
            update,
            "⚠️ Please complete the following before submitting:\n• " + "\n• ".join(missing),
        )
        return int(EthiopianTutorStates.SUMMARY)

    payload = {
        "telegram_user_id": update.effective_user.id,
        "full_name": app.get("full_name"),
        "phone": app.get("phone"),
        "gender": app.get("gender"),
        "age": app.get("age"),
        "current_address": app.get("current_address"),
        "locations": app.get("locations") or [],
        "grades": app.get("grades") or [],
        "subjects": app.get("subjects") or [],
        "english_proficiency": app.get("english_proficiency"),
        "english_voice_file_id": app.get("english_voice_file_id"),
        "english_voice_message_id": app.get("english_voice_message_id"),
        "education_level": app.get("education_level"),
        "university": app.get("university"),
        "department": app.get("department"),
        "university_year": app.get("university_year"),
        "cgpa": app.get("cgpa"),
        "entrance_exam_type": app.get("entrance_exam_type"),
        "entrance_exam_max_score": app.get("entrance_exam_max_score"),
        "entrance_exam_score": app.get("entrance_exam_score"),
        "entrance_exam_year": app.get("entrance_exam_year"),
        "teaching_experience_years": app.get("teaching_experience_years"),
        "teaching_experience_description": app.get("teaching_experience_description"),
        "documents": app.get("documents") or [],
    }

    try:
        with session_scope() as session:
            tutor = profile_service.create_ethiopian_tutor(session, payload)
        tdr_id = tutor.public_tutor_id
    except profile_service.ProfileValidationError as exc:
        await _safe_reply(update, f"⚠️ {exc}")
        return int(EthiopianTutorStates.SUMMARY)
    except Exception:
        logger.exception("Ethiopian tutor creation failed")
        await _safe_reply(update, "⚠️ Something went wrong saving your profile. Please try again.")
        return int(EthiopianTutorStates.SUMMARY)

    # Best-effort Telegram archive: docs captured, not re-uploaded here.
    await _safe_reply(
        update,
        "✅ <b>Application received!</b>\n\n"
        f"Your reference is <code>{tdr_id}</code>.\n\n"
        "Our team will review your profile and verify your documents, then you "
        "will appear in job recommendations. You can check your status any time "
        "with /my_application.",
    )
   
    with session_scope() as session:
        try:
            tutor = profile_service_create_tutor_lookup(session, update.effective_user.id)
        except Exception:
            tutor = None
        if tutor is not None:
            await notify_admin_new_tutor(context.bot, tutor)

    # Clear the onboarding data.
    context.user_data.pop(APP_KEY, None)
    context.user_data.pop(STEP_KEY, None)
    context.user_data.pop(DOC_QUEUE_KEY, None)
    return ConversationHandler.END


def profile_service_create_tutor_lookup(session, telegram_user_id: int):
    return tutor_service.get_tutor_by_telegram_id(session, telegram_user_id)


def _missing_fields(app: dict[str, Any]) -> list[str]:
    required = [
        ("full_name", "Full name"),
        ("phone", "Phone number"),
        ("current_address", "Current address"),
        ("gender", "Gender"),
        ("age", "Age"),
        ("locations", "Tutoring locations"),
        ("grades", "Grades"),
        ("subjects", "Subjects"),
        ("english_proficiency", "English level"),
        ("education_level", "Education level"),
        ("university", "University"),
        ("department", "Department"),
        ("university_year", "University year"),
        ("entrance_exam_type", "Entrance exam type"),
        ("entrance_exam_max_score", "Entrance exam max score"),
        ("entrance_exam_score", "Entrance exam score"),
        ("entrance_exam_year", "Entrance exam year"),
    ]
    missing = []
    for key, label in required:
        if key in ("locations", "grades", "subjects"):
            if not app.get(key):
                missing.append(label)
        else:
            if app.get(key) in (None, "", []):
                missing.append(label)
    return missing


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(APP_KEY, None)
    context.user_data.pop(STEP_KEY, None)
    await _safe_reply(update, CANCEL_TEXT_LOCAL)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


async def start_application(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    GET = get_app(context)
    GET.clear()
    set_step(context, int(EthiopianTutorStates.FULL_NAME))
    await _safe_reply(
        update,
        "👋 <b>Welcome to Tedor Tutors!</b>\n\n"
        "We're excited you want to teach with us. I'll collect a few structured details "
        "(about 3 minutes), then our team verifies your documents.\n\n"
        "Ready? Let's begin with your personal details.\n\n"
        "Use ⬅️ Back, ✏️ Edit, ❌ Cancel, or ✅ Continue at any step.",
    )
    return await _prompt(update, context, int(EthiopianTutorStates.FULL_NAME))


def build_conversation() -> ConversationHandler:
    """Wire the states and handlers into a ConversationHandler."""
    states: dict[int, list[Any]] = {}
    for state_value in STATE_ORDER:
        states[state_value] = [
            CallbackQueryHandler(callback_handler),
            MessageHandler(filters.VOICE, voice_handler),
            MessageHandler(filters.Document.ALL | filters.PHOTO, document_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler),
        ]

    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_application, pattern=r"^menu:become_tutor_ethiopia$"),
            CommandHandler("apply", start_application),
            CommandHandler("apply_ethiopia", start_application),
            CommandHandler("complete_profile", complete_profile_entry),
        ],
        states=states,
        fallbacks=[
            CommandHandler("cancel", _cancel),
            MessageHandler(filters.COMMAND, _cancel),
        ],
        name="ethiopian_tutor",
        allow_reentry=True,
        per_message=False,
    )


async def complete_profile_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """§26: let a returning tutor fill only the information they skipped."""
    # Report outstanding fields while still inside the session (lazy loads of
    # the related collections must not be triggered after the session closes).
    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_telegram_id(session, update.effective_user.id)
        if tutor is None:
            await update.message.reply_text(
                "We don't have your profile yet. Tap Become a Tutor to apply first."
            )
            return ConversationHandler.END
        missing_labels = profile_service.missing_profile_labels(tutor)
        if not missing_labels:
            await update.message.reply_text("✅ Your profile is complete! Nothing to do.")
            return ConversationHandler.END

        await update.message.reply_text(
            "✏️ <b>Profile completion</b>\n\n"
            "We still need:\n• " + "\n• ".join(missing_labels) + "\n\n"
            "Tap Become a Tutor and retake the steps you skipped.",
            parse_mode=ParseMode.HTML,
        )
    return ConversationHandler.END


__all__ = ["build_conversation", "start_application", "complete_profile_entry"]