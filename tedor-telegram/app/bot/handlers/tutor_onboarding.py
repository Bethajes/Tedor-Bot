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
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

from telegram import Document, InlineKeyboardButton, InlineKeyboardMarkup, Update, Voice
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

from app.bot.handlers import location_prompts, location_selector
from app.bot.handlers.common import end_flow, start_flow
from app.bot.keyboards.tutor_onboarding import (
    CB_BACK,
    CB_CANCEL,
    CB_CONTINUE,
    CB_DONE,
    CB_EDIT,
    CB_SUBMIT,
    choice_keyboard,
    documents_keyboard,
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
    ETH_GRADES,
    ETH_STREAMS,
    SUBJECTS,
)
from app.bot.states import CANCEL_TEXT, EthiopianTutorStates
from app.database import session_scope
from app.enums import DocumentType
from app.services import location_registry_service
from app.services import tutor_profile_service as profile_service
from app.services import tutor_service
from app.services.notification_service import notify_admin_new_tutor
from app.services.storage_service import TelegramStorageService

logger = logging.getLogger(__name__)

APP_KEY = "ethiopian_tutor_app"
STEP_KEY = "ethiopian_tutor_step"
HOME_AREA_KEY = "pending_home_area"
DOC_QUEUE_KEY = "ethiopian_tutor_doc_queue"
#: The ordered steps for *this* run. A fresh application walks every step;
#: an enrichment run walks only the ones still outstanding (section 26).
SEQUENCE_KEY = "ethiopian_tutor_sequence"
#: ``True`` while enriching an existing profile, which changes how submission
#: is applied — see :func:`submit_application`.
MODE_KEY = "ethiopian_tutor_mode"

MODE_APPLY = "apply"
MODE_ENRICH = "enrich"

#: The job reference this run was started for, when the tutor arrived through
#: a job post's Apply button rather than the main menu. Present only for that
#: one run; :func:`take_resume_job` consumes it so a later, unrelated
#: application is never silently attached to the job they applied for.
RESUME_JOB_KEY = "ethiopian_tutor_resume_job"

CANCEL_TEXT_LOCAL = (
    "❌ Application cancelled. Type /start or tap Become a Tutor to begin again."
)


def set_resume_job(context: ContextTypes.DEFAULT_TYPE, job_reference: str) -> None:
    """Remember which job this profile run is being completed for."""
    context.user_data[RESUME_JOB_KEY] = str(job_reference or "").strip().upper()


