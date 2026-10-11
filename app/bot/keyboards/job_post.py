"""Job-post generator keyboards.

Owns the ``post:`` callback namespace. Every keyboard here has a counterpart in
the template or the flow — there is deliberately no requirements button.
"""

from __future__ import annotations

from collections.abc import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.templates.tutor_job_post import REQUIRED_FIELDS

BTN_BACK = "⬅️ Back"
BTN_EDIT = "✏️ Edit"
BTN_CANCEL = "❌ Cancel"
BTN_PREVIEW = "👁 Preview"
BTN_PUBLISH = "📢 Publish"

CB_BACK = "post:back"
CB_EDIT = "post:edit"
CB_CANCEL = "post:cancel"
CB_PREVIEW = "post:preview"
CB_PUBLISH = "post:publish"

CB_PREFIX = "post"

#: Editable variables, in the order they are collected. No requirements entry.
EDITABLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("location", "📍 Location"),
    ("student_level", "✍️ Student's Level"),
    ("tutor_category", "✍️ Tutor Category"),
    ("duration", "✍️ Duration"),
    ("frequency", "✍️ Frequency"),
    ("subjects", "✍️ Target Subjects"),
    ("compensation", "💰 Compensation"),
)

#: Guard: the edit menu can never offer a requirements field.
assert {key for key, _label in EDITABLE_FIELDS} == set(REQUIRED_FIELDS)


def post_preview_keyboard() -> InlineKeyboardMarkup:
    """✏️ Edit / ❌ Cancel / 📢 Publish — nothing is sent automatically."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(BTN_EDIT, callback_data=CB_EDIT)],
            [
                InlineKeyboardButton(BTN_CANCEL, callback_data=CB_CANCEL),
                InlineKeyboardButton(BTN_PUBLISH, callback_data=CB_PUBLISH),
            ],
        ]
    )


def post_edit_keyboard() -> InlineKeyboardMarkup:
    """Per-variable edit menu shown from the preview."""
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for key, label in EDITABLE_FIELDS:
        current.append(
            InlineKeyboardButton(label, callback_data=f"{CB_PREFIX}:editfield:{key}")
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append(
        [
            InlineKeyboardButton(BTN_PREVIEW, callback_data=CB_PREVIEW),
            InlineKeyboardButton(BTN_CANCEL, callback_data=CB_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)


def post_choice_keyboard(
    field: str, options: Sequence[str], *, show_back: bool = True
) -> InlineKeyboardMarkup:
    """Single-choice grid in the ``post:`` namespace; free text also works."""
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for index, option in enumerate(options):
        current.append(
            InlineKeyboardButton(
                option, callback_data=f"{CB_PREFIX}:{field}:pick:{index}"
            )
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    nav = [InlineKeyboardButton(BTN_CANCEL, callback_data=CB_CANCEL)]
    if show_back:
        nav.insert(0, InlineKeyboardButton(BTN_BACK, callback_data=CB_BACK))
    rows.append(nav)
    return InlineKeyboardMarkup(rows)


def post_nav_keyboard(*, show_back: bool = True, show_preview: bool = True) -> InlineKeyboardMarkup:
    """Navigation for the free-text steps."""
    rows: list[list[InlineKeyboardButton]] = []
    top: list[InlineKeyboardButton] = []
    if show_back:
        top.append(InlineKeyboardButton(BTN_BACK, callback_data=CB_BACK))
    if show_preview:
        top.append(InlineKeyboardButton(BTN_PREVIEW, callback_data=CB_PREVIEW))
    if top:
        rows.append(top)
    rows.append([InlineKeyboardButton(BTN_CANCEL, callback_data=CB_CANCEL)])
    return InlineKeyboardMarkup(rows)


__all__ = [
    "CB_BACK",
    "CB_CANCEL",
    "CB_EDIT",
    "CB_PREVIEW",
    "CB_PUBLISH",
    "EDITABLE_FIELDS",
    "post_choice_keyboard",
    "post_edit_keyboard",
    "post_nav_keyboard",
    "post_preview_keyboard",
]