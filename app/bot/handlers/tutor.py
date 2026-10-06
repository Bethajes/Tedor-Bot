"""Tutor onboarding conversation (Requirements 2.x, 3.x, 4.x, 5.1).

The whole 26 step flow is declared as data (``STEP_SEQUENCE``) and the handlers
are generated from it, which keeps navigation (Back / Edit / Cancel / Continue)
consistent on every step.
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
    MessageHandler,
    filters,
)

from app.bot.keyboards.tutor import (
    CB_BACK,
    CB_CANCEL,
    CB_CONTINUE,
    CB_EDIT,
    CB_SKIP,
    CB_SUBMIT,
    choice_keyboard,
    multi_select_keyboard,
    nav_keyboard,
    skip_keyboard,
    summary_edit_keyboard,
    summary_keyboard,
)
from app.bot.options import (
    COUNTRIES,
    LANGUAGES,
    LEVELS,
    SUBJECTS,
    TEACHING_MODES,
    TIMEZONES,
    WEEKDAYS,
)
from app.bot.handlers.common import (
    Step,
    ValidationError,
    answer,
    end_flow,
    current_step,
    get_application,
    html_escape,
    reset_application,
    run_validator,
    safe_edit,
    safe_reply,
    set_step,
    split_list,
    start_flow,
    summary_lines,
)
from app.bot.states import CANCEL_TEXT, TutorStates
from app.database import session_scope
from app.enums import Currency, DocumentType
from app.services import notification_service, tutor_service
from app.services.currency import convert, currency_for_country, is_ethiopia, other_currency
from app.services.storage_service import TelegramStorageService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Step declaration
# ---------------------------------------------------------------------------

STEP_SEQUENCE: tuple[Step, ...] = (
    Step(
        TutorStates.FULL_NAME,
        "full_name",
        "👤 <b>Step 1/26 — Full name</b>\n\nSend your full legal name as it appears on your ID.",
        kind="name",
    ),
    Step(
        TutorStates.DISPLAY_NAME,
        "display_name",
        "🏷 <b>Step 2/26 — Display name</b>\n\nWhich name should students see? (e.g. Abebe M.)",
        kind="name",
    ),
    Step(
        TutorStates.PHONE,
        "phone",
        "📞 <b>Step 3/26 — Phone number</b>\n\nInclude your country code (e.g. +251911234567).",
        kind="phone",
    ),
    Step(
        TutorStates.EMAIL,
        "email",
        "✉️ <b>Step 4/26 — Email address</b>\n\nWe use this to contact you about your application.",
        kind="email",
    ),
    Step(
        TutorStates.COUNTRY,
        "country",
        "🌍 <b>Step 5/26 — Country</b>\n\nWhere are you currently teaching?",
        kind="choice",
        options=COUNTRIES,
    ),
    Step(
        TutorStates.CITY,
        "city",
        "🏙 <b>Step 6/26 — City</b>\n\nWhich city do you teach in?",
        kind="text",
    ),
    Step(
        TutorStates.PHOTO,
        "photo",
        "🖼 <b>Step 7/26 — Profile photo</b>\n\nSend a clear photo of yourself. You can skip it now and add it later.",
        kind="photo",
        optional=True,
    ),
    Step(
        TutorStates.SUBJECTS,
        "subjects",
        "📚 <b>Step 8/26 — Subjects</b>\n\nPick every subject you can teach (tap to toggle), or type them separated by commas.",
        kind="multiselect",
        options=SUBJECTS,
    ),
    Step(
        TutorStates.LEVELS,
        "levels",
        "🎓 <b>Step 9/26 — Education levels</b>\n\nWhich learner levels can you teach?",
        kind="multiselect",
        options=LEVELS,
    ),
    Step(
        TutorStates.TEACHING_MODE,
        "teaching_mode",
        "🧑‍🏫 <b>Step 10/26 — Teaching mode</b>\n\nHow do you normally teach?",
        kind="choice",
        options=TEACHING_MODES,
    ),
    Step(
        TutorStates.LANGUAGES,
        "languages",
        "🗣 <b>Step 11/26 — Languages</b>\n\nWhich languages can you teach in?",
        kind="multiselect",
        options=LANGUAGES,
    ),
    Step(
        TutorStates.EXPERIENCE,
        "experience_years",
        "💼 <b>Step 12/26 — Years of experience</b>\n\nHow many years have you been teaching? (number of years)",
        kind="int",
    ),
    Step(
        TutorStates.BIO,
        "bio",
        "📝 <b>Step 13/26 — Short biography</b>\n\nTell students about yourself (max 500 characters).",
        kind="text",
    ),
    Step(
        TutorStates.EDU_INSTITUTION,
        "edu_institution",
        "🎓 <b>Step 14/26 — Institution</b>\n\nName of your university / college / school.",
        kind="text",
    ),
    Step(
        TutorStates.EDU_DEGREE,
        "edu_degree",
        "📜 <b>Step 15/26 — Degree</b>\n\ne.g. BSc, MSc, MA, Grade 12 Certificate",
        kind="text",
    ),
    Step(
        TutorStates.EDU_FIELD,
        "edu_field",
        "🧪 <b>Step 16/26 — Field of study</b>\n\ne.g. Mathematics, Physics, English",
        kind="text",
    ),
    Step(
        TutorStates.EDU_YEAR,
        "edu_year",
        "📅 <b>Step 17/26 — Graduation year</b>\n\nThe year you completed the qualification (e.g. 2019).",
        kind="year",
    ),
    Step(
        TutorStates.ETB_RATE,
        "etb_rate",
        "💰 <b>Step 18/26 — Hourly rate in ETB</b>\n\nWhat is your hourly price in Ethiopian Birr?",
        kind="float",
    ),
    Step(
        TutorStates.USD_RATE,
        "usd_rate",
        "💵 <b>Step 19/26 — Hourly rate in USD</b>\n\nWhat is your hourly price in US dollars?",
        kind="float",
    ),
    Step(
        TutorStates.AVAIL_DAYS,
        "availability_days",
        "🗓 <b>Step 20/26 — Available days</b>\n\nPick the days you can teach.",
        kind="multiselect",
        options=WEEKDAYS,
    ),
    Step(
        TutorStates.AVAIL_TIMES,
        "availability_times",
        "⏰ <b>Step 21/26 — Available time ranges</b>\n\nFormat: <code>09:00-12:00, 16:00-18:00</code>",
        kind="text",
    ),
    Step(
        TutorStates.TIMEZONE,
        "timezone",
        "🕒 <b>Step 22/26 — Timezone</b>\n\nWhich timezone are your available hours in?",
        kind="choice",
        options=TIMEZONES,
    ),
    Step(
        TutorStates.DOC_CV,
        "doc_cv",
        "📄 <b>Step 23/26 — CV / Resume</b>\n\nUpload your CV as a PDF or document.",
        kind="document",
    ),
    Step(
        TutorStates.DOC_DEGREE,
        "doc_degree",
        "📜 <b>Step 24/26 — Degree / Certificate</b>\n\nUpload a photo or scan of your degree/certificate.",
        kind="document",
    ),
    Step(
        TutorStates.DOC_EXTRA,
        "doc_extra",
        "📎 <b>Step 25/26 — Supporting documents</b>\n\nOptional: send extra evidence (recommendation letters, training certificates…). You can add several or skip.",
        kind="document",
        optional=True,
    ),
    Step(TutorStates.SUMMARY, "summary", "", kind="summary"),
)

STEPS_BY_STATE: dict[int, Step] = {int(step.state): step for step in STEP_SEQUENCE}
STATE_ORDER: list[int] = [int(step.state) for step in STEP_SEQUENCE]

FIELD_BY_KEY: dict[str, int] = {
    "full_name": int(TutorStates.FULL_NAME),
    "display_name": int(TutorStates.DISPLAY_NAME),
    "phone": int(TutorStates.PHONE),
    "email": int(TutorStates.EMAIL),
    "country": int(TutorStates.COUNTRY),
    "city": int(TutorStates.CITY),
    "photo": int(TutorStates.PHOTO),
    "subjects": int(TutorStates.SUBJECTS),
    "levels": int(TutorStates.LEVELS),
    "teaching_mode": int(TutorStates.TEACHING_MODE),
    "languages": int(TutorStates.LANGUAGES),
    "experience": int(TutorStates.EXPERIENCE),
    "bio": int(TutorStates.BIO),
    "education": int(TutorStates.EDU_INSTITUTION),
    "etb_rate": int(TutorStates.ETB_RATE),
    "usd_rate": int(TutorStates.USD_RATE),
    "availability": int(TutorStates.AVAIL_DAYS),
    "timezone": int(TutorStates.TIMEZONE),
    "doc_cv": int(TutorStates.DOC_CV),
    "doc_degree": int(TutorStates.DOC_DEGREE),
    "doc_extra": int(TutorStates.DOC_EXTRA),
}

SUMMARY_LABELS: dict[str, str] = {
    "full_name": "Full name",
    "display_name": "Display name",
    "phone": "Phone",
    "email": "Email",
    "country": "Country",
    "city": "City",
    "photo": "Profile photo",
    "subjects": "Subjects",
    "levels": "Levels",
    "teaching_mode": "Teaching mode",
    "languages": "Languages",
    "experience": "Experience (years)",
    "bio": "Biography",
    "edu_institution": "Institution",
    "edu_degree": "Degree",
    "edu_field": "Field of study",
    "edu_year": "Graduation year",
    "etb_rate": "ETB rate",
    "usd_rate": "USD rate",
    "availability_days": "Available days",
    "availability_times": "Available times",
    "timezone": "Timezone",
    "doc_cv": "CV uploaded",
    "doc_degree": "Degree uploaded",
    "documents": "Documents",
}


# ---------------------------------------------------------------------------
# navigation helpers
# ---------------------------------------------------------------------------


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


def _parse_callback(data: str) -> tuple[str, str, str]:
    """Split ``tutor:<field>:<action>[:<value>]`` callback data."""
    parts = data.split(":")
    if len(parts) >= 3 and parts[-2] in {"pick", "toggle"}:
        return parts[0], parts[-2], parts[-1]
    if len(parts) >= 2 and parts[-1] == "done":
        return parts[0], parts[-1], ""
    return parts[0], "", ""


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------


async def _prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int) -> int:
    """Show the prompt for ``state`` and mark it as freshly displayed."""
    step = STEPS_BY_STATE[int(state)]
    application = get_application(context)
    set_step(context, state)

    if int(state) == int(TutorStates.SUMMARY):
        finalise_rates(application)
        await _show_summary(update, context)
        return state

    text = step.prompt
    if int(state) == int(TutorStates.ETB_RATE) and not is_ethiopia(application.get("country")):
        # Requirement 2.5: ETB is secondary for non-Ethiopian tutors.
        return await _prompt(update, context, next_state(state))
    if int(state) == int(TutorStates.USD_RATE) and is_ethiopia(application.get("country")):
        primary = application.get("etb_rate")
        if primary:
            application["usd_rate"] = convert(
                float(primary), Currency.ETB, Currency.USD
            )
            text += f"\n\nBased on your ETB rate this is about <code>{application['usd_rate']:g} USD</code>."
        return await _prompt(update, context, next_state(state))

    keyboard = _keyboard_for(step, application)
    await safe_reply(update, text, keyboard, parse_mode="HTML")
    return state


def _keyboard_for(step: Step, application: dict[str, Any]):
    prefix = f"tutor:{step.field}"
    if step.kind == "choice":
        options = [label for _code, label in step.options] if step.field == "teaching_mode" else list(step.options)
        return choice_keyboard(prefix, options)
    if step.kind == "multiselect":
        return multi_select_keyboard(prefix, list(step.options), application.get(step.field) or [])
    if step.optional:
        return skip_keyboard()
    return nav_keyboard()


async def _show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    application = get_application(context)
    view = summary_view(application)
    lines = summary_lines(view, SUMMARY_LABELS)
    lines.append("")
    lines.append("Please review everything before submitting.")
    await safe_reply(update, "\n".join(lines), summary_keyboard(), parse_mode="HTML")


def summary_view(application: dict[str, Any]) -> dict[str, Any]:
    """Flatten collected data for display."""
    documents = application.get("documents") or []

    def has(doc_type: str) -> bool:
        return any(item.get("document_type") == doc_type for item in documents)

    view = dict(application)
    view["photo"] = "✅ received" if has(DocumentType.PROFILE_PHOTO) else "—"
    view["doc_cv"] = "✅ received" if has(DocumentType.CV) else "—"
    view["doc_degree"] = "✅ received" if has(DocumentType.DEGREE) else "—"
    view["documents"] = ", ".join(
        str(item.get("document_type")) for item in documents
    ) or "—"
    for key in ("subjects", "levels", "languages", "availability_days"):
        value = view.get(key)
        if isinstance(value, list):
            view[key] = ", ".join(value) or "—"
    if not view.get("etb_rate"):
        view["etb_rate"] = "—"
    if not view.get("usd_rate"):
        view["usd_rate"] = "—"
    return view


def finalise_rates(application: dict[str, Any]) -> None:
    """Requirement 2.4/2.5: guarantee the primary currency is populated."""
    country = application.get("country")
    primary = currency_for_country(country)
    etb = application.get("etb_rate")
    usd = application.get("usd_rate")
    if primary == Currency.ETB and not etb and usd:
        application["etb_rate"] = convert(float(usd), Currency.USD, Currency.ETB)
    if primary == Currency.USD and not usd and etb:
        application["usd_rate"] = convert(float(etb), Currency.ETB, Currency.USD)


# ---------------------------------------------------------------------------
# generic handlers
# ---------------------------------------------------------------------------


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Navigation + selection handler shared by every step."""
    query = update.callback_query
    data = query.data or ""
    await answer(update)
    application = get_application(context)
    state = current_step(context)
    step = STEPS_BY_STATE.get(int(state)) if state is not None else None

    if data == CB_CANCEL:
        return await _cancel(update, context)

    if data in (CB_BACK, CB_CONTINUE, CB_SKIP, CB_EDIT, CB_SUBMIT):
        return await _navigation(update, context, data)

    prefix, action, value = _parse_callback(data)
    if action == "done" and step is not None:
        selected = application.get(step.field) or []
        if not selected:
            await safe_reply(
                update, "Please select at least one option, or type your answer."
            )
            return int(step.state)
        return await _prompt(update, context, next_state(int(step.state)))
    if action in {"pick", "toggle"} and step is not None:
        try:
            index = int(value)
        except ValueError:
            return int(step.state)
        if action == "pick":
            _apply_choice(update, context, step, index)
            return await _prompt(update, context, next_state(int(step.state)))
        return await _apply_toggle(update, context, step, index)
    if data == "tutor:summary":
        await _show_summary(update, context)
        return int(TutorStates.SUMMARY)
    if data.startswith("tutor:editfield:"):
        target = FIELD_BY_KEY.get(data.split(":")[-1])
        if target is None:
            return int(state or int(TutorStates.FULL_NAME))
        _clear_field(application, data.split(":")[-1])
        return await _prompt(update, context, target)

    return int(state or int(TutorStates.FULL_NAME))