def take_resume_job(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Consume the pending job reference, if there is one."""
    return context.user_data.pop(RESUME_JOB_KEY, None) or None


def peek_resume_job(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """The pending job reference without consuming it."""
    return context.user_data.get(RESUME_JOB_KEY) or None


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
         "👤 <b>Step 1/27 — 👤 Full name</b>\n\nYour full name as it appears on your national ID.", "name"),
    Step(EthiopianTutorStates.GENDER, "gender",
         "👤 <b>Step 2/27 — 👤 Gender</b>\n\nTap your choice.", "choice"),
    Step(EthiopianTutorStates.AGE, "age",
         "🎂 <b>Step 3/27 — 🎂 Age</b>\n\nYour age in years (18 or older).", "int"),
    Step(EthiopianTutorStates.PHONE, "phone",
         "📞 <b>Step 4/27 — 📞 Phone number</b>\n\nInclude your country code, e.g. <code>+251911234567</code>.", "phone"),
    Step(EthiopianTutorStates.CURRENT_ADDRESS, "current_address",
         "📍 <b>Step 5/27 — 📍 Current address</b>\n\nWhere you live now, e.g. <code>Bole, Addis Ababa</code>.", "address"),
    Step(EthiopianTutorStates.LOCATIONS, "locations",
         "📍 <b>Step 6/27 — 📍 Tutoring areas</b>\n\n"
         "Type the areas you can tutor in and pick from the suggestions. "
         "If your area is missing, add it manually.",
         "location"),
    Step(EthiopianTutorStates.GRADES, "grades",
         "🎓 <b>Step 7/27 — 🎓 Grade groups you can tutor</b>\n\nSelect all that apply.", "multiselect"),
    Step(EthiopianTutorStates.SUBJECTS, "subjects",
         "📚 <b>Step 8/27 — 📚 Subjects you can tutor</b>\n\nSelect all that apply.", "multiselect"),
    Step(EthiopianTutorStates.ENGLISH, "english_proficiency",
         "🇬🇧 <b>Step 9/27 — 🇬🇧 English communication skill</b>\n\n1–10. This is your own rating; an optional recording helps our admin verify it.", "english"),
    Step(EthiopianTutorStates.ENGLISH_VOICE, "english_voice",
         "🎤 <b>Step 10/27 — 🎤 English voice introduction (optional)</b>\n\nSend a 30–60 second voice message in English, or skip.\n\n<i>This is supporting evidence only — it is reviewed by an admin, not auto-scored.</i>", "voice", optional=True),
    Step(EthiopianTutorStates.EDUCATION_LEVEL, "education_level",
         "🎓 <b>Step 11/27 — 🎓 Current / highest education</b>", "edu_level"),
    Step(EthiopianTutorStates.UNIVERSITY, "university",
         "🏫 <b>Step 12/27 — 🏫 University / institution</b>\n\nWhich university or college did you attend?", "text"),
    Step(EthiopianTutorStates.DEPARTMENT, "department",
         "🧪 <b>Step 13/27 — 🧪 Department</b>\n\nYour department or field of study.", "text"),
    Step(EthiopianTutorStates.UNIVERSITY_YEAR, "university_year",
         "📅 <b>Step 14/27 — 📅 Year</b>\n\nYour graduation year (or current year of study).", "year"),
    Step(EthiopianTutorStates.CGPA, "cgpa",
         "📈 <b>Step 15/27 — 📈 CGPA (if applicable)</b>\n\nYour current/last CGPA, e.g. <code>3.75</code>.", "cgpa", optional=True),
    Step(EthiopianTutorStates.ENTRANCE_MAX_SCORE, "entrance_exam_max_score",
         "⚖️ <b>Step 16/27 — ⚖️ Maximum score for that exam</b>\n\nE.g. <code>700</code> (current entrance exam) or <code>600</code> (older one). Stored so scores from different scales are normalised.", "max_score"),
    Step(EthiopianTutorStates.ENTRANCE_SCORE, "entrance_exam_score",
         "🏅 <b>Step 17/27 — 🏅 Your entrance exam score</b>\n\nE.g. <code>612</code>.", "exam_score"),
    Step(EthiopianTutorStates.ENTRANCE_YEAR, "entrance_exam_year",
         "📆 <b>Step 18/27 — 📆 Exam year</b>\n\nThe year you took that exam, e.g. <code>2016</code>.", "year"),
    Step(EthiopianTutorStates.ACADEMIC_STREAM, "academic_stream",
         "🧭 <b>Step 19/27 — 🧭 Preparatory stream you studied</b>\n\nTap your stream (Natural or Social). This tells us which preparatory subjects you can cover.", "academic_stream"),
    Step(EthiopianTutorStates.GRADE8_AVERAGE, "grade8_exam_average",
         "🧮 <b>Step 20/27 — Grade 8 exam average (optional)</b>\n\nThe average you scored in the Grade 8 national exam, out of 100 — e.g. <code>82</code>.\n\n<i>Used when ranking tutors. Tap Continue to skip.</i>", "average", optional=True),
    Step(EthiopianTutorStates.GRADE9_AVERAGE, "grade9_average",
         "🧮 <b>Step 21/27 — Grade 9 average (optional)</b>\n\nYour Grade 9 average out of 100, e.g. <code>78</code>.\n\n<i>Used when ranking tutors. Tap Continue to skip.</i>", "average", optional=True),
    Step(EthiopianTutorStates.GRADE10_AVERAGE, "grade10_average",
         "🧮 <b>Step 22/27 — Grade 10 average (optional)</b>\n\nYour Grade 10 average out of 100, e.g. <code>80</code>.\n\n<i>Used when ranking tutors. Tap Continue to skip.</i>", "average", optional=True),
    Step(EthiopianTutorStates.GRADE11_AVERAGE, "grade11_average",
         "🧮 <b>Step 23/27 — Grade 11 average (optional)</b>\n\nYour Grade 11 average out of 100, e.g. <code>84</code>.\n\n<i>Used when ranking tutors. Tap Continue to skip.</i>", "average", optional=True),
    Step(EthiopianTutorStates.GRADE12_AVERAGE, "grade12_average",
         "🧮 <b>Step 24/27 — Grade 12 average (optional)</b>\n\nYour Grade 12 average out of 100, e.g. <code>86</code>.\n\n<i>Used when ranking tutors. Tap Continue to skip.</i>", "average", optional=True),
    Step(EthiopianTutorStates.EXPERIENCE_YEARS, "teaching_experience_years",
         "💼 <b>Step 25/27 — Teaching experience</b>\n\nHow many years of tutoring/teaching experience do you have? Whole number.", "int", optional=True),
    Step(EthiopianTutorStates.EXPERIENCE_DESCRIPTION, "teaching_experience_description",
         "🧑‍🏫 <b>Step 26/27 — Experience details</b>\n\nBriefly describe your experience — subjects taught, grades, and where (e.g. private tutoring, school).", "text", optional=True),
    Step(EthiopianTutorStates.DOCUMENTS, "documents",
         "📄 <b>Step 27/27 — 📄 Academic documents (required)</b>\n\nUpload your <b>university transcript</b> and your <b>university entrance exam result</b> (PDF or image).\n\nTap a type below, then send the file. Keep pressing Done when finished.", "documents"),
    Step(EthiopianTutorStates.SUMMARY, "summary", "", "summary"),
)

#: The secondary-result steps in the order the flow asks for them, paired with
#: the column each one stores. Kept beside :data:`STEPS` so the form and the
#: ranking signal cannot drift apart.
SECONDARY_RESULT_STATES: Final[dict[str, int]] = {
    step.field: int(step.state)
    for step in STEPS
    if step.field in {field for field, _label in profile_service.SECONDARY_RESULT_FIELDS}
}

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

    if step.kind == "location":
        return await _prompt_location(update, context, step, state)

    await _safe_reply(update, step.prompt, _keyboard_for(step, get_app(context)))
    return state


async def _prompt_location(
    update: Update, context: ContextTypes.DEFAULT_TYPE, step: Step, state: int
) -> int:
    """Open the shared selector at the tutoring-areas step.

    The tutor's teaching mode is passed in so an online-only tutor can finish
    the step empty — for them, location is not a constraint at all.
    """
    app = get_app(context)
    return await location_selector.on_enter(
        update,
        context,
        state=state,
        field=step.field,
        teaching_mode=str(app.get("teaching_mode") or "IN_PERSON"),
        preselect=app.get("location_selections") or app.get("locations") or [],
    ) or state


def _keyboard_for(step: Step, app: dict[str, Any]):
    # ``edu_level`` and ``academic_stream`` name the option set for those
    # single-select steps; they are single-select like ``choice`` and must
    # render their buttons too, otherwise the tutor is left with navigation only.
    if step.kind in ("choice", "edu_level", "academic_stream"):
        if step.field == "education_level":
            return choice_keyboard("education_level", [label for _k, label in ETH_EDUCATION_LEVELS])
        if step.field == "academic_stream":
            return choice_keyboard("academic_stream", [label for _k, label in ETH_STREAMS])
        if step.field == "gender":
            return choice_keyboard("gender", ["Male", "Female", "Other"])
        return choice_keyboard(step.field, [])
    if step.kind == "multiselect":
        options = {
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
        uploaded = set(app.get("documents") or [])
        return documents_keyboard(uploaded, app.get("_pending_doc_type"))
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

    # They gave an address but picked nowhere to teach. Offer that address here,
    # where it costs nothing to answer: a home area must become a tutoring
    # location only because the tutor said so, never because it was nearby.
    keyboard = summary_keyboard()
    address = context.user_data.pop(HOME_AREA_KEY, "") or ""
    if address and not app.get("locations"):
        ask = await location_selector.ask_about_home_area(
            update, context, state=int(EthiopianTutorStates.SUMMARY), home_area=address
        )
        if ask is not None:
            keyboard = location_selector.kb.home_area_keyboard()

    try:
        if update.callback_query is not None:
            await update.callback_query.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Failed to render summary")
    return int(EthiopianTutorStates.SUMMARY)


def _summary_lines(app: dict[str, Any]) -> list[str]:
    def fmt(value: Any, default: str = "—") -> str:
        if value in (None, "", []):
            return default
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        # Scores are stored as floats so results from different exam scales can
        # be normalised, but the tutor typed "612", not "612.0". Whole numbers
        # are shown without the trailing ".0" that str() would add.
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def labelled(value: Any, table: Sequence[tuple[str, str]], default: str = "—") -> str:
        """Render a stored enum key as the label the tutor actually picked."""
        if value in (None, "", []):
            return default
        return dict(table).get(str(value), str(value))

    def doc_label(kind: Any, table: Mapping[str, str], default: str) -> str:
        if kind in (None, ""):
            return default
        return table.get(str(kind), str(kind))

    def _secondary_results_line(app: dict[str, Any]) -> str:
        """The recorded exam averages, or a dash when none were given."""
        recorded = [
            f"{label} {fmt(app.get(field))}"
            for field, label in profile_service.SECONDARY_RESULT_SHORT_LABELS
            if app.get(field) is not None
        ]
        return ", ".join(recorded) if recorded else "—"

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
        f"🎓 <b>Education level:</b> {labelled(app.get('education_level'), ETH_EDUCATION_LEVELS)}",
        f"🏫 <b>Institution:</b> {fmt(app.get('university'))}",
        f"🧪 <b>Department:</b> {fmt(app.get('department'))}",
        f"📅 <b>Year:</b> {fmt(app.get('university_year'))}",
        f"📈 <b>CGPA:</b> {fmt(app.get('cgpa'))}",
        f"📝 <b>Entrance exam:</b> {fmt(app.get('entrance_exam_score'))}/{fmt(app.get('entrance_exam_max_score'))} ({fmt(app.get('entrance_exam_year'))})",
        f"🧭 <b>Stream:</b> {labelled(app.get('academic_stream'), ETH_STREAMS)}",
        f"🧮 <b>Secondary results:</b> {_secondary_results_line(app)}",
        f"💼 <b>Experience:</b> {fmt(app.get('teaching_experience_years'))} yr(s)",
        f"🧑‍🏫 <b>Details:</b> {fmt(app.get('teaching_experience_description'))}",
        f"📄 <b>Documents:</b> {fmt([doc_label(d.get('document_type'), profile_service.REQUIRED_ONBOARDING_DOCUMENT_LABELS, 'supporting') for d in app.get('documents', [])])}",
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


def _validate_locations_text(value: str) -> list[str]:
    """Split free-text tutoring areas on commas and normalise each entry."""
    try:
        return profile_service.validate_locations(
            [part for part in (part.strip() for part in value.split(",")) if part]
        )
    except profile_service.ProfileValidationError as exc:
        raise ValueError(str(exc)) from None


#: Document types the attachment picker exposes on the Documents step.
_DOCUMENT_PICKER_TYPES: frozenset[str] = frozenset(
    (
        str(DocumentType.UNIVERSITY_TRANSCRIPT),
        str(DocumentType.UNIVERSITY_ENTRANCE_RESULT),
        str(DocumentType.SUPPORTING),
    )
)


def _uploaded_document_types(app: dict[str, Any]) -> set[str]:
    """Document types already attached in the current run, as strings."""
    types = set()
    for item in app.get("documents") or []:
        if isinstance(item, dict):
            types.add(str(item.get("document_type") or ""))
        else:
            types.add(str(item))
    return types


def _missing_document_labels(app: dict[str, Any]) -> list[str]:
    """Which mandatory documents are still missing in the current run."""
    uploaded = _uploaded_document_types(app)
    return [
        profile_service.REQUIRED_ONBOARDING_DOCUMENT_LABELS.get(kind, kind)
        for kind in profile_service.REQUIRED_ONBOARDING_DOCUMENTS
        if kind not in uploaded
    ]


def _validate_cgpa(value: str) -> float | None:
    return profile_service.validate_cgpa(value)


def _validate_int(value: str) -> int:
    out = profile_service._optional_int(value)
    if out is None:
        raise ValueError("Please enter a whole number.")
    return out


def _validate_grade_average(value: str, label: str) -> float | None:
    try:
        return profile_service.validate_grade_average(value, label)
    except profile_service.ProfileValidationError as exc:
        raise ValueError(str(exc)) from None


#: Field → the label the tutor knows the exam by, for the error messages.
_SECONDARY_RESULT_LABELS: Final[dict[str, str]] = dict(
    profile_service.SECONDARY_RESULT_FIELDS
)


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
    if field == "academic_stream":
        return _pick(ETH_STREAMS, lambda item: item[0])
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

    if data in (location_selector.kb.CB_HOME_YES, location_selector.kb.CB_HOME_NO):
        # The home-area question. It is answered wherever it appears, so the
        # buttons are handled before any step's own dispatch.
        return await _answer_home_area(update, context, state, data)

    if step is not None and step.kind == "location":
        # An online-only tutor can leave this step empty: for them location is
        # not a constraint, so Continue is a legitimate answer rather than a
        # validation failure.
        if data == CB_CONTINUE and _is_online(app):
            location_selector.LocationSession.clear(context)
            context.user_data[HOME_AREA_KEY] = ""
            return await _advance(update, context, state)

        # The selector owns this step's other buttons. ``None`` means the
        # callback was not one of its own, so the conversation's dispatch
        # carries on.
        handled = await _location_callback(update, context, state, data)
        if handled is not None:
            return handled

    if data == CB_BACK:
        return await _prompt(update, context, prev_state(state, sequence_for(context)))
    if data == CB_CONTINUE:
        # Documents are mandatory: even a stray Continue must not skip the two
        # academic files once they exist on the current run.
        if step is not None and step.field == "documents":
            missing = _missing_document_labels(app)
            if missing:
                await query.answer(
                    "Please attach: " + ", ".join(missing), show_alert=True
                )
                return state
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
    # eth:documents:pick:<TYPE> — choose what the next uploaded file is.
    if (
        len(parts) == 4
        and parts[0] == "eth"
        and parts[1] == "documents"
        and parts[2] == "pick"
    ):
        doc_type = parts[3].upper()
        if doc_type not in _DOCUMENT_PICKER_TYPES:
            await query.answer("Unknown document type.", show_alert=True)
            return state
        app["_pending_doc_type"] = doc_type
        label = profile_service.REQUIRED_ONBOARDING_DOCUMENT_LABELS.get(
            doc_type, "supporting document"
        )
        await _safe_reply(
            update,
            f"📎 Send the file for <b>{label}</b> now (PDF or image).",
            documents_keyboard(_uploaded_document_types(app), doc_type),
        )
        return state

    # eth:done / eth:<field>:done — every multi-select step ends on Done. The
    # Documents keyboard scopes it (``eth:documents:done``) like the others; the
    # bare form is still accepted so keyboards already sitting in a tutor's chat
    # keep working.
    if data == CB_DONE or (
        len(parts) == 3 and parts[0] == "eth" and parts[2] == "done"
    ):
        if step is not None and step.field == "documents":
            missing = _missing_document_labels(app)
            if missing:
                await query.answer(
                    "Please attach: " + ", ".join(missing), show_alert=True
                )
                return state
            app.pop("_pending_doc_type", None)
        elif len(parts) == 3:
            selected = app.get(parts[1]) or []
            if not selected:
                await query.answer("Please select at least one option.", show_alert=True)
                return state
        return await _advance(update, context, state)

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

    if step.kind == "location":
        # The selector owns this step's text: a query at the search screen,
        # an answer inside the manual form, or a whole list at once.
        outcome = await location_selector.handle_text(
            update, context, state=state, save=_save_location
        )
        if outcome is location_selector.done:
            return await _commit_location(update, context, state)
        return state if outcome is None else outcome

    try:
        if step.field == "full_name":
            app["full_name"] = _validate_name(text)
        elif step.field == "age":
            app["age"] = _validate_age(text)
        elif step.field == "phone":
            app["phone"] = _validate_phone(text)
        elif step.field == "current_address":
            app["current_address"] = _validate_text(text, "your current address")
        elif step.field == "locations":
            app["locations"] = _validate_locations_text(text)
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
        elif step.field in _SECONDARY_RESULT_LABELS:
            app[step.field] = _validate_grade_average(text, _SECONDARY_RESULT_LABELS[step.field])
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

    # The type the tutor picked; defaults to the first mandatory document so a
    # tutor who never tapped a button still uploads the required ones.
    uploaded = _uploaded_document_types(app)
    pending_type = app.get("_pending_doc_type")
    if pending_type not in _DOCUMENT_PICKER_TYPES:
        for kind in profile_service.REQUIRED_ONBOARDING_DOCUMENTS:
            if kind not in uploaded:
                pending_type = kind
                break
    pending_type = pending_type or str(DocumentType.SUPPORTING)

    app.setdefault("documents", []).append(
        {
            "document_type": pending_type,
            "telegram_file_id": file_id,
            "telegram_file_unique_id": file_unique,
            "telegram_message_id": update.message.message_id,
            "file_name": file_name,
            "mime_type": mime,
        }
    )
    label = profile_service.REQUIRED_ONBOARDING_DOCUMENT_LABELS.get(
        pending_type, "supporting document"
    )
    uploaded = _uploaded_document_types(app)
    missing = _missing_document_labels(app)
    note = "✅ All required documents received." if not missing else "Still needed: " + ", ".join(missing)
    await update.message.reply_text(
        f"📄 <b>{label}</b> received. \n{note}\n\nTap a type to attach the next file, then Done.",
        parse_mode=ParseMode.HTML,
    )
    await _safe_reply(
        update,
        f"📎 <b>Step 22/22 — 📄 Academic documents (required)</b>\n\nNext attachment: <b>{label}</b>.",
        documents_keyboard(uploaded, app.get("_pending_doc_type")),
    )
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
    # Transcript + entrance exam result are mandatory. A full application
    # always needs them; an enrichment only when it asked for documents.
    if not app.get("_enriching") or "documents" in (app.get("_planned") or ()):
        missing.extend(_missing_document_labels(app))
    return missing


def _archive_document_refs(app: dict[str, Any]) -> list[dict[str, Any]]:
    """Document refs to push to the archive channel, including the voice."""
    refs = [dict(item) for item in (app.get("documents") or [])]
    if app.get("english_voice_file_id"):
        refs.append(
            {
                "document_type": str(DocumentType.ENGLISH_VOICE),
                "telegram_file_id": app["english_voice_file_id"],
                "telegram_file_unique_id": None,
                "file_name": "english_voice.ogg",
            }
        )
    return refs


async def _submit_application(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Requirement 2.7: persist, archive in Telegram, notify the admin."""
    app = get_app(context)
    missing = _summary_missing(app)
    if missing:
        await _safe_reply(
            update,
            "⚠️ Please complete the following before submitting:\n• " + "\n• ".join(missing),
        )
        return int(EthiopianTutorStates.SUMMARY)

    # A tutor can only ever have one application. Mirroring the legacy flow, an
    # existing record is reported instead of over-written, which is what used to
    # surface as a generic "something went wrong" when the database rejected the
    # duplicate.
    with session_scope() as session:
        existing = tutor_service.get_tutor_by_telegram_id(
            session, update.effective_user.id
        )
        if existing is not None:
            await _safe_reply(
                update,
                "ℹ️ You already have an application on file under "
                f"<code>{existing.public_tutor_id}</code>.\n\n"
                "Our team is reviewing it. You can review the status with "
                "/my_application or fill the missing details with /complete_profile.",
            )
            return ConversationHandler.END

    payload = {
        "telegram_user_id": update.effective_user.id,
        "full_name": app.get("full_name"),
        "phone": app.get("phone"),
        "gender": app.get("gender"),
        "age": app.get("age"),
        "current_address": app.get("current_address"),
        "locations": app.get("locations") or [],
        # The canonical rows the selector attached, so the profile is linked to
        # real locations rather than to the text alone. A row the selector could
        # not resolve simply has no id, and is stored as plain text as before.
        "location_selections": app.get("location_selections") or [],
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
        "entrance_exam_max_score": app.get("entrance_exam_max_score"),
        "entrance_exam_score": app.get("entrance_exam_score"),
        "entrance_exam_year": app.get("entrance_exam_year"),
        "academic_stream": app.get("academic_stream"),
        **{field: app.get(field) for field in _SECONDARY_RESULT_LABELS},
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

    # Requirement 12.2: best-effort archive of the profile plus every document
    # (transcript, entrance exam result, supporting files) and the voice record
    # into the admin/database Telegram channel. If it fails the SQLite record
    # stays and the admin is alerted by the storage layer itself.
    try:
        await TelegramStorageService(context.bot).store_tutor_record(
            tdr_id, _archive_document_refs(app) or None
        )
    except Exception:
        logger.exception("Archival failed for %s; profile already saved", tdr_id)

    await _safe_reply(
        update,
        "✅ <b>Application received!</b>\n\n"
        f"Your reference is <code>{tdr_id}</code>.\n\n"
        "Our team will review your profile and verify your documents, then you "
        "will appear in job recommendations. You can check your status any time "
        "with /my_application.",
    )

    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_telegram_id(
            session, update.effective_user.id
        )
        if tutor is not None:
            await notify_admin_new_tutor(context.bot, tutor)

    return await _finish_run(update, context)


async def _finish_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Clear the run, then hand back to the job flow if one is waiting.

    The job reference is read *before* the run is cleared, because the marker
    lives in the same ``user_data`` the run occupies. A tutor who arrived from a
    job post's Apply button is returned straight to that job's evaluation
    instead of being dropped back on the main menu; everyone else just ends.
    """
    resume_job = take_resume_job(context)
    _clear_run(context)
    if not resume_job:
        return ConversationHandler.END

    # Imported here rather than at module scope: the job-apply handler starts
    # *this* conversation, so a top-level import would close a cycle.
    from app.bot.handlers.job_apply import resume_after_profile

    return await resume_after_profile(update, context, resume_job)


def _clear_run(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Drop everything about the run in progress, including the flow marker.

    The marker has to go too: the global fallback only stays quiet while a
    flow is active, so leaving it set would silence the fallback for good once
    a tutor finished applying.
    """
    for key in (APP_KEY, STEP_KEY, DOC_QUEUE_KEY, SEQUENCE_KEY, MODE_KEY):
        context.user_data.pop(key, None)
    end_flow(context)


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
        "full_name",
        "phone",
        "gender",
        "age",
        "current_address",
        "university",
        "department",
        "university_year",
        "cgpa",
        "education_level",
        "entrance_exam_max_score",
        "entrance_exam_score",
        "entrance_exam_year",
        "academic_stream",
        "english_proficiency",
        "teaching_experience_years",
        "teaching_experience_description",
    ):
        if key in app and app.get(key) not in (None, "", []):
            scalars[key] = app[key]
    # Optional ranking signals: absent means "not provided", so only send what
    # the tutor actually answered this run.
    for field in _SECONDARY_RESULT_LABELS:
        if app.get(field) is not None:
            scalars[field] = app[field]

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
                profile_service.set_locations(
                    session, tutor, app["locations"], app.get("location_selections")
                )
            if app.get("grades"):
                profile_service.set_grades(session, tutor, app["grades"])
            if app.get("subjects"):
                profile_service.set_subjects(session, tutor, app["subjects"])
            if scalars:
                profile_service.update_profile_fields(session, tutor, scalars)
            if app.get("english_voice_file_id"):
                profile_service.record_english_voice(
                    session,
                    tutor,
                    app["english_voice_file_id"],
                    app.get("english_voice_message_id"),
                )
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

    # Archive freshly uploaded documents / the voice record into the admin
    # channel. Best-effort: the stored profile is already updated.
    if app.get("documents") or app.get("english_voice_file_id"):
        try:
            await TelegramStorageService(context.bot).store_tutor_record(
                tdr_id, _archive_document_refs(app) or None
            )
        except Exception:
            logger.exception("Archival failed for %s; profile already saved", tdr_id)

    editing = app.get("_editing")
    if editing:
        # An edit is a deliberate change to one field, so it is reported as
        # such rather than as a completion round: the tutor did not "finish"
        # anything, they corrected something.
        text = (
            "✅ <b>Updated</b>\n\n"
            f"{_edit_label(editing)}: <code>{app.get('_previous', '—')}</code> → "
            f"<b>{app.get(editing, '—')}</b>\n"
            f"Your reference is <code>{tdr_id}</code>."
        )
    else:
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
    return await _finish_run(update, context)


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
    ("locations", "Tutoring areas"),
    ("grades", "Grades"),
    ("subjects", "Subjects"),
    ("english_proficiency", "English level"),
    ("education_level", "Education level"),
    ("university", "University"),
    ("department", "Department"),
    ("university_year", "University year"),
    ("entrance_exam_max_score", "Entrance exam max score"),
    ("entrance_exam_score", "Entrance exam score"),
    ("entrance_exam_year", "Entrance exam year"),
    ("academic_stream", "Academic stream"),
)


