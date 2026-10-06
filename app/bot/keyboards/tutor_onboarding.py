"""Keyboards for the Ethiopian tutor onboarding flow."""

from __future__ import annotations

from collections.abc import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

#: Simple navigation used at the bottom of most steps.
NAV_ROWS = (
    ["⬅️ Back", "✏️ Edit"],
    ["❌ Cancel", "✅ Continue"],
)

CB_BACK = "eth:back"
CB_EDIT = "eth:edit"
CB_CANCEL = "eth:cancel"
CB_CONTINUE = "eth:continue"
CB_DONE = "eth:done"
CB_SUBMIT = "eth:submit"


def nav_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK),
                InlineKeyboardButton("✏️ Edit", callback_data=CB_EDIT),
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
                InlineKeyboardButton("✅ Continue", callback_data=CB_CONTINUE),
            ],
        ]
    )


def skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⏭ Skip", callback_data=CB_CONTINUE),
                InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
        ]
    )


def voice_keyboard() -> InlineKeyboardMarkup:
    """Offer a voice intro, a skip path, and normal nav."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎤 I'll record one", callback_data="eth:voice:record")],
            [
                InlineKeyboardButton("⏭ Skip for now", callback_data=CB_CONTINUE),
                InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
        ]
    )


def choice_keyboard(field: str, options: Sequence[str]) -> InlineKeyboardMarkup:
    """Single-select option grid in the ``eth:`` namespace."""
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for index, option in enumerate(options):
        current.append(
            InlineKeyboardButton(option, callback_data=f"eth:{field}:pick:{index}")
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK),
            InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)


def english_level_keyboard() -> InlineKeyboardMarkup:
    """The 1-10 English proficiency picker, two columns."""
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for index, (_value, label) in enumerate(ENGLISH_LEVELS):
        current.append(
            InlineKeyboardButton(label, callback_data=f"eth:english:pick:{index}")
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK),
            InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)


def multiselect_keyboard(
    field: str, options: Sequence[str], selected: Sequence[str]
) -> InlineKeyboardMarkup:
    """Toggleable multi-select, two per row, in the ``eth:`` namespace."""
    selected_set = {item.casefold() for item in selected}
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for index, option in enumerate(options):
        mark = "✅ " if option.casefold() in selected_set else ""
        current.append(
            InlineKeyboardButton(
                f"{mark}{option}", callback_data=f"eth:{field}:toggle:{index}"
            )
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append(
        [
            InlineKeyboardButton("✅ Done", callback_data=f"eth:{field}:done"),
            InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK),
        ]
    )
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)])
    return InlineKeyboardMarkup(rows)


def summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Submit application", callback_data=CB_SUBMIT)],
            [InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK)],
            [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
        ]
    )


# Pull the level list in at the end to keep the import machinery simple and
# avoid a circular import in some test contexts.
from app.bot.options import ETH_ENGLISH_LEVELS as ENGLISH_LEVELS  # noqa: E402

ENGLISH_LEVEL_OPTIONS = [label for _v, label in ENGLISH_LEVELS]
ENGLISH_LEVEL_VALUES = [value for value, _l in ENGLISH_LEVELS]

__all__ = [
    "CB_BACK",
    "CB_CANCEL",
    "CB_CONTINUE",
    "CB_DONE",
    "CB_EDIT",
    "CB_SUBMIT",
    "choice_keyboard",
    "english_level_keyboard",
    "multiselect_keyboard",
    "nav_keyboard",
    "skip_keyboard",
    "summary_keyboard",
    "voice_keyboard",
]