def _apply_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE, step: Step, index: int
) -> int:
    application = get_application(context)
    options = list(step.options)
    if index < 0 or index >= len(options):
        return int(step.state)
    raw = options[index]
    value = raw[0] if isinstance(raw, tuple) else raw
    application[step.field] = str(value).strip()
    if step.field == "country":
        primary = currency_for_country(value)
        application["primary_currency"] = str(primary)
        application.pop("etb_rate", None)
        application.pop("usd_rate", None)
        application["secondary_currency"] = str(other_currency(value))
    return int(step.state)


async def _apply_toggle(
    update: Update, context: ContextTypes.DEFAULT_TYPE, step: Step, index: int
) -> int:
    application = get_application(context)
    options = list(step.options)
    if index < 0 or index >= len(options):
        return int(step.state)
    value = options[index]
    selected: list[str] = list(application.get(step.field) or [])
    if value in selected:
        selected.remove(value)
    else:
        selected.append(value)
    application[step.field] = selected
    keyboard = multi_select_keyboard(f"tutor:{step.field}", options, selected)
    await safe_edit(
        update,
        f"{step.prompt}\n\n<b>Selected:</b> {html_escape(', '.join(selected) or 'none yet')}",
        keyboard,
        parse_mode="HTML",
    )
    return int(step.state)