def _missing_fields(app: dict[str, Any]) -> list[str]:
    """Outstanding required fields for a full application."""
    return _summary_missing({**app, "_required": list(_REQUIRED_FIELDS)})


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_run(context)
    await _safe_reply(update, CANCEL_TEXT_LOCAL)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


async def start_application(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    GET = get_app(context)
    GET.clear()
    # The global "I did not recognise that" fallback silences itself while a
    # flow is active, so the tutor is never told to use the menu in the middle
    # of answering a question.
    start_flow(context, "tutor")
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
    EthiopianTutorStates.ENTRANCE_MAX_SCORE,
    EthiopianTutorStates.ENTRANCE_SCORE,
    EthiopianTutorStates.ENTRANCE_YEAR,
    EthiopianTutorStates.ACADEMIC_STREAM,
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
    int(EthiopianTutorStates.ENTRANCE_MAX_SCORE): "entrance_exam_max_score",
    int(EthiopianTutorStates.ENTRANCE_SCORE): "entrance_exam_score",
    int(EthiopianTutorStates.ENTRANCE_YEAR): "entrance_exam_year",
    int(EthiopianTutorStates.ACADEMIC_STREAM): "academic_stream",
    int(EthiopianTutorStates.GRADE8_AVERAGE): "grade8_exam_average",
    int(EthiopianTutorStates.GRADE9_AVERAGE): "grade9_average",
    int(EthiopianTutorStates.GRADE10_AVERAGE): "grade10_average",
    int(EthiopianTutorStates.GRADE11_AVERAGE): "grade11_average",
    int(EthiopianTutorStates.GRADE12_AVERAGE): "grade12_average",
    int(EthiopianTutorStates.EXPERIENCE_YEARS): "experience",
    int(EthiopianTutorStates.EXPERIENCE_DESCRIPTION): "teaching_experience_description",
    int(EthiopianTutorStates.DOCUMENTS): "documents",
}


