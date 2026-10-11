"""Tutor application keyboards (Requirements 2.2, 2.6)."""

from __future__ import annotations

from collections.abc import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

BTN_BACK = "⬅️ Back"
BTN_EDIT = "✏️ Edit"
BTN_CANCEL = "❌ Cancel"
BTN_CONTINUE = "✅ Continue"
BTN_SKIP = "⏭ Skip"
BTN_DONE = "✅ Done"
BTN_SUBMIT = "✅ Submit Application"

CB_BACK = "tutor:back"
CB_EDIT = "tutor:edit"
CB_CANCEL = "tutor:cancel"
CB_CONTINUE = "tutor:continue"
CB_SKIP = "tutor:skip"
CB_SUBMIT = "tutor:submit"

_EDIT_FIELDS: tuple[tuple[str, str], ...] = (
    ("full_name", "👤 Full name"),
    ("display_name", "🏷 Display name"),
    ("phone", "📞 Phone"),
    ("email", "✉️ Email"),
    ("country", "🌍 Country"),
    ("city", "🏙 City"),
    ("photo", "🖼 Profile photo"),
    ("subjects", "📚 Subjects"),
    ("levels", "🎓 Education levels"),
    ("teaching_mode", "🧑‍🏫 Teaching mode"),
    ("languages", "🗣 Languages"),
    ("experience", "💼 Years of experience"),
    ("bio", "📝 Biography"),
    ("education", "🎓 Education record"),
    ("etb_rate", "💰 ETB rate"),
    ("usd_rate", "💵 USD rate"),
    ("availability", "🗓 Availability"),
    ("timezone", "🕒 Timezone"),
    ("doc_cv", "📄 CV / Resume"),
    ("doc_degree", "📜 Degree / Certificate"),
    ("doc_extra", "📎 Supporting documents"),
)


def nav_keyboard(
    *, show_back: bool = True, show_edit: bool = True, show_continue: bool = True
) -> InlineKeyboardMarkup:
    """⬅️ Back / ✏️ Edit / ❌ Cancel / ✅ Continue navigation."""
    rows: list[list[InlineKeyboardButton]] = []
    top: list[InlineKeyboardButton] = []
    if show_back:
        top.append(InlineKeyboardButton(BTN_BACK, callback_data=CB_BACK))
    if show_edit:
        top.append(InlineKeyboardButton(BTN_EDIT, callback_data=CB_EDIT))
    if top:
        rows.append(top)

    bottom: list[InlineKeyboardButton] = [
        InlineKeyboardButton(BTN_CANCEL, callback_data=CB_CANCEL)
    ]
    if show_continue:
        bottom.append(InlineKeyboardButton(BTN_CONTINUE, callback_data=CB_CONTINUE))
    rows.append(bottom)
    return InlineKeyboardMarkup(rows)


def skip_keyboard(continue_cb: str = CB_CONTINUE) -> InlineKeyboardMarkup:
    """Optional step: full navigation plus a skip action."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(BTN_BACK, callback_data=CB_BACK),
                InlineKeyboardButton(BTN_EDIT, callback_data=CB_EDIT),
            ],
            [
                InlineKeyboardButton(BTN_SKIP, callback_data=CB_SKIP),
                InlineKeyboardButton(BTN_CONTINUE, callback_data=continue_cb),
            ],
            [InlineKeyboardButton(BTN_CANCEL, callback_data=CB_CANCEL)],
        ]
    )


def currency_confirm_keyboard(
    primary_cb: str, other_cb: str
) -> InlineKeyboardMarkup:
    """Accept the converted secondary rate or enter it manually."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Use this rate", callback_data=primary_cb),
                InlineKeyboardButton("✏️ Enter manually", callback_data=other_cb),
            ],
            [InlineKeyboardButton(BTN_BACK, callback_data=CB_BACK)],
        ]
    )


def multi_select_keyboard(
    prefix: str, options: Sequence[str], selected: Sequence[str] = ()
) -> InlineKeyboardMarkup:
    """Toggleable option grid. ``prefix`` is the callback namespace."""
    selected_set = {value.casefold() for value in selected}
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for index, option in enumerate(options):
        mark = "✅ " if option.casefold() in selected_set else ""
        current.append(
            InlineKeyboardButton(
                f"{mark}{option}", callback_data=f"{prefix}:toggle:{index}"
            )
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)

    rows.append(
        [
            InlineKeyboardButton(BTN_DONE, callback_data=f"{prefix}:done"),
            InlineKeyboardButton(BTN_BACK, callback_data=CB_BACK),
            InlineKeyboardButton(BTN_CANCEL, callback_data=CB_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)


def choice_keyboard(
    prefix: str, options: Sequence[str], *, nav: bool = True
) -> InlineKeyboardMarkup:
    """Single-choice grid; the callback data carries the option index."""
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for index, option in enumerate(options):
        current.append(
            InlineKeyboardButton(option, callback_data=f"{prefix}:pick:{index}")
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    if nav:
        rows.append(
            [
                InlineKeyboardButton(BTN_BACK, callback_data=CB_BACK),
                InlineKeyboardButton(BTN_CANCEL, callback_data=CB_CANCEL),
            ]
        )
    return InlineKeyboardMarkup(rows)


def summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(BTN_SUBMIT, callback_data=CB_SUBMIT)],
            [InlineKeyboardButton(BTN_EDIT, callback_data=CB_EDIT)],
            [InlineKeyboardButton(BTN_BACK, callback_data=CB_BACK)],
            [InlineKeyboardButton(BTN_CANCEL, callback_data=CB_CANCEL)],
        ]
    )


def summary_edit_keyboard() -> InlineKeyboardMarkup:
    """Per-field edit menu displayed from the application summary."""
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for key, label in _EDIT_FIELDS:
        current.append(
            InlineKeyboardButton(label, callback_data=f"tutor:editfield:{key}")
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append(
        [
            InlineKeyboardButton("✅ Back to summary", callback_data="tutor:summary"),
            InlineKeyboardButton(BTN_CANCEL, callback_data=CB_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)


__all__ = [
    "choice_keyboard",
    "currency_confirm_keyboard",
    "multi_select_keyboard",
    "nav_keyboard",
    "skip_keyboard",
    "summary_edit_keyboard",
    "summary_keyboard",
]