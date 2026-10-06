"""Main menu and help keyboards (Requirement 1.1)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings

BTN_BECOME_TUTOR = "👨‍🏫 Become a Tutor"
BTN_FIND_TUTOR = "🔎 Find a Tutor"
BTN_CONTACT = "📩 Contact Us"
BTN_HOW_IT_WORKS = "ℹ️ How It Works"
BTN_HELP = "❓ Help"
BTN_WEBSITE = "🌐 Visit Website"

CB_BECOME_TUTOR = "menu:become_tutor_ethiopia"
CB_FIND_TUTOR = "menu:find_tutor"
CB_CONTACT = "menu:contact"
CB_HOW = "menu:how"
CB_HELP = "menu:help"


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Primary entry keyboard shown after ``/start``."""
    return InlineKeyboardMarkup(
        [
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
    )


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