def planned_fields(steps: list[int]) -> set[str]:
    """The profile fields a planned step list will collect."""
    return {
        _STATE_FIELDS[state] for state in steps if state in _STATE_FIELDS
    }


#: The profile fields covered by :data:`_ENTRANCE_CHAIN`, derived so the two
#: can never drift apart.
_ENTRANCE_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    _STATE_FIELDS[state] for state in _ENTRANCE_CHAIN
)


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

    # Secondary results are optional ranking signals, so only the ones actually
    # absent are asked for — never as a block the tutor has to complete.
    steps.extend(
        state
        for field, state in SECONDARY_RESULT_STATES.items()
        if getattr(tutor, field, None) is None
    )

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
        or tutor.education_level is None
        or tutor.academic_stream is None
    )


# ---------------------------------------------------------------------------
# editing a submitted profile (/edit_profile)
# ---------------------------------------------------------------------------

#: Fields a tutor may change after submitting. Status is excluded because it
#: is the admin's decision, and documents because replacing one is a separate
#: conversation (``/documents``) rather than a field edit.
EDITABLE_FIELDS: Final[tuple[str, ...]] = (
    "full_name",
    "phone",
    "gender",
    "age",
    "current_address",
    "locations",
    "grades",
    "subjects",
    "english_proficiency",
    # One entry for the whole entrance chain: editing any part of it re-asks
    # all of it, so offering education level and stream separately would give
    # the tutor three buttons that do exactly the same thing.
    "entrance_exam_score",
    "university",
    "department",
    "university_year",
    "cgpa",
    "experience",
    "teaching_experience_description",
)

