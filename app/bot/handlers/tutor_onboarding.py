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
from collections.abc import Callable, Sequence
from typing import Any, Final

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
#: The ordered steps for *this* run. A fresh application walks every step;
#: an enrichment run walks only the ones still outstanding (section 26).
SEQUENCE_KEY = "ethiopian_tutor_sequence"
#: ``True`` while enriching an existing profile, which changes how submission
#: is applied — see :func:`submit_application`.
MODE_KEY = "ethiopian_tutor_mode"

MODE_APPLY = "apply"
MODE_ENRICH = "enrich"

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


def next_state(state: int, sequence: list[int] | None = None) -> int:
    order = sequence if sequence is not None else STATE_ORDER
    index = order.index(int(state))
    return order[index + 1] if index + 1 < len(order) else int(state)


def prev_state(state: int, sequence: list[int] | None = None) -> int:
    order = sequence if sequence is not None else STATE_ORDER
    index = order.index(int(state))
    return order[index - 1] if index > 0 else order[0]


def sequence_for(context: ContextTypes.DEFAULT_TYPE) -> list[int]:
    """The ordered steps for the run in progress.

    Falls back to the full sequence so a context that was never seeded still
    behaves like a fresh application.
    """
    stored = context.user_data.get(SEQUENCE_KEY)
    return list(stored) if stored else list(STATE_ORDER)


def mode_for(context: ContextTypes.DEFAULT_TYPE) -> str:
    return str(context.user_data.get(MODE_KEY) or MODE_APPLY)


