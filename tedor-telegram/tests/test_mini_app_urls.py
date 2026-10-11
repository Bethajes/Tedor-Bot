"""Property-based test for Mini App URL construction.

Covers:
   Property 15: Job URL construction embeds the reference  (Requirements 1.3, 1.4)
"""

from __future__ import annotations

import re

import pytest
from hypothesis import HealthCheck, given, settings as hyp_settings
from hypothesis import strategies as st
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.job_apply import (
    BTN_APPLY_IN_APP,
    apply_intro_keyboard,
    recommendation_keyboard,
)
from app.bot.keyboards.main import (
    BTN_REQUEST_TUTOR_APP,
    BTN_TUTOR_APPLICATION_APP,
    main_menu_keyboard,
)
from app.bot.mini_app import mini_app_url, web_app_button
from app.config import settings

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

#: Job references are exactly ``JOB-`` plus six digits (see
#: ``app.services.job_post_service.JOB_POST_PATTERN``).
_job_reference = st.from_regex(r"JOB-[0-9]{6}", fullmatch=True)

_mini_app_base = st.from_regex(
    r"https://[a-z0-9.-]+\.[a-z]{2,}(/[a-z0-9._-]*)?", fullmatch=True
)


@pytest.fixture
def mini_app_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``MINI_APP_URL`` at a plausible deployed URL for the test."""
    monkeypatch.setattr(settings, "mini_app_url", "https://app.example.com/mini", raising=False)


@pytest.fixture
def mini_app_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mini_app_url", "", raising=False)


# ---------------------------------------------------------------------------
# Property 15: Job URL construction embeds the reference
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 15: Job URL construction embeds the reference
@given(base=_mini_app_base, reference=_job_reference)
@hyp_settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_job_url_embeds_the_reference(
    monkeypatch: pytest.MonkeyPatch,
    base: str,
    reference: str,
) -> None:
    """For any valid job reference, the built Mini App URL carries it.

    The reference is the only identifier the job screen has: dropping it, or
    escaping it into something the frontend cannot read back out of the hash,
    would open the wrong job.

    Validates: Requirements 1.3, 1.4
    """
    monkeypatch.setattr(settings, "mini_app_url", base, raising=False)

    url = mini_app_url("job", reference)

    assert url is not None, "a configured Mini App URL must produce a URL"
    assert url.startswith(base), url
    assert reference in url, f"the reference {reference} is missing from {url}"
    # The reference is read back out of the hash by the frontend, so it must
    # survive a round trip unchanged.
    hash_part = url.split("#", 1)[1]
    params = dict(
        pair.split("=", 1) for pair in hash_part.split("&") if "=" in pair
    )
    assert params["screen"] == "job"
    assert params["ref"] == reference
    assert re.search(r"ref=" + re.escape(reference) + r"$", url), url


# Feature: tedor-mini-app, Property 15: Job URL construction embeds the reference
@given(reference=_job_reference)
@hyp_settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_job_web_app_button_embeds_the_reference(
    mini_app_configured: None,
    reference: str,
) -> None:
    """The job WebApp button must point at the same reference.

    Validates: Requirements 1.3, 1.4
    """
    button = web_app_button(BTN_APPLY_IN_APP, "job", reference)

    assert button is not None, "a configured Mini App URL must yield a button"
    assert button.web_app is not None, "the button must be a WebApp button"
    assert reference in button.web_app.url, (
        f"the button URL {button.web_app.url} does not carry {reference}"
    )


# Feature: tedor-mini-app, Property 15: Job URL construction embeds the reference
@given(reference=_job_reference)
@hyp_settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_job_keyboards_carry_a_web_app_button_with_the_reference(
    mini_app_configured: None,
    reference: str,
) -> None:
    """Both private-chat job keyboards offer the Mini App alongside the bot
    buttons, and the WebApp URL embeds the reference.

    The existing callback buttons must survive untouched (Requirement 1.4).

    Validates: Requirements 1.3, 1.4
    """
    for keyboard in (
        apply_intro_keyboard(reference, "tedor_bot"),
        recommendation_keyboard(reference),
    ):
        web_app_urls = [
            button.web_app.url
            for row in keyboard.inline_keyboard
            for button in row
            if button.web_app is not None
        ]
        assert web_app_urls, "the Mini App button is missing"
        assert any(reference in url for url in web_app_urls), (
            f"no Mini App button carries {reference}: {web_app_urls}"
        )
        # The bot's own buttons are still there.
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]
        assert callbacks, "the bot buttons were removed"
        assert all(reference in data for data in callbacks), callbacks


# ---------------------------------------------------------------------------
# The main menu buttons
# ---------------------------------------------------------------------------


def _web_app_buttons(keyboard: InlineKeyboardMarkup) -> list[InlineKeyboardButton]:
    return [
        button
        for row in keyboard.inline_keyboard
        for button in row
        if button.web_app is not None
    ]


def test_main_menu_offers_both_mini_app_entry_points(
    mini_app_configured: None,
) -> None:
    """Requirements 1.1, 1.2: the main menu opens both Mini App screens."""
    keyboard = main_menu_keyboard()

    web_app_urls = {button.web_app.url for button in _web_app_buttons(keyboard)}
    assert web_app_urls, "no Mini App buttons on the main menu"

    labels = {button.text for button in _web_app_buttons(keyboard)}
    assert BTN_TUTOR_APPLICATION_APP in labels
    assert BTN_REQUEST_TUTOR_APP in labels

    assert any(url.endswith("#screen=tutor") for url in web_app_urls), web_app_urls
    assert any(url.endswith("#screen=parent") for url in web_app_urls), web_app_urls


def test_main_menu_keeps_every_existing_button(mini_app_configured: None) -> None:
    """Requirement 1.4: nothing that worked before may be removed."""
    keyboard = main_menu_keyboard()
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }
    urls = {
        button.url
        for row in keyboard.inline_keyboard
        for button in row
        if button.url
    }

    assert "menu:become_tutor_ethiopia" in callbacks
    assert "menu:find_tutor" in callbacks
    assert "menu:contact" in callbacks
    assert "menu:how" in callbacks
    assert "menu:help" in callbacks
    assert "https://tedortutors.com" in urls


def test_main_menu_without_a_configured_url_is_unchanged(
    mini_app_unset: None,
) -> None:
    """An unconfigured Mini App must leave the keyboard exactly as it was."""
    keyboard = main_menu_keyboard()

    assert _web_app_buttons(keyboard) == []
    # Still the five original rows.
    assert len(keyboard.inline_keyboard) == 5


def test_urls_are_absent_without_configuration(mini_app_unset: None) -> None:
    assert mini_app_url("tutor") is None
    assert mini_app_url("job", "JOB-000001") is None
    assert web_app_button(BTN_APPLY_IN_APP, "job", "JOB-000001") is None