#: Menu labels. Only the chain needs one; everything else falls back to the
#: same label the missing-details flow uses, so the two never disagree.
_EDIT_LABELS: Final[dict[str, str]] = {"entrance_exam_score": "Entrance exam"}

#: Callback names are field names, so the buttons stay readable and a future
#: field is one entry away rather than a new handler.
EDIT_CALLBACK = "menu:edit_field:"


def _edit_label(field: str) -> str:
    return _EDIT_LABELS.get(field) or _step_label(field)


def _field_states(field: str) -> list[int]:
    """The conversation steps that answer ``field``.

    The entrance exam is the exception: its score is validated against the
    recorded maximum and sits in a chain that only means something together, so
    editing any part of it re-asks the whole chain rather than half of it.
    """
    if field in _ENTRANCE_FIELD_NAMES:
        return [int(state) for state in _ENTRANCE_CHAIN]
    state = next(
        (int(value) for value, name in _STATE_FIELDS.items() if name == field), None
    )
    return [state] if state is not None else []


def _current_value(tutor, field: str) -> str:
    """The tutor's stored answer for ``field``, for display on the menu."""
    if field == "locations":
        return ", ".join(tutor.location_list) or "—"
    if field == "grades":
        return ", ".join(tutor.grade_list) or "—"
    if field == "subjects":
        return ", ".join(tutor.subject_list) or "—"
    if field == "entrance_exam_score":
        if tutor.entrance_exam_score is None:
            return "—"
        return (
            f"{tutor.entrance_exam_score:g}/{tutor.entrance_exam_max_score:g}"
            if tutor.entrance_exam_max_score is not None
            else f"{tutor.entrance_exam_score:g}"
        )
    value = getattr(tutor, _FIELD_ALIASES.get(field, field), None)
    if value in (None, "", []):
        return "—"
    return str(value)


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
            CallbackQueryHandler(_edit_field_callback, pattern=rf"^{EDIT_CALLBACK}(.+)$"),
            CommandHandler("apply", start_application),
            CommandHandler("apply_ethiopia", start_application),
            CommandHandler("complete_profile", complete_profile_entry),
            CommandHandler("edit_profile", edit_profile_entry),
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

    A thin wrapper over :func:`start_profile_completion`, which the job-apply
    flow also calls so a tutor arriving from a job post gets the identical
    "only ask what is missing" behaviour.
    """
    return await start_profile_completion(update, context)


async def start_profile_completion(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_reference: str = ""
) -> int:
    """Enter the enrichment flow for whatever the stored profile still lacks.

    Builds the step list from what the profile is *actually* missing, so nobody
    is asked to retype an answer they already gave. When ``job_reference`` is
    given the run remembers that job, and :func:`_finish_run` returns the tutor
    to that job's evaluation once they submit.

    The tutor is loaded inside the session and only plain values cross the
    boundary; their ORM object must not outlive it.
    """
    if job_reference:
        set_resume_job(context, job_reference)

    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_telegram_id(session, update.effective_user.id)
        if tutor is None:
            await _safe_reply(
                update,
                "We don't have your profile yet. Tap Become a Tutor to apply first.",
            )
            return ConversationHandler.END
        # Reuse what an imported tutor already recorded, so the plan below never
        # asks for it and the completeness report tells the same story.
        profile_service.backfill_education_from_legacy(session, tutor)
        # Link the areas this tutor already typed to the locations we now hold.
        # Purely additive: a row that matches nothing stays exactly as it was,
        # and nothing about the tutor's own spelling changes.
        linked = location_registry_service.link_tutor_rows(session, tutor)
        steps = enrichment_steps(tutor)
        before = tutor_profile_completion(tutor)

    if linked:
        # Reported rather than done silently: the tutor's own spelling is
        # unchanged, and seeing that their areas were recognised is how they
        # know nothing was invented on their behalf.
        await _safe_reply(
            update,
            f"🔁 Matched {linked} of the areas you had already saved to a "
            "location in our records. Nothing about them was changed.",
        )

    if steps == [int(EthiopianTutorStates.SUMMARY)]:
        await _safe_reply(
            update,
            "✅ <b>Your profile is already complete</b> — nothing left to fill in.",
        )
        # Nothing to collect, so hand straight back to the waiting job rather
        # than leaving the tutor on a dead end.
        resume_job = take_resume_job(context)
        if resume_job:
            from app.bot.handlers.job_apply import resume_after_profile

            return await resume_after_profile(update, context, resume_job)
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
    application["_planned"] = planned_fields(steps)
    context.user_data[SEQUENCE_KEY] = steps
    context.user_data[MODE_KEY] = MODE_ENRICH
    start_flow(context, "tutor")
    set_step(context, steps[0])

    # The bullets name the steps this run will actually ask for. Deriving them from
    # the plan rather than from the completeness report keeps the two in step:
    # the score does not cover every field the flow collects, so it can be 100%
    # while personal details are still outstanding.
    asked = [_step_label(field) for field in planned_fields(steps)]
    await _safe_reply(
        update,
        "✏️ <b>Profile completion</b>\n\n"
        f"You are at <b>{before}%</b>. Let's fill in what's missing:\n• "
        + "\n• ".join(asked)
        + "\n\nUse ⬅️ Back, ✏️ Edit, ❌ Cancel or ✅ Continue.",
    )
    return await _prompt(update, context, steps[0])


async def _edit_field_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Unpack ``menu:edit_field:<name>`` and start that single-field edit."""
    query = update.callback_query
    if query is not None:
        # Without this the button keeps spinning while the question is sent.
        try:
            await query.answer()
        except TelegramError:
            logger.debug("Could not answer the edit callback", exc_info=True)
    field = (query.data if query is not None else "").split(":")[-1]
    return await start_field_edit(update, context, field)