def is_enrichment(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return mode_for(context) == MODE_ENRICH


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
    # ``edu_level`` and ``exam_type`` name the option set for the education and
    # exam-type steps; they are single-select like ``choice`` and must render
    # their buttons too, otherwise the tutor is left with navigation only.
    if step.kind in ("choice", "edu_level", "exam_type"):
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
        return english_level_keyboard(step.field)
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


def _choice_value(field: str, index_text: str) -> str:
    """Map a pick index back to the stored value.

    The field comes from the callback rather than from the current state, so a
    button always resolves against the options it was rendered with. An index
    outside the option list is a stale button, which is reported to the tutor
    instead of raising.
    """
    try:
        index = int(index_text)
    except ValueError:
        raise ValueError("Please use the buttons to choose.") from None

    def _pick(options: Sequence[Any], cast=lambda item: item) -> str:
        if not 0 <= index < len(options):
            raise ValueError("That option is no longer available — please tap again.")
        return str(cast(options[index]))

    if field == "gender":
        return _pick(("Male", "Female", "Other"))
    if field == "education_level":
        return _pick(ETH_EDUCATION_LEVELS, lambda item: item[0])
    if field == "entrance_exam_type":
        return _pick(ETH_ENTRANCE_EXAM_TYPES, lambda item: item[0])
    if field == "english_proficiency":
        return _pick(ETH_ENGLISH_LEVELS, lambda item: item[0])
    raise ValueError("Unsupported choice field")


async def _advance(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int) -> int:
    """Move to the next step of *this run*, then prompt.

    Uses the run's own sequence rather than the full one, so an enrichment run
    that only collected locations jumps straight to its summary instead of
    walking the 20 steps the tutor already answered years ago.
    """
    target = next_state(state, sequence_for(context))
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
        return await _prompt(update, context, prev_state(state, sequence_for(context)))
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
        # Older keyboards were built before the step was named after the field
        # it stores, so the alias is resolved once here: both the lookup and the
        # stored answer then use the canonical name.
        field = _FIELD_ALIASES_FROM_CALLBACK.get(parts[1], parts[1])
        try:
            value = _choice_value(field, parts[3])
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return state
        app[field] = value
        if field == "english_proficiency":
            app["english_proficiency"] = int(value)
        return await _advance(update, context, state)

    # eth:<field>:toggle:<n> / eth:<field>:done
    if len(parts) == 4 and parts[0] == "eth" and parts[2] == "toggle":
        # The field comes from the callback, not from the current state: the
        # button belongs to the step that rendered it, and during an enrichment
        # run the sequence differs from the full flow's.
        field = parts[1]
        options = {
            "locations": list(ETH_LOCATIONS),
            "grades": list(ETH_GRADES),
            "subjects": list(SUBJECTS),
        }.get(field, [])
        try:
            option = options[int(parts[3])]
        except (ValueError, IndexError):
            return state
        selected: list[str] = list(app.get(field) or [])
        if option in selected:
            selected.remove(option)
        else:
            selected.append(option)
        app[field] = selected
        try:
            await query.edit_message_reply_markup(
                reply_markup=multiselect_keyboard(field, options, selected)
            )
        except Exception:
            pass
        return state

    if len(parts) == 3 and parts[0] == "eth" and parts[2] == "done":
        selected = app.get(parts[1]) or []
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
    """Persist the answers, either creating a profile or completing one."""
    if is_enrichment(context):
        return await _submit_enrichment(update, context)
    return await _submit_application(update, context)


def _summary_missing(app: dict[str, Any]) -> list[str]:
    """Outstanding fields for the run in progress.

    During enrichment the tutor already satisfies most of the required list, so
    only the fields this run was actually asked for can be missing.
    """
    if app.get("_enriching"):
        outstanding = app.get("_required") or []
    else:
        outstanding = list(_REQUIRED_FIELDS)
    missing = []
    for key, label in outstanding:
        if key in ("locations", "grades", "subjects"):
            if not app.get(key):
                missing.append(label)
        elif app.get(key) in (None, "", []):
            missing.append(label)
    return missing


async def _submit_application(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Requirement 2.7: persist, archive in Telegram, notify the admin."""
    app = get_app(context)
    telegram_user_id = update.effective_user.id
    missing = _summary_missing(app)
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

    _clear_run(context)
    return ConversationHandler.END


def _clear_run(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (APP_KEY, STEP_KEY, DOC_QUEUE_KEY, SEQUENCE_KEY, MODE_KEY):
        context.user_data.pop(key, None)


async def _submit_enrichment(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Apply the collected answers to an existing profile (section 26).

    Never creates a second tutor and never re-asks what is already stored: the
    values go through the same validators as the original application, so the
    enrichment path cannot accept something the full flow would reject.
    """
    app = get_app(context)
    missing = _summary_missing(app)
    if missing:
        await _safe_reply(
            update,
            "⚠️ Still missing:\n• " + "\n• ".join(missing),
        )
        return int(EthiopianTutorStates.SUMMARY)

    scalars: dict[str, Any] = {}
    for key in (
        "gender",
        "age",
        "current_address",
        "university",
        "department",
        "university_year",
        "cgpa",
        "education_level",
        "entrance_exam_type",
        "entrance_exam_max_score",
        "entrance_exam_score",
        "entrance_exam_year",
        "english_proficiency",
        "teaching_experience_years",
        "teaching_experience_description",
    ):
        if key in app and app.get(key) not in (None, "", []):
            scalars[key] = app[key]

    try:
        with session_scope() as session:
            tutor = tutor_service.get_tutor_by_telegram_id(
                session, update.effective_user.id
            )
            if tutor is None:
                await _safe_reply(
                    update,
                    "We could not find your profile. Please contact support.",
                )
                return ConversationHandler.END

            if app.get("locations"):
                profile_service.set_locations(session, tutor, app["locations"])
            if app.get("grades"):
                profile_service.set_grades(session, tutor, app["grades"])
            if app.get("subjects"):
                profile_service.set_subjects(session, tutor, app["subjects"])
            if scalars:
                profile_service.update_profile_fields(session, tutor, scalars)
            if app.get("teaching_experience_years") is not None:
                tutor.teaching_experience_years = int(app["teaching_experience_years"])
                tutor.experience_years = int(app["teaching_experience_years"])
            for document in app.get("documents") or []:
                profile_service.record_document(
                    session,
                    tutor,
                    document_type=document.get("document_type"),
                    file_id=document.get("telegram_file_id"),
                    file_unique_id=document.get("telegram_file_unique_id"),
                    message_id=document.get("telegram_message_id"),
                    file_name=document.get("file_name"),
                    mime_type=document.get("mime_type"),
                )
            session.flush()

            after = profile_service.completion_report(tutor)
            tdr_id = tutor.public_tutor_id
            still_missing = after["missing_labels"]
    except profile_service.ProfileValidationError as exc:
        await _safe_reply(update, f"⚠️ {exc}")
        return int(EthiopianTutorStates.SUMMARY)
    except Exception:
        logger.exception("Profile enrichment failed")
        await _safe_reply(
            update, "⚠️ Something went wrong saving your profile. Please try again."
        )
        return int(EthiopianTutorStates.SUMMARY)

    text = (
        "✅ <b>Profile updated!</b>\n\n"
        f"Your reference is <code>{tdr_id}</code>.\n"
        f"Profile completeness: <b>{after['percentage']}%</b>"
    )
    if still_missing:
        text += "\n\n⚠ Still missing:\n• " + "\n• ".join(still_missing)
    else:
        text += "\n\nYour profile is now complete. 🎉"

    _clear_run(context)
    await _safe_reply(update, text)
    return ConversationHandler.END


#: What a *full* application must collect before it can be submitted.
#: Personal details and the three collections are mandatory (section 23); the
#: academic block is required of the application but is re-askable, so an
#: enrichment run is judged only on what it was actually asked for.
_REQUIRED_FIELDS: Final[tuple[tuple[str, str], ...]] = (
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
)


def _missing_fields(app: dict[str, Any]) -> list[str]:
    """Outstanding required fields for a full application."""
    return _summary_missing({**app, "_required": list(_REQUIRED_FIELDS)})


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


# ---------------------------------------------------------------------------
# enrichment planning (section 26)
# ---------------------------------------------------------------------------

#: The entrance exam is validated against its own maximum, so filling any one
#: of these in isolation would leave the answer meaningless. The whole chain is
#: therefore requested together, in this order, whenever any part is missing.
_ENTRANCE_CHAIN: Final[tuple[int, ...]] = (
    EthiopianTutorStates.EDUCATION_LEVEL,
    EthiopianTutorStates.ENTRANCE_TYPE,
    EthiopianTutorStates.ENTRANCE_MAX_SCORE,
    EthiopianTutorStates.ENTRANCE_SCORE,
    EthiopianTutorStates.ENTRANCE_YEAR,
)


#: Every step's collected field, including the sub-steps that are not keyed by
#: a :mod:`tutor_profile_service` field (the entrance-exam chain, department,
#: university year and the free-text experience description).
_STATE_FIELDS: Final[dict[int, str]] = {
    int(EthiopianTutorStates.FULL_NAME): "full_name",
    int(EthiopianTutorStates.GENDER): "gender",
    int(EthiopianTutorStates.AGE): "age",
    int(EthiopianTutorStates.PHONE): "phone",
    int(EthiopianTutorStates.CURRENT_ADDRESS): "current_address",
    int(EthiopianTutorStates.LOCATIONS): "locations",
    int(EthiopianTutorStates.GRADES): "grades",
    int(EthiopianTutorStates.SUBJECTS): "subjects",
    int(EthiopianTutorStates.ENGLISH): "english_proficiency",
    int(EthiopianTutorStates.EDUCATION_LEVEL): "education_level",
    int(EthiopianTutorStates.UNIVERSITY): "university",
    int(EthiopianTutorStates.DEPARTMENT): "department",
    int(EthiopianTutorStates.UNIVERSITY_YEAR): "university_year",
    int(EthiopianTutorStates.CGPA): "cgpa",
    int(EthiopianTutorStates.ENTRANCE_TYPE): "entrance_exam_type",
    int(EthiopianTutorStates.ENTRANCE_MAX_SCORE): "entrance_exam_max_score",
    int(EthiopianTutorStates.ENTRANCE_SCORE): "entrance_exam_score",
    int(EthiopianTutorStates.ENTRANCE_YEAR): "entrance_exam_year",
    int(EthiopianTutorStates.EXPERIENCE_YEARS): "experience",
    int(EthiopianTutorStates.EXPERIENCE_DESCRIPTION): "teaching_experience_description",
    int(EthiopianTutorStates.DOCUMENTS): "documents",
}


def planned_fields(steps: list[int]) -> set[str]:
    """The profile fields a planned step list will collect."""
    return {
        _STATE_FIELDS[state] for state in steps if state in _STATE_FIELDS
    }


def enrichment_steps(tutor) -> list[int]:
    """The ordered steps still outstanding for ``tutor``.

    Follows the section 26 priority — tutoring locations, grades, subjects,
    English, entrance exam, university, experience, documents — and adds the
    personal fields only when they are genuinely absent, so an existing tutor
    is never asked to repeat what they already told us.
    """
    from app.services.tutor_profile_service import missing_profile_fields

    missing = set(missing_profile_fields(tutor))
    steps: list[int] = []

    # Personal details, only if actually unknown.
    for state in (
        EthiopianTutorStates.FULL_NAME,
        EthiopianTutorStates.GENDER,
        EthiopianTutorStates.AGE,
        EthiopianTutorStates.PHONE,
        EthiopianTutorStates.CURRENT_ADDRESS,
    ):
        field = _STATE_FIELDS[int(state)]
        if not _has(tutor, field):
            steps.append(int(state))

    # Section 26's priority order for the structured fields.
    for state in (
        EthiopianTutorStates.LOCATIONS,
        EthiopianTutorStates.GRADES,
        EthiopianTutorStates.SUBJECTS,
        EthiopianTutorStates.ENGLISH,
    ):
        if not _has(tutor, _STATE_FIELDS[int(state)]):
            steps.append(int(state))

    # The entrance exam is asked as one chain when any of it is missing.
    if _entrance_incomplete(tutor):
        steps.extend(int(state) for state in _ENTRANCE_CHAIN)

    if not _has(tutor, "university"):
        steps.append(int(EthiopianTutorStates.UNIVERSITY))
        steps.append(int(EthiopianTutorStates.DEPARTMENT))
        if tutor.university_year is None:
            steps.append(int(EthiopianTutorStates.UNIVERSITY_YEAR))

    if "experience" in missing:
        steps.append(int(EthiopianTutorStates.EXPERIENCE_YEARS))
        if not tutor.teaching_experience_description:
            steps.append(int(EthiopianTutorStates.EXPERIENCE_DESCRIPTION))

    if "documents" in missing:
        steps.append(int(EthiopianTutorStates.DOCUMENTS))

    # Never empty and always ending at the summary.
    steps.append(int(EthiopianTutorStates.SUMMARY))
    return steps


#: Fields whose conversation name differs from the column they live in.
_FIELD_ALIASES: Final[dict[str, str]] = {
    # There is deliberately no ``full_name`` column; ``name`` holds it.
    "full_name": "name",
}

#: Readable labels for the fields the enrichment flow asks about. Taken from
#: ``_REQUIRED_FIELDS`` where one exists, otherwise title-cased.
_REQUIRED_LABELS: Final[dict[str, str]] = dict(_REQUIRED_FIELDS)


def _step_label(field: str) -> str:
    label = _REQUIRED_LABELS.get(field)
    if label:
        return label
    return field.replace("_", " ").capitalize()


#: Callback field names that predate the step being named after the field it
#: stores. Resolved on the way in so buttons that are already in a tutor's chat
#: keep working and still record the answer under the canonical name.
_FIELD_ALIASES_FROM_CALLBACK: Final[dict[str, str]] = {
    "english": "english_proficiency",
}

#: Structured university questions an imported tutor has already answered in the
#: legacy education record, mapped to the attribute that answers them.
_LEGACY_EDUCATION_FIELDS: Final[dict[str, Callable[[Any], Any]]] = {
    "university": lambda record: record.institution,
    "department": lambda record: record.field or record.degree,
    "university_year": lambda record: record.graduation_year,
}


def _has(tutor, field: str) -> bool:
    """Whether a field is already filled on the stored tutor."""
    if field == "locations":
        return bool(tutor.tutoring_locations)
    if field == "grades":
        return bool(tutor.grades)
    if field == "subjects":
        return bool(tutor.subjects)
    if field == "documents":
        return bool(tutor.documents)
    if field in _LEGACY_EDUCATION_FIELDS:
        # An imported tutor's university lives in the legacy education record,
        # not in the structured columns. Matching already reads that record, so
        # asking again would only duplicate what is on file.
        record = tutor.education[0] if tutor.education else None
        return bool(record and _LEGACY_EDUCATION_FIELDS[field](record))
    return bool(getattr(tutor, _FIELD_ALIASES.get(field, field), None))


def _entrance_incomplete(tutor) -> bool:
    """True when the entrance exam needs (re)asking.

    ``entrance_exam_max_score`` is deliberately part of the test: a tutor with
    a raw score but no recorded scale cannot be compared to anyone, so the whole
    chain is re-asked.
    """
    return (
        tutor.entrance_exam_score is None
        or tutor.entrance_exam_max_score is None
        or tutor.entrance_exam_type is None
        or tutor.education_level is None
    )


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
    """§26: let a returning tutor fill only the information they skipped.

    Builds the step list from what the stored profile is actually missing, so
    nobody is asked to retype an answer they already gave. The tutor is loaded
    inside the session and only plain values cross the boundary; their ORM
    object must not outlive it.
    """
    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_telegram_id(session, update.effective_user.id)
        if tutor is None:
            await update.message.reply_text(
                "We don't have your profile yet. Tap Become a Tutor to apply first."
            )
            return ConversationHandler.END
        # Reuse what an imported tutor already recorded, so the plan below never
        # asks for it and the completeness report tells the same story.
        profile_service.backfill_education_from_legacy(session, tutor)
        steps = enrichment_steps(tutor)
        before = tutor_profile_completion(tutor)

    if steps == [int(EthiopianTutorStates.SUMMARY)]:
        await update.message.reply_text(
            "✅ <b>Your profile is already complete</b> — nothing left to fill in.",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    # Seed the run: only the outstanding steps, in enrichment mode, and with
    # the tutor's existing answers pre-filled so the summary reads correctly.
    application = get_app(context)
    application.clear()
    application["_enriching"] = True
    # Only the fields this run asks about can block submission; the rest are
    # already stored on the tutor and are not re-requested.
    application["_required"] = [
        entry for entry in _REQUIRED_FIELDS if entry[0] in planned_fields(steps)
    ]
    context.user_data[SEQUENCE_KEY] = steps
    context.user_data[MODE_KEY] = MODE_ENRICH
    set_step(context, steps[0])

    # The bullets name the steps this run will actually ask for. Deriving them from
    # the plan rather than from the completeness report keeps the two in step:
    # the score does not cover every field the flow collects, so it can be 100%
    # while personal details are still outstanding.
    asked = [_step_label(field) for field in planned_fields(steps)]
    await update.message.reply_text(
        "✏️ <b>Profile completion</b>\n\n"
        f"You are at <b>{before}%</b>. Let's fill in what's missing:\n• "
        + "\n• ".join(asked)
        + "\n\nUse ⬅️ Back, ✏️ Edit, ❌ Cancel or ✅ Continue.",
        parse_mode=ParseMode.HTML,
    )
    return await _prompt(update, context, steps[0])


def tutor_profile_completion(tutor) -> int:
    return profile_service.profile_completion_percentage(tutor)


__all__ = ["build_conversation", "start_application", "complete_profile_entry"]