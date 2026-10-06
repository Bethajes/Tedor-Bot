"""Bot wiring, keyboard and conversation-flow tests."""

from __future__ import annotations

from app.bot import build_application
from app.bot.handlers import start, student, tutor
from app.bot.keyboards import (
    admin_menu_keyboard,
    main_menu_keyboard,
    student_navigation_keyboard,
    summary_keyboard,
    tutor_review_keyboard,
)
from app.bot.states import AdminStates, StudentStates, SupportStates, TutorStates
from app.enums import DocumentType


# ---------------------------------------------------------------------------
# states
# ---------------------------------------------------------------------------


def test_tutor_states_cover_all_26_steps() -> None:
    assert len(TutorStates) == 26
    assert len(tutor.STEP_SEQUENCE) == 26
    assert [int(step.state) for step in tutor.STEP_SEQUENCE] == tutor.STATE_ORDER
    assert int(tutor.STEP_SEQUENCE[-1].state) == int(TutorStates.SUMMARY)


def test_navigation_moves_forward_and_backward() -> None:
    first = int(TutorStates.FULL_NAME)
    second = int(TutorStates.DISPLAY_NAME)
    assert tutor.next_state(first) == second
    assert tutor.prev_state(second) == first
    assert tutor.prev_state(first) == first
    assert tutor.next_state(int(TutorStates.SUMMARY)) == int(TutorStates.SUMMARY)


def test_every_step_has_a_prompt_except_the_summary() -> None:
    for step in tutor.STEP_SEQUENCE:
        if step.kind != "summary":
            assert step.prompt.strip(), step.field


def test_application_wiring(api_env) -> None:
    application = build_application()
    handlers = application.handlers[0]
    names = {getattr(handler, "name", None) for handler in handlers}
    assert {"tutor_application", "student_request", "support", "admin"} <= names
    assert application.error_handlers


def test_optional_steps_offer_skip() -> None:
    optional = [
        step for step in tutor.STEP_SEQUENCE if step.optional
    ]
    assert {step.state for step in optional} == {
        int(TutorStates.PHOTO),
        int(TutorStates.DOC_EXTRA),
    }


# ---------------------------------------------------------------------------
# keyboards
# ---------------------------------------------------------------------------


def _buttons(keyboard):
    return [button for row in keyboard.inline_keyboard for button in row]


def test_main_menu_contains_required_buttons() -> None:
    labels = [button.text for button in _buttons(main_menu_keyboard())]
    assert "👨‍🏫 Become a Tutor" in labels
    assert "🔎 Find a Tutor" in labels
    assert "📩 Contact Us" in labels
    assert "ℹ️ How It Works" in labels
    assert "🌐 Visit Website" in labels


def test_admin_review_keyboard_actions(api_env) -> None:
    buttons = _buttons(tutor_review_keyboard("TDR-000123"))
    data = [button.callback_data for button in buttons]
    assert "admin:verify:TDR-000123" in data
    assert "admin:reject:TDR-000123" in data
    assert "admin:request_info:TDR-000123" in data
    assert "admin:under_review:TDR-000123" in data


def test_navigation_keyboard_has_back_edit_cancel_continue() -> None:
    from app.bot.keyboards.tutor import nav_keyboard

    labels = [button.text for button in _buttons(nav_keyboard())]
    assert "⬅️ Back" in labels
    assert "✏️ Edit" in labels
    assert "❌ Cancel" in labels
    assert "✅ Continue" in labels


def test_all_callback_data_fits_telegram_limit() -> None:
    from app.bot.handlers.tutor import STEP_SEQUENCE
    from app.bot.keyboards.tutor import choice_keyboard, multi_select_keyboard

    keyboards = [
        main_menu_keyboard(),
        admin_menu_keyboard(),
        student_navigation_keyboard(),
        summary_keyboard(),
        tutor_review_keyboard("TDR-000999"),
    ]
    for step in STEP_SEQUENCE:
        keyboards.append(tutor._keyboard_for(step, {}))
        if step.kind == "multiselect":
            for option in list(step.options)[:5]:
                keyboards.append(
                    multi_select_keyboard(f"tutor:{step.field}", [option], [option])
                )
        elif step.kind == "choice":
            for option in list(step.options)[:5]:
                label = option[1] if isinstance(option, tuple) else option
                keyboards.append(choice_keyboard(f"tutor:{step.field}", [label]))
    for keyboard in keyboards:
        for button in _buttons(keyboard):
            if button.callback_data is None:
                continue
            assert len(button.callback_data.encode("utf-8")) <= 64, button.callback_data


# ---------------------------------------------------------------------------
# application payload
# ---------------------------------------------------------------------------