async def edit_profile_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Let a submitted tutor change an answer they got wrong or have moved on from.

    ``/complete_profile`` only ever asks for what is *missing*, so a tutor whose
    phone number changed, or who picked the wrong grade, had no way to correct
    it — only an admin could. This offers the fields they own, each showing
    what is currently stored, and runs the ordinary conversation step for
    whichever one they pick, so the answer goes through exactly the same
    validation as the original application.
    """
    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_telegram_id(
            session, update.effective_user.id
        )
        if tutor is None:
            await _safe_reply(
                update,
                "You don't have a profile yet. Tap Become a Tutor to apply first.",
            )
            return ConversationHandler.END
        rows = [
            (field, _current_value(tutor, field)) for field in EDITABLE_FIELDS
        ]
        tdr_id = tutor.public_tutor_id

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{_edit_label(field)}: {value[:28]}",
                    callback_data=f"{EDIT_CALLBACK}{field}",
                )
            ]
            for field, value in rows
        ]
        + [[InlineKeyboardButton("⬅️ Close", callback_data="menu:home")]]
    )
    await _safe_reply(
        update,
        "✏️ <b>Edit your profile</b>\n\n"
        f"Reference: <code>{tdr_id}</code>\n\n"
        "Tap what you want to change. Your verification status and documents "
        "are not changed here.",
        keyboard,
    )
    return int(EthiopianTutorStates.SUMMARY)


async def start_field_edit(
    update: Update, context: ContextTypes.DEFAULT_TYPE, field: str
) -> int:
    """Run the conversation for a single chosen field and save the answer."""
    if field not in EDITABLE_FIELDS:
        await _safe_reply(update, "That field cannot be edited here.")
        return int(EthiopianTutorStates.SUMMARY)

    steps = _field_states(field)
    if not steps:
        await _safe_reply(update, "That field cannot be edited here.")
        return int(EthiopianTutorStates.SUMMARY)

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
        current = _current_value(tutor, field)

    application = get_app(context)
    application.clear()
    application["_enriching"] = True
    application["_editing"] = field
    application["_previous"] = current
    # Only the field being edited can block submission. Nothing else is
    # re-requested, so the tutor is never asked to retype the rest.
    application["_required"] = [
        entry for entry in _REQUIRED_FIELDS if entry[0] == field
    ]
    application["_planned"] = {field}
    context.user_data[SEQUENCE_KEY] = [*steps, int(EthiopianTutorStates.SUMMARY)]
    context.user_data[MODE_KEY] = MODE_ENRICH
    start_flow(context, "tutor")
    set_step(context, context.user_data[SEQUENCE_KEY][0])

    await _safe_reply(
        update,
        f"✏️ <b>Changing {_edit_label(field)}</b>\n\n"
        f"Currently: <b>{current}</b>\n\n"
        "Send the new value. Use ❌ Cancel to keep what you have.",
    )
    return await _prompt(update, context, context.user_data[SEQUENCE_KEY][0])


def tutor_profile_completion(tutor) -> int:
    return profile_service.profile_completion_percentage(tutor)


__all__ = [
    "build_conversation",
    "complete_profile_entry",
    "set_resume_job",
    "start_application",
    "start_profile_completion",
    "take_resume_job",
]

# ---------------------------------------------------------------------------
# The location selector
# ---------------------------------------------------------------------------


async def _save_location(
    name: str, city: str, sub_city: str, landmark: str
) -> dict[str, Any]:
    """Write a manually typed location, returning what was created.

    Mirrors the selector's saver contract: a plain dict of the row's fields,
    with provenance. Opening the session here rather than in the selector keeps
    the commit and the rollback in the flow that owns them, and means the ORM
    object never outlives the block that loaded it.
    """
    from app.database import session_scope

    with session_scope() as session:
        resolved = location_registry_service.resolve_or_create_manual(
            session,
            name=name,
            city=city,
            sub_city=sub_city,
            landmark=landmark,
            created_by_user_id=None,
        )
        location = resolved.location
        return {
            "name": location.name,
            "location_id": location.id,
            "source_text": location.name,
            "city": location.city,
            "sub_city": location.sub_city or "",
            "landmark": location.landmark or "",
            "latitude": location.latitude,
            "longitude": location.longitude,
            "normalized_name": location.normalized_name,
            "provider": location.provider,
            "verification_status": location.verification_status,
            "is_suggestion": resolved.is_suggestion,
        }


async def _location_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: int, data: str
) -> int | None:
    """Run a location-selector button and merge its answer into the payload.

    Returns the conversation state to stay on, the "finished" state to advance,
    or ``None`` when this callback belonged to another step.
    """
    app = get_app(context)
    outcome = await location_selector.handle_callback(
        update, context, state=state, save=_save_location
    )

    if outcome is location_selector.done:
        return await _commit_location(update, context, state)

    if outcome is None:
        # Not one of the selector's callbacks: the conversation's own dispatch
        # carries on.
        return None

    # Still inside the selector. Stay on the same conversation step; it is a
    # sub-dialog, not a new state band.
    return state


async def _commit_location(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: int
) -> int:
    """Move the selector's picks into this step's payload and advance.

    Both routes into here — pressing Done and typing a whole list at once — end
    the same way, so neither can end differently by accident.
    """
    app = get_app(context)
    picks = location_selector.LocationSession.get(context)
    app.update(picks.payload())
    location_selector.LocationSession.clear(context)

    if _location_step_is_empty(app) and not _is_online(app):
        # In-person tutors need somewhere to teach. Refusing Done is honest;
        # making an answer up for them is not.
        await _safe_reply(
            update,
            location_prompts.PROMPT_NEED_ONE,
            _keyboard_for(STEPS_BY_STATE[state], app),
        )
        return state

    # If they gave an address but nowhere to teach, the summary screen will
    # offer that address as an option — once, explicitly, and only ever added
    # on a Yes. Nothing happens here, because asking mid-flow would interrupt
    # someone who is still working through the steps.
    if _location_step_is_empty(app):
        context.user_data[HOME_AREA_KEY] = app.get("current_address") or ""

    return await _advance(update, context, state)


def _is_online(app: dict[str, Any]) -> bool:
    """Whether the tutor is teaching online only.

    Read from the steps that asked about it, and from the answers they already
    have. Online-only tutors are never asked for a location at all.
    """
    if str(app.get("teaching_mode") or "").strip().upper() == "ONLINE":
        return True
    for value in list(app.get("locations") or []):
        if str(value).strip().lower() in ("online", "remote", "anywhere"):
            return True
    return False


def _location_step_is_empty(app: dict[str, Any]) -> bool:
    return not bool(app.get("locations"))


async def _answer_home_area(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: int, data: str
) -> int:
    """Apply the Yes/No answer, then carry on from the address step."""
    answered = await location_selector.answer_about_home_area(
        update,
        context,
        state=state,
        answer_text="yes" if data == location_selector.kb.CB_HOME_YES else "no",
    )
    if answered is None:  # pragma: no cover - defensive
        return state
    return await _advance(update, context, state)


async def _answer_home_area(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: int, data: str
) -> int:
    """Apply the Yes/No answer, then carry on from the current step."""
    answered = await location_selector.answer_about_home_area(
        update,
        context,
        state=state,
        answer_text="yes" if data == location_selector.kb.CB_HOME_YES else "no",
    )
    return await _show_summary(update, context)