def _clear_field(application: dict[str, Any], key: str) -> None:
    if key == "education":
        for field_name in ("edu_institution", "edu_degree", "edu_field", "edu_year"):
            application.pop(field_name, None)
    elif key == "availability":
        application.pop("availability_days", None)
        application.pop("availability_times", None)
    elif key == "doc_cv":
        _drop_documents(application, DocumentType.CV)
    elif key == "doc_degree":
        _drop_documents(application, DocumentType.DEGREE)
    elif key == "doc_extra":
        _drop_documents(application, DocumentType.SUPPORTING)
    elif key == "photo":
        _drop_documents(application, DocumentType.PROFILE_PHOTO)
    else:
        application.pop(key, None)


def _drop_documents(application: dict[str, Any], document_type: str) -> None:
    documents = application.get("documents") or []
    application["documents"] = [
        item for item in documents if item.get("document_type") != document_type
    ]


async def _navigation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: str
) -> int:
    state = current_step(context)
    if data == CB_EDIT:
        await safe_reply(
            update,
            "✏️ <b>Which field do you want to edit?</b>",
            summary_edit_keyboard(),
            parse_mode="HTML",
        )
        return int(state or int(TutorStates.FULL_NAME))
    if data == CB_BACK:
        return await _prompt(update, context, prev_state(state))
    if data in (CB_CONTINUE, CB_SKIP):
        return await _prompt(update, context, next_state(state))
    if data == CB_SUBMIT:
        return await submit_application(update, context)
    return int(state or int(TutorStates.FULL_NAME))


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_application(context)
    set_step(context, None)
    end_flow(context)
    await safe_reply(update, CANCEL_TEXT)
    logger.info("Tutor application cancelled")
    return ConversationHandler.END


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store free-text input for the current step."""
    state = current_step(context)
    step = STEPS_BY_STATE.get(int(state)) if state is not None else None
    if step is None or step.kind == "summary":
        return int(state or int(TutorStates.FULL_NAME))

    application = get_application(context)
    text = update.message.text or ""
    try:
        if step.kind == "choice":
            match = _match_option(step, text)
            if match is None:
                await safe_reply(update, f"'{text}' is not one of the options. Please pick from the buttons or type an exact option.")
                return int(step.state)
            application[step.field] = match
            if step.field == "country":
                application["primary_currency"] = str(currency_for_country(match))
        elif step.kind == "multiselect":
            values = split_list(text)
            if not values:
                await safe_reply(update, "Please select at least one option.")
                return int(step.state)
            application[step.field] = values
        else:
            application[step.field] = run_validator(step.kind, text)
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
    return None


# ---------------------------------------------------------------------------
# document steps
# ---------------------------------------------------------------------------


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    state = current_step(context)
    if state is None:
        return int(TutorStates.FULL_NAME)
    application = get_application(context)
    if int(state) != int(TutorStates.PHOTO):
        await safe_reply(update, "⚠️ Photos can only be added at the profile photo step.")
        return int(state)
    sizes = update.message.photo
    photo = sizes[-1]
    _store_document(
        application,
        DocumentType.PROFILE_PHOTO,
        photo.file_id,
        photo.file_unique_id,
    )
    await safe_reply(update, "🖼 Profile photo received.")
    return await _prompt(update, context, next_state(int(state)))


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    state = current_step(context)
    if state is None:
        return int(TutorStates.FULL_NAME)
    if int(state) not in {
        int(TutorStates.DOC_CV),
        int(TutorStates.DOC_DEGREE),
        int(TutorStates.DOC_EXTRA),
    }:
        await safe_reply(update, "⚠️ Documents can only be uploaded at the document steps.")
        return int(state)
    application = get_application(context)
    message = update.message
    document = message.document
    voice = message.voice
    video = message.video

    file_id = None
    unique_id = None
    file_name = None
    if document is not None:
        file_id, unique_id, file_name = document.file_id, document.file_unique_id, document.file_name
    elif voice is not None:
        file_id, unique_id = voice.file_id, voice.file_unique_id
    elif video is not None:
        file_id, unique_id = video.file_id, video.file_unique_id
    else:  # pragma: no cover - guarded by filters
        await safe_reply(update, "⚠️ Please send the file as a document or a photo.")
        return int(state)

    document_type = _document_type_for_state(int(state))
    _store_document(application, document_type, file_id, unique_id, file_name)
    await safe_reply(update, f"✅ {document_type} received.")
    if document_type == DocumentType.SUPPORTING:
        return int(state)
    return await _prompt(update, context, next_state(int(state)))


def _document_type_for_state(state: int) -> str:
    if state == int(TutorStates.DOC_CV):
        return str(DocumentType.CV)
    if state == int(TutorStates.DOC_DEGREE):
        return str(DocumentType.DEGREE)
    return str(DocumentType.SUPPORTING)


def _store_document(
    application: dict[str, Any],
    document_type: str,
    file_id: str,
    unique_id: str | None,
    file_name: str | None = None,
) -> None:
    documents: list[dict[str, Any]] = application.setdefault("documents", [])
    for item in documents:
        if item["document_type"] == document_type:
            item.update(
                {
                    "telegram_file_id": file_id,
                    "telegram_file_unique_id": unique_id,
                    "file_name": file_name,
                }
            )
            return
    documents.append(
        {
            "document_type": document_type,
            "telegram_file_id": file_id,
            "telegram_file_unique_id": unique_id,
            "file_name": file_name,
        }
    )


# ---------------------------------------------------------------------------
# submission
# ---------------------------------------------------------------------------


def build_tutor_payload(telegram_user_id: int, application: dict[str, Any]) -> dict[str, Any]:
    finalise_rates(application)
    availability = _availability_slots(application)
    return {
        "telegram_user_id": telegram_user_id,
        "name": application.get("full_name", ""),
        "display_name": application.get("display_name", ""),
        "phone": application.get("phone", ""),
        "email": application.get("email", ""),
        "country": application.get("country", ""),
        "city": application.get("city", ""),
        "bio": application.get("bio", ""),
        "profile_photo_file_id": _photo_file_id(application),
        "etb_rate": application.get("etb_rate"),
        "usd_rate": application.get("usd_rate"),
        "experience_years": int(application.get("experience_years") or 0),
        "timezone": application.get("timezone") or "UTC",
        "teaching_mode": application.get("teaching_mode", "ONLINE"),
        "subjects": application.get("subjects") or [],
        "levels": application.get("levels") or [],
        "languages": application.get("languages") or [],
        "education": [
            {
                "institution": application.get("edu_institution", ""),
                "degree": application.get("edu_degree", ""),
                "field": application.get("edu_field", ""),
                "graduation_year": application.get("edu_year"),
            }
        ],
        "availability": availability,
        "documents": [
            {
                "document_type": item["document_type"],
                "telegram_file_id": item.get("telegram_file_id"),
                "telegram_file_unique_id": item.get("telegram_file_unique_id"),
            }
            for item in application.get("documents") or []
        ],
    }


def _photo_file_id(application: dict[str, Any]) -> str | None:
    for item in application.get("documents") or []:
        if item.get("document_type") == DocumentType.PROFILE_PHOTO:
            return item.get("telegram_file_id")
    return None


def _availability_slots(application: dict[str, Any]) -> list[dict[str, Any]]:
    days = application.get("availability_days") or []
    times = str(application.get("availability_times") or "").strip()
    timezone = application.get("timezone") or "UTC"
    slots: list[dict[str, Any]] = []
    for day in days:
        for chunk in times.replace(";", ",").split(","):
            chunk = chunk.strip()
            if not chunk or "-" not in chunk:
                continue
            start, end = [part.strip() for part in chunk.split("-", 1)]
            slots.append(
                {
                    "day": day,
                    "start_time": start,
                    "end_time": end,
                    "timezone": timezone,
                }
            )
    return slots


async def submit_application(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Requirement 2.7: persist, archive in Telegram, notify the admin."""
    application = get_application(context)
    telegram_user_id = update.effective_user.id
    missing = _missing_fields(application)
    if missing:
        await safe_reply(
            update, "⚠️ Some required details are still missing:\n• " + "\n• ".join(missing)
        )
        return int(TutorStates.SUMMARY)

    payload = build_tutor_payload(telegram_user_id, application)
    try:
        with session_scope() as session:
            existing = tutor_service.get_tutor_by_telegram_id(session, telegram_user_id)
            if existing is not None:
                await safe_reply(
                    update,
                    f"You already have an application on file: "
                    f"<code>{existing.public_tutor_id}</code> (status: {existing.status}).\n"
                    "Use /my_application to check its progress.",
                )
                reset_application(context)
                set_step(context, None)
                end_flow(context)
                return ConversationHandler.END
            tutor = tutor_service.create_tutor(session, payload)
            tdr_id = tutor.public_tutor_id
    except tutor_service.TutorValidationError as exc:
        await safe_reply(update, f"⚠️ {exc}")
        return int(TutorStates.SUMMARY)
    except Exception:
        logger.error("Tutor application could not be stored")
        await safe_reply(
            update,
            "⚠️ Something went wrong while saving your application. Please try again later.",
        )
        return int(TutorStates.SUMMARY)

    await safe_reply(update, "⏳ Saving your application and uploading your documents…")

    storage = TelegramStorageService(context.bot)
    result = await storage.store_tutor_record(tdr_id, application.get("documents") or [])
    if not result.success:
        await safe_reply(
            update,
            "⚠️ Your application was saved, but our document archive is temporarily "
            "unavailable. Our team will finish the upload manually.",
        )

    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_tdr_id(session, tdr_id)
        if tutor is not None:
            await notification_service.notify_admin_new_tutor(context.bot, tutor)

    await safe_reply(
        update,
        "🎉 <b>Application submitted!</b>\n\n"
        f"Your TDR ID is <code>{tdr_id}</code>.\n"
        "Our verification team will review your documents and contact you.\n"
        "Use /my_application at any time to check your status.",
    )
    logger.info("Tutor application submitted", extra={"tdr_id": tdr_id})
    reset_application(context)
    set_step(context, None)
    end_flow(context)
    return ConversationHandler.END


