"""Web login for the Mini App outside Telegram (Requirements 2.5, 2.6).

The Mini App is normally opened from a chat, where signed Init Data identifies
the user and this module is not involved. In a plain browser there is no Init
Data, so a person signs in with the phone number already on their application
and a one-time code.

Three properties matter more than convenience here:

* **It cannot create access.** A code is only issued for a phone number that
  already belongs to a tutor or a parent request. An unknown number gets the
  same answer as a rate-limited one, so the endpoint cannot be used to find out
  who has applied.
* **The code is stored hashed.** Reading the table is not enough to log in.
* **It is one-time and short-lived.** A code is consumed on first use and
  expires; the token it buys expires too, and carries the same
  ``telegram_user_id`` the Init Data would, so no route handler changes.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import StudentRequest, Tutor, WebLoginCode, utcnow

logger = logging.getLogger(__name__)

#: Digits only, six of them: no letters, no lookalike characters.
CODE_DIGITS = 6


class LoginError(Exception):
    """A login attempt could not be completed."""


@dataclass(frozen=True)
class IssuedCode:
    """What the caller may be told about a freshly issued code."""

    delivered: bool
    #: Populated only when ``MINI_APP_DEV_CODES`` is on, i.e. local development.
    dev_code: str | None = None


def hash_code(code: str) -> str:
    """SHA-256 of the code, hex encoded."""
    return hashlib.sha256(str(code).encode()).hexdigest()


def normalise_phone(phone: str | None) -> str:
    """Digits and a leading ``+``, so ``0911…`` and ``+251911…`` can both match.

    Storing one spelling is what makes the lookup below possible at all; a
    parent who typed their number without the country code still finds their
    request.
    """
    text = str(phone or "").strip()
    keep = [char for char in text if char.isdigit() or char == "+"]
    cleaned = "".join(keep)
    if cleaned.startswith("+"):
        return "+" + cleaned[1:]
    return cleaned


def find_user_by_phone(session: Session, phone: str) -> int | None:
    """The Telegram user id owning this phone number, or ``None``.

    A tutor wins over a parent request: a tutor may also have sent a request
    for their own child, and the tutor application is the one with data behind
    it.
    """
    cleaned = normalise_phone(phone)
    if not cleaned:
        return None

    tutor_id = session.execute(
        select(Tutor.telegram_user_id).where(Tutor.phone.in_(_phone_candidates(cleaned)))
    ).scalars().first()
    if tutor_id:
        return int(tutor_id)

    requester = session.execute(
        select(StudentRequest.telegram_user_id).where(
            StudentRequest.phone.in_(_phone_candidates(cleaned))
        )
    ).scalars().first()
    if requester:
        return int(requester)
    return None


def _phone_candidates(cleaned: str) -> list[str]:
    """Every spelling worth looking for, for one normalised number.

    An Ethiopian number typed locally starts with ``0`` and is stored with
    ``+251``; people also drop the ``+`` altogether. Rather than guess, all the
    spellings are searched — a failed login costs one indexed query, and a
    success is worth it.
    """
    bare = cleaned.lstrip("+")
    candidates = {cleaned, bare, f"+{bare}"}
    if bare.startswith("0"):
        # 0911… → +251911…
        candidates.add("+251" + bare[1:])
    else:
        candidates.add("0" + bare)
    return sorted(candidates)


def _as_utc(value):
    """A datetime from the database as an aware UTC value.

    SQLite has no timezone type and hands timestamps back naive, so a freshly
    inserted ``expires_at`` compares unequal to ``utcnow()`` even when it is in
    the future. Normalising on read is what makes the expiry check mean
    something.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def purge_expired(session: Session) -> None:
    """Drop codes that can no longer be used.

    Called on every issue so the table does not grow without bound on a service
    nobody cleans.
    """
    now = utcnow()
    for row in session.execute(
        select(WebLoginCode).where(WebLoginCode.expires_at < now)
    ).scalars():
        session.delete(row)


def issue_code(session: Session, phone: str) -> IssuedCode:
    """Create a one-time code for this phone number.

    Raises :class:`LoginError` when the number belongs to nobody — the caller
    turns that into a generic failure so the endpoint never confirms whether a
    number is on file.
    """
    cleaned = normalise_phone(phone)
    telegram_user_id = find_user_by_phone(session, cleaned)
    if telegram_user_id is None:
        raise LoginError("That number is not on file")

    purge_expired(session)

    # An outstanding code for the same person is replaced, not stacked: a
    # second request must not leave two working codes behind.
    now = utcnow()
    for row in session.execute(
        select(WebLoginCode).where(
            WebLoginCode.phone == cleaned, WebLoginCode.consumed_at.is_(None)
        )
    ).scalars():
        session.delete(row)

    code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
    row = WebLoginCode(
        phone=cleaned,
        code_hash=hash_code(code),
        telegram_user_id=telegram_user_id,
        created_at=now,
        expires_at=now + timedelta(seconds=settings.mini_app_code_ttl_seconds),
    )
    session.add(row)
    session.flush()

    logger.info(
        "Mini App login code issued",
        extra={"phone_tail": cleaned[-4:], "expires_at": row.expires_at.isoformat()},
    )
    # The code itself is never logged.
    return IssuedCode(
        delivered=False,
        dev_code=code if settings.mini_app_dev_codes else None,
    )


def redeem_code(session: Session, phone: str, code: str) -> int:
    """Consume the code and return the Telegram user id it belongs to.

    Raises :class:`LoginError` for every failure — unknown number, wrong code,
    expired, already used — so a caller cannot probe which of those it was.
    """
    cleaned = normalise_phone(phone)
    if not cleaned or not str(code or "").strip():
        raise LoginError("Invalid login attempt")

    row = session.execute(
        select(WebLoginCode).where(
            WebLoginCode.phone == cleaned, WebLoginCode.consumed_at.is_(None)
        )
    ).scalars().first()

    if row is None:
        raise LoginError("Invalid login attempt")
    expires_at = _as_utc(row.expires_at)
    if expires_at is None or expires_at < utcnow():
        session.delete(row)
        session.flush()
        raise LoginError("Invalid login attempt")
    if not secrets.compare_digest(row.code_hash, hash_code(str(code).strip())):
        raise LoginError("Invalid login attempt")
    if row.telegram_user_id is None:
        raise LoginError("Invalid login attempt")

    row.consumed_at = utcnow()
    session.flush()
    return int(row.telegram_user_id)


__all__ = [
    "CODE_DIGITS",
    "IssuedCode",
    "LoginError",
    "find_user_by_phone",
    "hash_code",
    "issue_code",
    "normalise_phone",
    "redeem_code",
]