def _complete_application() -> dict:
    return {
        "full_name": "Abebe Bekele",
        "display_name": "Abebe B.",
        "phone": "+251911234567",
        "email": "abebe@example.com",
        "country": "Ethiopia",
        "city": "Addis Ababa",
        "subjects": ["Mathematics", "Physics"],
        "levels": ["Grade 9-10"],
        "teaching_mode": "ONLINE",
        "languages": ["Amharic", "English"],
        "experience_years": 5,
        "bio": "I teach maths and physics.",
        "edu_institution": "AAU",
        "edu_degree": "BSc",
        "edu_field": "Mathematics",
        "edu_year": 2016,
        "availability_days": ["Monday", "Saturday"],
        "availability_times": "09:00-12:00, 16:00-18:00",
        "timezone": "EAT (UTC+3)",
        "documents": [
            {
                "document_type": str(DocumentType.PROFILE_PHOTO),
                "telegram_file_id": "photo-1",
                "telegram_file_unique_id": "uniq-photo-1",
            },
            {
                "document_type": str(DocumentType.CV),
                "telegram_file_id": "cv-1",
                "telegram_file_unique_id": "uniq-cv-1",
            },
            {
                "document_type": str(DocumentType.DEGREE),
                "telegram_file_id": "deg-1",
                "telegram_file_unique_id": "uniq-deg-1",
            },
        ],
    }


def test_build_tutor_payload_is_complete() -> None:
    application = _complete_application()
    application["etb_rate"] = 350.0
    payload = tutor.build_tutor_payload(-8001, application)

    assert payload["telegram_user_id"] == -8001
    assert payload["name"] == "Abebe Bekele"
    assert payload["profile_photo_file_id"] == "photo-1"
    assert payload["subjects"] == ["Mathematics", "Physics"]
    assert len(payload["documents"]) == 3
    assert payload["education"][0]["graduation_year"] == 2016
    # 2 days × 2 time ranges
    assert len(payload["availability"]) == 4
    assert payload["availability"][0]["day"] == "Monday"
    assert payload["availability"][0]["timezone"] == "EAT (UTC+3)"
    assert tutor._missing_fields(application) == []


def test_missing_fields_are_reported() -> None:
    application = _complete_application()
    application.pop("bio")
    application["documents"] = []
    missing = tutor._missing_fields(application)
    assert "Biography" in missing
    assert "CV / Resume" in missing
    assert "Degree / Certificate" in missing


def test_summary_view_marks_documents() -> None:
    view = tutor.summary_view(_complete_application())
    assert view["photo"] == "✅ received"
    assert view["doc_cv"] == "✅ received"
    assert view["subjects"] == "Mathematics, Physics"


def test_currency_conversion_for_non_ethiopian_tutors() -> None:
    application = {
        "country": "Kenya",
        "etb_rate": 3900.0,
        "usd_rate": None,
    }
    tutor.finalise_rates(application)
    assert application["usd_rate"] == 30.0


def test_document_store_replaces_same_type() -> None:
    application: dict = {}
    tutor._store_document(application, str(DocumentType.CV), "file-1", "u1")
    tutor._store_document(application, str(DocumentType.CV), "file-2", "u2")
    assert len(application["documents"]) == 1
    assert application["documents"][0]["telegram_file_id"] == "file-2"


# ---------------------------------------------------------------------------
# student and support flows
# ---------------------------------------------------------------------------


def test_student_request_payload_defaults_to_etb_for_ethiopia() -> None:
    from tests.factories import student_request_payload

    payload = student_request_payload(-8100)
    assert payload["country"] == "Ethiopia"
    assert student._budget_view(payload) == "500 ETB"
    payload["country"] = "Kenya"
    assert student._budget_view(payload) == "500 USD"


def test_support_categories_cover_requirement() -> None:
    from app.bot.keyboards.support import CATEGORY_BUTTONS

    labels = [label for label, _code in CATEGORY_BUTTONS]
    assert labels == [
        "💬 General Question",
        "👨‍🏫 Tutor Application",
        "👨‍🎓 Finding a Tutor",
        "💳 Payment",
        "🛠 Technical Problem",
        "👤 Talk to Admin",
    ]


def test_start_and_help_text_is_complete() -> None:
    assert "Welcome to Tedor Tutors" in start.WELCOME
    assert "How Tedor Tutors works" in start.HOW_IT_WORKS
    for command in ("/start", "/help", "/cancel", "/my_application", "/support"):
        assert command in start.HELP_TEXT


def test_help_command_lists_commands() -> None:
    assert "/stats" in start.HELP_TEXT


def test_state_enums_are_distinct() -> None:
    values = [
        {int(item) for item in enum}
        for enum in (TutorStates, StudentStates, SupportStates, AdminStates)
    ]
    for index, left in enumerate(values):
        for right in values[index + 1 :]:
            assert not left & right