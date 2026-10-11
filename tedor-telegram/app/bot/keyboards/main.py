"""Main menu and help keyboards (Requirement 1.1)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.mini_app import web_app_button
from app.config import settings

BTN_BECOME_TUTOR = "👨‍🏫 Become a Tutor"
BTN_FIND_TUTOR = "🔎 Find a Tutor"
BTN_CONTACT = "📩 Contact Us"
BTN_HOW_IT_WORKS = "ℹ️ How It Works"
BTN_HELP = "❓ Help"
BTN_WEBSITE = "🌐 Visit Website"
#: Mini App entry points (Requirements 1.1, 1.2). Both are WebApp buttons: they
#: open the Mini App inside Telegram, where the validated Init Data is attached
#: to every request it makes.
BTN_TUTOR_APPLICATION_APP = "📝 Complete Tutor Application"
BTN_REQUEST_TUTOR_APP = "👨‍👩‍👧 Request a Tutor"

CB_BECOME_TUTOR = "menu:become_tutor_ethiopia"
CB_FIND_TUTOR = "menu:find_tutor"
CB_CONTACT = "menu:contact"
CB_HOW = "menu:how"
CB_HELP = "menu:help"


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Primary entry keyboard shown after ``/start``."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(BTN_BECOME_TUTOR, callback_data=CB_BECOME_TUTOR)],
        [InlineKeyboardButton(BTN_FIND_TUTOR, callback_data=CB_FIND_TUTOR)],
        [InlineKeyboardButton(BTN_CONTACT, callback_data=CB_CONTACT)],
        [
            InlineKeyboardButton(BTN_HOW_IT_WORKS, callback_data=CB_HOW),
            InlineKeyboardButton(BTN_HELP, callback_data=CB_HELP),
        ],
        [
            InlineKeyboardButton(
                BTN_WEBSITE, url=settings.website_url or "https://tedortutors.com"
            )
        ],
    ]
    # The Mini App rows are added alongside — never instead of — the existing
    # buttons, and only when a Mini App URL is configured (Requirements 1.1,
    # 1.2). The bot flow keeps working unchanged without one.
    mini_app_row = [
        button
        for button in (
            web_app_button(BTN_TUTOR_APPLICATION_APP, "tutor"),
            web_app_button(BTN_REQUEST_TUTOR_APP, "parent"),
        )
        if button is not None
    ]
    if mini_app_row:
        rows.insert(len(rows) - 1, mini_app_row)
    return InlineKeyboardMarkup(rows)


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(BTN_HOW_IT_WORKS, callback_data=CB_HOW)],
            [
                InlineKeyboardButton(BTN_BECOME_TUTOR, callback_data=CB_BECOME_TUTOR),
                InlineKeyboardButton(BTN_FIND_TUTOR, callback_data=CB_FIND_TUTOR),
            ],
            [InlineKeyboardButton(BTN_CONTACT, callback_data=CB_CONTACT)],
        ]
    )


def how_it_works_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(BTN_BECOME_TUTOR, callback_data=CB_BECOME_TUTOR),
                InlineKeyboardButton(BTN_FIND_TUTOR, callback_data=CB_FIND_TUTOR),
            ]
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")]]
    )


def website_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(
        BTN_WEBSITE, url=settings.website_url or "https://tedortutors.com"
    )