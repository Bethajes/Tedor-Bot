"""Shared pytest fixtures: an isolated in-memory SQLite database."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.database import (
    Base,
    configure_database,
    get_engine,
    get_session_factory,
    reset_database,
)


def clean_tables(session: Session | None = None) -> None:
    """Remove every row so a test starts from an empty schema."""
    if session is not None:
        session.rollback()
        session.expunge_all()
    engine = get_engine()
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    """Configure the whole test session against ``sqlite://`` (in memory)."""
    configure_database("sqlite://")
    Base.metadata.create_all(bind=get_engine())
    yield
    reset_database()


@pytest.fixture(scope="session")
def db(_database: None) -> Iterator[Session]:
    """Long lived session shared by every test (safe for hypothesis)."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _clean_tables(db: Session, _database: None) -> Iterator[None]:
    """Truncate every table after each test so cases stay independent."""
    yield
    clean_tables(db)


@pytest.fixture(scope="session")
def fresh_db(db: Session) -> Callable[[], Session]:
    """Return a helper that empties the tables and returns the session.

    Property tests call this at the start of every generated example.
    """

    def _fresh() -> Session:
        clean_tables(db)
        return db

    return _fresh


@pytest.fixture
def unique_telegram_id() -> Iterator[Callable[[], int]]:
    """Return a callable producing unique (negative) Telegram IDs."""
    counter = itertools.count(1)

    def _next() -> int:
        return -next(counter)

    yield _next


@pytest.fixture
def api_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Deterministic API/admin configuration for the tests."""
    monkeypatch.setattr(settings, "api_secret", "test-secret-value", raising=False)
    monkeypatch.setattr(settings, "admin_user_ids", [111, 222], raising=False)
    monkeypatch.setattr(settings, "tutor_storage_chat_id", -100999, raising=False)
    monkeypatch.setattr(settings, "student_request_chat_id", -100998, raising=False)
    monkeypatch.setattr(settings, "support_chat_id", -100997, raising=False)
    return settings


@pytest.fixture(scope="session")
def api_env(_database: None) -> Any:
    """Session wide API/admin configuration (usable from property tests)."""
    original = (
        settings.api_secret,
        list(settings.admin_user_ids),
        settings.tutor_storage_chat_id,
        settings.student_request_chat_id,
        settings.support_chat_id,
        settings.bot_token,
    )
    settings.api_secret = "test-secret-value"
    settings.admin_user_ids = [111, 222]
    settings.tutor_storage_chat_id = -100999
    settings.student_request_chat_id = -100998
    settings.support_chat_id = -100997
    settings.bot_token = "123456789:TESTTOKENTESTTOKENTESTTOKENTEST"
    yield settings
    (
        settings.api_secret,
        settings.admin_user_ids,
        settings.tutor_storage_chat_id,
        settings.student_request_chat_id,
        settings.support_chat_id,
        settings.bot_token,
    ) = original


@pytest.fixture(scope="session")
def client(api_env: Any) -> Iterator[Any]:
    """A ``TestClient`` bound to the in-memory database."""
    from fastapi.testclient import TestClient

    from app.api.server import create_app

    with TestClient(create_app()) as test_client:
        yield test_client