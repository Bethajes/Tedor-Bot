"""Currency helpers shared by the tutor and student flows.

Requirement 2.4/2.5: Ethiopian tutors quote ETB, everybody else quotes USD.
"""

from __future__ import annotations

from app.config import settings
from app.enums import Currency
from app.models import COUNTRY_ETHIOPIA

_ETHIOPIA_ALIASES = {
    "ethiopia",
    "ethiopian",
    "et",
    "eth",
    "addis ababa, ethiopia",
    "ethiopia 🇪🇹",
}


def normalise_country(country: str | None) -> str:
    return (country or "").strip()


def is_ethiopia(country: str | None) -> bool:
    """True when ``country`` denotes Ethiopia (case/whitespace insensitive)."""
    cleaned = normalise_country(country).strip().lower()
    return cleaned in _ETHIOPIA_ALIASES


def currency_for_country(country: str | None) -> Currency:
    """Primary currency for the given country."""
    return Currency.ETB if is_ethiopia(country) else Currency.USD


def other_currency(country: str | None) -> Currency:
    return Currency.USD if is_ethiopia(country) else Currency.ETB


def convert(amount: float, source: Currency, target: Currency) -> float:
    """Convert between ETB and USD using the configured reference rate."""
    if source == target:
        return round(float(amount), 2)
    rate = settings.etb_per_usd
    if source == Currency.ETB and target == Currency.USD:
        return round(float(amount) / rate, 2)
    return round(float(amount) * rate, 2)


def format_rate(amount: float | None, currency: Currency) -> str:
    if amount is None:
        return "—"
    symbol = "ETB" if currency == Currency.ETB else "USD"
    return f"{amount:g} {symbol}"


__all__ = [
    "COUNTRY_ETHIOPIA",
    "convert",
    "currency_for_country",
    "format_rate",
    "is_ethiopia",
    "normalise_country",
    "other_currency",
]