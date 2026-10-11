"""Helpers for opening the Tedor Mini App from the bot.

The Mini App is opened as a Telegram WebApp button. ``window.Telegram.WebApp``
is only populated for a WebApp button, so the Init Data the Mini App API
validates arrives for free on every request made from one — which is why the
buttons below are built here rather than as plain URL buttons.

Nothing personal is ever placed in these URLs: the only values carried are the
screen to open and, for a job, its public ``JOB-000123`` reference.

Requirements: 1.1, 1.2, 1.3, 1.4
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, WebAppInfo

from app.config import settings


def mini_app_url(screen: str, ref: str | None = None) -> str | None:
    """The Mini App URL for a screen, or ``None`` when it is not configured.

    ``ref`` is appended as a hash parameter, so a deep link opens the screen
    already scoped to one job. Hash parameters are never sent to the server:
    the Mini App reads them client-side, and the API is addressed separately.
    """
    base = str(getattr(settings, "mini_app_url", "") or "").strip()
    if not base:
        return None
    if ref:
        return f"{base}#screen={screen}&ref={ref}"
    return f"{base}#screen={screen}"


def web_app_button(
    text: str, screen: str, ref: str | None = None
) -> InlineKeyboardButton | None:
    """A WebApp button opening the Mini App, or ``None`` when unconfigured.

    A deployment without ``MINI_APP_URL`` never renders the button: an empty
    WebApp URL is rejected by Telegram at send time, so skipping it keeps the
    bot working while the Mini App is not deployed yet.
    """
    url = mini_app_url(screen, ref)
    if url is None:
        return None
    return InlineKeyboardButton(text, web_app=WebAppInfo(url=url))


__all__ = ["mini_app_url", "web_app_button"]
