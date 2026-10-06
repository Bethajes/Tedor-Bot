"""Logging and error-handling requirements (Requirement 13.1 – 13.5)."""

from __future__ import annotations

import logging

from hypothesis import given, settings as hypothesis_settings, strategies as st

from app.logging_config import REDACTED, RedactFilter, configure_logging, get_logger
from app.services import tutor_service

PROFILE = hypothesis_settings(max_examples=100, deadline=None)


def _rendered(message: str, **extra) -> str:
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=message, args=None, exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    RedactFilter().filter(record)
    return record.getMessage()


SAFE_EMAILS = st.builds(
    lambda local, domain: f"{local}@{domain}",
    st.text(alphabet="abcdefghij", min_size=3, max_size=10),
    st.sampled_from(["example.com", "mail.org", "tedortutors.com"]),
)


@given(email=SAFE_EMAILS, token=st.from_regex(r"\d{8,10}:[A-Za-z0-9_\-]{30,}", fullmatch=True))
@PROFILE
def test_property_logging_never_leaks_pii_or_secrets(email: str, token: str) -> None:
    """Requirement 13.3: no tokens, emails, phone numbers or user IDs in logs."""
    output = _rendered("contact=" + email + " token=" + token)
    assert email not in output
    assert token not in output


def test_phone_numbers_are_redacted() -> None:
    output = _rendered("calling +251911234567 now")
    assert "+251911234567" not in output
    assert REDACTED in output


def test_extra_fields_are_redacted() -> None:
    output = _rendered(
        "admin action", telegram_user_id=123456789, phone="+251911", api_secret="shh"
    )
    assert "123456789" not in output
    assert "shh" not in output


def test_configure_logging_is_idempotent(caplog) -> None:
    configure_logging("INFO")
    configure_logging("INFO")
    logger = get_logger("app.test")
    logger.info("hello", extra={"tdr_id": "TDR-000001"})
    messages = [record.getMessage() for record in caplog.records]
    assert any("hello" in message for message in messages)


def test_info_events_for_business_actions(fresh_db, caplog) -> None:
    """Requirements 13.1 and 13.2: the key events are logged."""
    from tests.factories import create_tutor, student_request_payload
    from app.services import student_service

    session = fresh_db()
    with caplog.at_level(logging.INFO):
        tutor = create_tutor(session, -7001)
        session.commit()
        tutor_service.update_tutor_status(session, tutor.public_tutor_id, "VERIFIED")
        session.commit()
        student_service.create_student_request(session, student_request_payload(-7002))
        session.commit()

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Tutor application submitted" in text
    assert "Student request created" in text
    assert "Tutor status changed to VERIFIED" in text


def test_error_event_for_storage_failure(fresh_db, caplog) -> None:
    """Requirement 13.2: Telegram storage failures are logged at ERROR."""
    import asyncio

    from app.services.storage_service import TelegramStorageService
    from tests.factories import create_tutor
    from tests.test_storage import FakeBot

    session = fresh_db()
    tutor = create_tutor(session, -7100)
    session.commit()

    with caplog.at_level(logging.ERROR):
        service = TelegramStorageService(
            FakeBot(fail_times=99),
            chat_id=-6001,
            max_attempts=1,
            backoff_seconds=0,
        )
        asyncio.run(service.store_tutor_record(tutor.public_tutor_id, []))

    assert any(
        "storage" in record.getMessage().lower() and record.levelno == logging.ERROR
        for record in caplog.records
    )


def test_api_auth_failure_is_logged_as_warning(api_env, caplog) -> None:
    """Requirement 13.2: API authentication failures are logged."""
    import pytest
    from fastapi import HTTPException

    from app.api.auth import require_api_secret, verify_api_secret

    assert verify_api_secret("wrong-secret") is False
    assert verify_api_secret("test-secret-value") is True

    with caplog.at_level(logging.WARNING):
        with pytest.raises(HTTPException) as excinfo:
            require_api_secret("wrong-secret")
    assert excinfo.value.status_code == 401
    assert any("secret" in record.getMessage().lower() for record in caplog.records)
    assert "wrong-secret" not in caplog.text