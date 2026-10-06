"""Student request keyboards (Requirements 6.x, 7.x)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.search_service import ScoredTutor

BTN_BACK = "⬅️ Back"
BTN_EDIT = "✏️ Edit"
BTN_CANCEL = "❌ Cancel"
BTN_CONTINUE = "✅ Continue"
BTN_SUBMIT = "✅ Submit Request"

CB_BACK = "student:back"
CB_EDIT = "student:edit"
CB_CANCEL = "student:cancel"
CB_CONTINUE = "student:continue"
CB_SUBMIT = "student:submit"
CB_MORE = "stu:more"
CB_VIEW = "stu:view"
CB_SELECT = "stu:select"


def student_navigation_keyboard(*, show_continue: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(BTN_BACK, callback_data=CB_BACK)]
    ]
    bottom = [InlineKeyboardButton(BTN_CANCEL, callback_data=CB_CANCEL)]
    if show_continue:
        bottom.insert(0, InlineKeyboardButton(BTN_CONTINUE, callback_data=CB_CONTINUE))
    rows.append(bottom)
    return InlineKeyboardMarkup(rows)


def student_submit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(BTN_SUBMIT, callback_data=CB_SUBMIT)],
            [InlineKeyboardButton(BTN_EDIT, callback_data=CB_EDIT)],
            [InlineKeyboardButton(BTN_BACK, callback_data=CB_BACK)],
            [InlineKeyboardButton(BTN_CANCEL, callback_data=CB_CANCEL)],
        ]
    )


def tutor_match_keyboard(
    matches: Sequence[ScoredTutor], request_id: str
) -> InlineKeyboardMarkup:
    """👤 View Tutor / 📩 Request Tutor / 🔎 More Tutors per match."""
    rows: list[list[InlineKeyboardButton]] = []
    for item in matches:
        tdr = item.tutor.public_tutor_id
        rows.append(
            [
                InlineKeyboardButton(
                    f"👤 View {item.tutor.display_name}",
                    callback_data=f"{CB_VIEW}:{tdr}:{request_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    f"📩 Request {item.tutor.display_name}",
                    callback_data=f"{CB_SELECT}:{request_id}:{tdr}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("🔎 More Tutors", callback_data=f"{CB_MORE}:{request_id}"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def view_tutor_keyboard(tdr_id: str, request_id: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if request_id:
        rows.append(
            [
                InlineKeyboardButton(
                    "📩 Request This Tutor",
                    callback_data=f"{CB_SELECT}:{request_id}:{tdr_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back to results", callback_data=f"{CB_MORE}:{request_id or ''}"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def tutor_card_keyboard(tdr_id: str, extra: dict[str, Any] | None = None) -> InlineKeyboardMarkup:
    extra = extra or {}
    rows = [list(row) for row in extra.get("rows", [])]
    rows.append(
        [
            InlineKeyboardButton("🌐 Open website", callback_data="menu:website"),
            InlineKeyboardButton("🔎 Find another tutor", callback_data="menu:find_tutor"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def no_match_keyboard(request_id: str) -> InlineKeyboardMarkup:
    """Requirement 7.5: offer to forward the request to the support team."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📩 Send to support team", callback_data=f"stu:support:{request_id}"
                )
            ],
            [
                InlineKeyboardButton("🔎 Change my search", callback_data="menu:find_tutor"),
                InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home"),
            ],
        ]
    )