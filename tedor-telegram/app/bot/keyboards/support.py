"""Support ticket keyboards (Requirement 8.1)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

CATEGORY_BUTTONS: tuple[tuple[str, str], ...] = (
    ("💬 General Question", "GENERAL_QUESTION"),
    ("👨‍🏫 Tutor Application", "TUTOR_APPLICATION"),
    ("👨‍🎓 Finding a Tutor", "FINDING_A_TUTOR"),
    ("💳 Payment", "PAYMENT"),
    ("🛠 Technical Problem", "TECHNICAL_PROBLEM"),
    ("👤 Talk to Admin", "TALK_TO_ADMIN"),
)

CATEGORY_LABELS = {code: label for label, code in CATEGORY_BUTTONS}


def support_category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"support:cat:{code}")]
        for label, code in CATEGORY_BUTTONS
    ]
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def support_nav_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("❌ Cancel", callback_data="support:cancel")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")],
        ]
    )


def support_ticket_keyboard(ticket_id: str) -> InlineKeyboardMarkup:
    """Attached to the admin notification so replies can be relayed."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "↩️ Reply to user", callback_data=f"support:reply:{ticket_id}"
                )
            ],
            [InlineKeyboardButton("✅ Mark resolved", callback_data=f"support:close:{ticket_id}")],
        ]
    )