def _missing_fields(application: dict[str, Any]) -> list[str]:
    required = (
        "full_name",
        "display_name",
        "phone",
        "email",
        "country",
        "city",
        "subjects",
        "levels",
        "teaching_mode",
        "languages",
        "experience_years",
        "bio",
        "edu_institution",
        "edu_degree",
        "edu_field",
        "edu_year",
        "availability_days",
        "availability_times",
        "timezone",
    )
    missing = [
        SUMMARY_LABELS.get(field, field)
        for field in required
        if not application.get(field)
    ]
    documents = {item.get("document_type") for item in application.get("documents") or []}
    for document_type, label in (
        (DocumentType.CV, "CV / Resume"),
        (DocumentType.DEGREE, "Degree / Certificate"),
    ):
        if document_type not in documents:
            missing.append(label)
    if is_ethiopia(application.get("country")) and not application.get("etb_rate"):
        missing.append("ETB rate")
    if not is_ethiopia(application.get("country")) and not application.get("usd_rate"):
        missing.append("USD rate")
    return missing


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


async def start_application(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_application(context)
    start_flow(context, "tutor")
    application = get_application(context)
    application["telegram_user_id"] = update.effective_user.id
    await safe_reply(
        update,
        "👨‍🏫 <b>Tutor application</b>\n\n"
        "It takes about 5 minutes. Use ⬅️ Back, ✏️ Edit and ❌ Cancel at any step.",
    )
    return await _prompt(update, context, int(TutorStates.FULL_NAME))


async def my_application(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Requirement 2.8."""
    with session_scope() as session:
        tutor = tutor_service.get_tutor_by_telegram_id(
            session, update.effective_user.id
        )
        end_flow(context)
        if tutor is None:
            await safe_reply(
                update,
                "You have not submitted an application yet.\n"
                "Tap 👨‍🏫 Become a Tutor to start one.",
            )
            return ConversationHandler.END
        text = (
            "📄 <b>Your application</b>\n\n"
            f"TDR ID: <code>{tutor.public_tutor_id}</code>\n"
            f"Status: <b>{tutor.status}</b>\n"
            f"Submitted: {tutor.created_at:%Y-%m-%d}\n"
            f"Documents archived: {len(tutor.documents)}"
        )
    await safe_reply(update, text, parse_mode="HTML")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# conversation wiring
# ---------------------------------------------------------------------------


def _state_handlers() -> dict[int, list[Any]]:
    handlers: dict[int, list[Any]] = {}
    for state in STATE_ORDER:
        handlers[state] = [
            CallbackQueryHandler(callback_handler),
            MessageHandler(
                filters.Document.ALL | filters.VOICE | filters.VIDEO,
                document_handler,
            ),
            MessageHandler(filters.PHOTO, photo_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler),
        ]
    return handlers


def build_tutor_conversation() -> ConversationHandler:
    """Assemble the tutor application ConversationHandler."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_application, pattern=r"^menu:become_tutor$"),
            CommandHandler("my_application", my_application),
        ],
        states=_state_handlers(),
        fallbacks=[
            CommandHandler("cancel", _cancel),
            MessageHandler(filters.COMMAND, _cancel),
        ],
        name="tutor_application",
        allow_reentry=True,
        conversation_timeout=None,
    )


__all__ = [
    "STEP_SEQUENCE",
    "STEPS_BY_STATE",
    "SUMMARY_LABELS",
    "build_tutor_conversation",
    "build_tutor_payload",
    "callback_handler",
    "document_handler",
    "finalise_rates",
    "my_application",
    "next_state",
    "photo_handler",
    "prev_state",
    "start_application",
    "submit_application",
    "summary_view",
    "text_handler",
]