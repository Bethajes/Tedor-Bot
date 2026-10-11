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
from app.services import telegram_client


class FakeMessage:
    """Stands in for a ``telegram.Message`` returned by a send call."""

    def __init__(self, message_id: int, file_id: str, unique_id: str) -> None:
        self.message_id = message_id
        self.file_id = file_id
        self.file_unique_id = unique_id

    # ``storage_service._extract_file_reference`` looks for one of these
    # attributes; a document is enough for every document type the Mini App
    # accepts, and it is the only shape that carries a file_name.
    document = property(lambda self: self)


class FakeBot:
    """A ``Bot`` stand-in that records sends instead of calling Telegram.

    Autouse so no test can reach the network: the REST layer archives uploads
    on the request path, so without this a unit test would try to talk to
    api.telegram.org.
    """

    def __init__(self, chat_id: int = -1001234567890) -> None:
        self.chat_id = chat_id
        self.sent_documents: list[dict[str, Any]] = []
        self.sent_messages: list[dict[str, Any]] = []
        self.fail_next: Exception | None = None
        #: When set, every send raises it — used to exercise the retry budget
        #: running out rather than a single transient blip.
        self.always_fail: Exception | None = None
        self._next_message_id = 100

    def _maybe_fail(self) -> None:
        if self.always_fail is not None:
            raise self.always_fail
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error

    def _message(self, prefix: str) -> FakeMessage:
        self._next_message_id += 1
        return FakeMessage(
            self._next_message_id, f"{prefix}-file-id", f"{prefix}-unique-id"
        )

    async def send_document(self, *, chat_id: int, document: Any, caption: str, filename: str | None = None, **kwargs: Any) -> FakeMessage:
        self._maybe_fail()
        message = self._message("doc")
        self.sent_documents.append(
            {"chat_id": chat_id, "bytes": document, "caption": caption, "filename": filename}
        )
        return message

    async def send_photo(self, *, chat_id: int, photo: Any, caption: str, **kwargs: Any) -> FakeMessage:
        self._maybe_fail()
        message = self._message("photo")
        self.sent_documents.append(
            {"chat_id": chat_id, "bytes": photo, "caption": caption, "filename": None}
        )
        return message

    async def send_voice(self, *, chat_id: int, voice: Any, caption: str, **kwargs: Any) -> FakeMessage:
        self._maybe_fail()
        message = self._message("voice")
        self.sent_documents.append(
            {"chat_id": chat_id, "bytes": voice, "caption": caption, "filename": None}
        )
        return message

    async def send_message(self, *, chat_id: int, text: str, **kwargs: Any) -> FakeMessage:
        self._maybe_fail()
        self._next_message_id += 1
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return FakeMessage(self._next_message_id, "", "")


@pytest.fixture(autouse=True)
def fake_telegram_bot(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeBot]:
    """Point the shared archive bot at :class:`FakeBot` for every test."""
    bot = FakeBot(chat_id=settings.tutor_storage_chat_id or -1001234567890)
    monkeypatch.setattr(telegram_client, "get_bot", lambda: bot)
    # The cache must not leak a real client built by an earlier test.
    telegram_client.reset_bot()
    yield bot
    telegram_client.reset_bot()


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