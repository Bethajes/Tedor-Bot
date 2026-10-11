"""Draft persistence for the Tedor Mini App.

Stores and retrieves partially-completed tutor applications and parent requests.
All payload data is serialised as JSON in the ``drafts.payload`` column.
"""

from __future__ import annotations

import json
from datetime import timezone

from sqlalchemy.orm import Session

from app.models import Draft, utcnow


def save_draft(
    session: Session,
    telegram_user_id: int,
    draft_type: str,
    payload: dict,
) -> Draft:
    """Upsert a draft for ``(telegram_user_id, draft_type)``.

    If a draft already exists it is updated in-place; otherwise a new row is
    created. The caller is responsible for committing the session.
    """
    draft = (
        session.query(Draft)
        .filter_by(telegram_user_id=telegram_user_id, draft_type=draft_type)
        .first()
    )
    now = utcnow()
    if draft is None:
        draft = Draft(
            telegram_user_id=telegram_user_id,
            draft_type=draft_type,
            payload=json.dumps(payload),
            created_at=now,
            updated_at=now,
        )
        session.add(draft)
    else:
        draft.payload = json.dumps(payload)
        draft.updated_at = now
    session.flush()
    return draft


def get_draft(
    session: Session,
    telegram_user_id: int,
    draft_type: str,
) -> Draft | None:
    """Return the draft for ``(telegram_user_id, draft_type)``, or ``None``."""
    return (
        session.query(Draft)
        .filter_by(telegram_user_id=telegram_user_id, draft_type=draft_type)
        .first()
    )


def get_draft_payload(
    session: Session,
    telegram_user_id: int,
    draft_type: str,
) -> dict | None:
    """Return the decoded payload dict, or ``None`` if no draft exists."""
    draft = get_draft(session, telegram_user_id, draft_type)
    if draft is None:
        return None
    return json.loads(draft.payload)


def delete_draft(
    session: Session,
    telegram_user_id: int,
    draft_type: str,
) -> None:
    """Delete the draft for ``(telegram_user_id, draft_type)`` if it exists."""
    draft = get_draft(session, telegram_user_id, draft_type)
    if draft is not None:
        session.delete(draft)
        session.flush()
