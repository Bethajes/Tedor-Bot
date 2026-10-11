"""TDR identifier properties (Properties 1, 2, 3 and 9).

Feature: tedor-telegram-bot, Property 1: TDR ID uniqueness
Validates: Requirements 3.1, 3.2

Feature: tedor-telegram-bot, Property 2: TDR ID format
Validates: Requirements 3.1

Feature: tedor-telegram-bot, Property 3: TDR ID monotonicity
Validates: Requirements 3.2, 3.3

Feature: tedor-telegram-bot, Property 9: Status transition safety
Validates: Requirements 5.2, 5.3
"""

from __future__ import annotations

import re

from hypothesis import given, settings as hypothesis_settings, strategies as st

from sqlalchemy.orm import Session

from app.enums import TutorStatus
from app.services import tutor_service
from tests.factories import create_tutor, tutor_payload

PROFILE = hypothesis_settings(max_examples=100, deadline=None)

COUNTRY_STRATEGY = st.sampled_from(
    ["Ethiopia", "ethiopia", "ETHIOPIA", "Kenya", "United States", "Germany", "Other"]
)


@given(
    count=st.integers(min_value=2, max_value=6),
    seed=st.integers(min_value=1, max_value=10**6),
)
@PROFILE
def test_property_tdr_id_uniqueness(fresh_db, count: int, seed: int) -> None:
    """Feature: tedor-telegram-bot, Property 1: TDR ID uniqueness.

    Validates: Requirements 3.1, 3.2
    """
    session = fresh_db()
    ids = []
    for offset in range(count):
        telegram_id = -(seed * 100 + offset + 1)
        tutor = create_tutor(session, telegram_id)
        ids.append(tutor.public_tutor_id)
    session.commit()
    assert len(set(ids)) == count, f"TDR IDs repeated: {ids}"


@given(sequence=st.integers(min_value=0, max_value=999_999))
@PROFILE
def test_property_tdr_id_format(sequence: int) -> None:
    """Feature: tedor-telegram-bot, Property 2: TDR ID format.

    Validates: Requirements 3.1
    """
    tdr_id = tutor_service.format_tdr_id(sequence)
    assert re.match(r"^TDR-\d{6}$", tdr_id), tdr_id


@given(
    first=st.integers(min_value=1, max_value=5_000_000),
    second=st.integers(min_value=1, max_value=5_000_000),
)
@PROFILE
def test_property_tdr_id_monotonicity(fresh_db, first: int, second: int) -> None:
    """Feature: tedor-telegram-bot, Property 3: TDR ID monotonicity.

    Validates: Requirements 3.2, 3.3
    """
    session = fresh_db()
    earlier = create_tutor(session, -first - 1)
    later = create_tutor(session, -(second + 10_000_001))
    session.commit()
    assert tutor_service.tdr_sequence(later.public_tutor_id) > tutor_service.tdr_sequence(
        earlier.public_tutor_id
    )


@given(
    status=st.sampled_from([item.value for item in TutorStatus]),
    seed=st.integers(min_value=1, max_value=10**6),
)
@PROFILE
def test_property_status_transition_safety(fresh_db, status: str, seed: int) -> None:
    """Feature: tedor-telegram-bot, Property 9: Status transition safety.

    Only ``status`` and ``updated_at`` may change.
    """
    session = fresh_db()
    tutor = create_tutor(session, -seed - 1)
    session.commit()
    before = _snapshot(tutor, ignore_status=True)
    updated = tutor_service.update_tutor_status(session, tutor.public_tutor_id, status)
    session.commit()
    assert updated is not None
    after = _snapshot(updated, ignore_status=True)
    assert before == after, "status update modified unrelated fields"
    assert updated.status == status


@given(
    country=COUNTRY_STRATEGY,
    seed=st.integers(min_value=1, max_value=10**6),
)
@PROFILE
def test_tdr_id_never_reassigned_after_deletion(fresh_db, country: str, seed: int) -> None:
    """Requirement 3.3: a rejected or deleted tutor never frees their TDR ID."""
    session = fresh_db()
    tutor = create_tutor(session, -seed - 1, country=country)
    session.commit()
    tdr_id = tutor.public_tutor_id
    session.delete(tutor)
    session.commit()
    replacement = create_tutor(session, -seed - 500_000)
    session.commit()
    assert replacement.public_tutor_id != tdr_id
    assert tutor_service.tdr_sequence(replacement.public_tutor_id) > tutor_service.tdr_sequence(
        tdr_id
    )


def _snapshot(tutor, ignore_status: bool = False) -> dict:
    fields = {
        "public_tutor_id": tutor.public_tutor_id,
        "telegram_user_id": tutor.telegram_user_id,
        "name": tutor.name,
        "display_name": tutor.display_name,
        "phone": tutor.phone,
        "email": tutor.email,
        "country": tutor.country,
        "city": tutor.city,
        "bio": tutor.bio,
        "etb_rate": tutor.etb_rate,
        "usd_rate": tutor.usd_rate,
        "experience_years": tutor.experience_years,
        "teaching_mode": tutor.teaching_mode,
        "storage_status": tutor.storage_status,
        "telegram_storage_chat_id": tutor.telegram_storage_chat_id,
        "telegram_storage_message_id": tutor.telegram_storage_message_id,
        "subjects": sorted(tutor.subject_list),
        "levels": sorted(tutor.level_list),
        "languages": sorted(tutor.language_list),
        "created_at": tutor.created_at,
    }
    if not ignore_status:
        fields["status"] = tutor.status
    return fields


def test_tutor_service_validation_rejects_incomplete_payloads(db: Session) -> None:
    session = db
    payload = tutor_payload(-5)
    payload.pop("country")
    try:
        tutor_service.create_tutor(session, payload)
    except tutor_service.TutorValidationError as exc:
        assert "country" in str(exc)
    else:  # pragma: no cover - the call must fail
        raise AssertionError("expected TutorValidationError")


def test_update_status_for_unknown_tutor_returns_none(db: Session) -> None:
    session = db
    assert tutor_service.update_tutor_status(session, "TDR-999999", "VERIFIED") is None


def test_update_status_rejects_unknown_value(db: Session) -> None:
    session = db
    tutor = create_tutor(session, -77)
    session.commit()
    try:
        tutor_service.update_tutor_status(session, tutor.public_tutor_id, "BOGUS")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")