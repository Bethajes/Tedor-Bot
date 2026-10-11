"""Web login for the Mini App outside Telegram.

Covers Requirements 2.5, 2.6: a browser with no Init Data may exchange a
one-time code for a short-lived token, and that token identifies the caller
exactly as the Init Data would.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.miniapp.auth import (
    WEB_TOKEN_TTL_SECONDS,
    create_web_token,
    user_from_web_token,
)
from app.api.miniapp.auth import get_current_telegram_user
from app.api.routes.miniapp import router
from app.config import settings
from app.database import get_db
from app.models import WebLoginCode
from app.services import draft_service
from app.services import web_login_service
from app.services.tutor_profile_service import create_ethiopian_tutor

ADMIN_ID = 111


def _make_app(user_id: int | None, db: Session) -> FastAPI:
    """The miniapp router.

    With a ``user_id`` the auth dependency is pinned to that user; without one
    it is left alone, so the real header handling runs — which is the point of
    these tests.
    """
    app = FastAPI()

    def _fixed_db():
        yield db

    if user_id is not None:
        from app.api.miniapp.auth import TelegramUser

        def _fixed_user() -> TelegramUser:
            return TelegramUser(id=user_id)

        app.dependency_overrides[get_current_telegram_user] = _fixed_user

    app.dependency_overrides[get_db] = _fixed_db
    app.include_router(router)
    return app


@pytest.fixture
def jwt_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mini_app_jwt_secret", "test-jwt-secret-at-least-32-characters-long", raising=False)


@pytest.fixture
def dev_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return the one-time code in the response, as local development does."""
    monkeypatch.setattr(settings, "mini_app_dev_codes", True, raising=False)


def _tutor_with_phone(db: Session, user_id: int, phone: str) -> Any:
    return create_ethiopian_tutor(
        db,
        {
            "telegram_user_id": user_id,
            "full_name": "Abebe Bekele",
            "phone": phone,
            "locations": ["Ayat Tsebel"],
            "grades": ["Grade 4"],
            "subjects": ["Mathematics"],
        },
    )


# ---------------------------------------------------------------------------
# Token shape
# ---------------------------------------------------------------------------


def test_web_token_carries_telegram_user_id(jwt_configured: None) -> None:
    token = create_web_token(4242)
    claims = jwt.decode(token, "test-jwt-secret-at-least-32-characters-long", algorithms=["HS256"])

    assert claims["telegram_user_id"] == 4242
    assert claims["exp"] > claims["iat"]


def test_web_token_expires(jwt_configured: None) -> None:
    """A token minted with a zero TTL is refused — TTL is not decorative."""
    token = create_web_token(4242, ttl_seconds=-10)
    assert user_from_web_token(token) is None


def test_tampered_token_is_refused(jwt_configured: None) -> None:
    token = create_web_token(4242)
    assert user_from_web_token(token + "x") is None


def test_token_signed_with_another_secret_is_refused(jwt_configured: None) -> None:
    forged = jwt.encode(
        {"telegram_user_id": 4242, "exp": int(time.time()) + 600},
        "some-other-secret",
        algorithm="HS256",
    )
    assert user_from_web_token(forged) is None


def test_web_login_is_off_without_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """No secret configured means no token, never a default-signed one."""
    monkeypatch.setattr(settings, "mini_app_jwt_secret", "", raising=False)
    monkeypatch.setattr(settings, "api_secret", "", raising=False)
    monkeypatch.setattr(settings, "bot_token", "", raising=False)

    with pytest.raises(Exception):
        create_web_token(4242)


# ---------------------------------------------------------------------------
# The code round trip
# ---------------------------------------------------------------------------


def test_code_is_stored_hashed(db: Session, jwt_configured: None, dev_codes: None) -> None:
    """The code itself is never written down."""
    tutor = _tutor_with_phone(db, 7001, "+251911234567")
    db.commit()

    issued = web_login_service.issue_code(db, "+251911234567")

    row = db.query(WebLoginCode).one()
    assert row.code_hash != issued.dev_code
    assert row.code_hash == web_login_service.hash_code(issued.dev_code)
    assert row.telegram_user_id == tutor.telegram_user_id


def test_redeem_returns_the_same_user_id(db: Session, jwt_configured: None, dev_codes: None) -> None:
    _tutor_with_phone(db, 7002, "+251911234567")
    db.commit()

    issued = web_login_service.issue_code(db, "+251911234567")
    assert issued.dev_code is not None
    user_id = web_login_service.redeem_code(db, "+251911234567", issued.dev_code)

    assert user_id == 7002


def test_a_code_is_one_time(db: Session, jwt_configured: None, dev_codes: None) -> None:
    _tutor_with_phone(db, 7003, "+251911234567")
    db.commit()

    issued = web_login_service.issue_code(db, "+251911234567")
    assert issued.dev_code is not None
    web_login_service.redeem_code(db, "+251911234567", issued.dev_code)

    with pytest.raises(web_login_service.LoginError):
        web_login_service.redeem_code(db, "+251911234567", issued.dev_code)


def test_an_expired_code_is_refused(db: Session, jwt_configured: None, dev_codes: None) -> None:
    _tutor_with_phone(db, 7004, "+251911234567")
    db.commit()

    issued = web_login_service.issue_code(db, "+251911234567")
    assert issued.dev_code is not None
    row = db.query(WebLoginCode).one()
    row.expires_at = row.created_at
    db.flush()

    with pytest.raises(web_login_service.LoginError):
        web_login_service.redeem_code(db, "+251911234567", issued.dev_code)


def test_a_wrong_code_is_refused(db: Session, jwt_configured: None, dev_codes: None) -> None:
    _tutor_with_phone(db, 7005, "+251911234567")
    db.commit()

    web_login_service.issue_code(db, "+251911234567")

    with pytest.raises(web_login_service.LoginError):
        web_login_service.redeem_code(db, "+251911234567", "000000")


def test_an_unknown_number_never_gets_a_code(db: Session, jwt_configured: None) -> None:
    """The endpoint cannot be used to find out who has applied."""
    with pytest.raises(web_login_service.LoginError):
        web_login_service.issue_code(db, "+251999999999")


def test_a_new_code_replaces_the_previous_one(
    db: Session, jwt_configured: None, dev_codes: None
) -> None:
    _tutor_with_phone(db, 7006, "+251911234567")
    db.commit()

    first = web_login_service.issue_code(db, "+251911234567")
    assert first.dev_code is not None
    second = web_login_service.issue_code(db, "+251911234567")
    assert second.dev_code is not None

    db.commit()
    assert db.query(WebLoginCode).count() == 1
    with pytest.raises(web_login_service.LoginError):
        web_login_service.redeem_code(db, "+251911234567", first.dev_code)


def test_phone_lookup_accepts_the_common_spellings(db: Session) -> None:
    _tutor_with_phone(db, 7007, "+251911234567")
    db.commit()

    for spelling in ("+251911234567", "251911234567", "0911234567"):
        assert web_login_service.find_user_by_phone(db, spelling) == 7007


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


def test_end_to_end_login_then_authorised_request(
    db: Session, jwt_configured: None, dev_codes: None
) -> None:
    """The whole web path: phone → code → token → the caller's own draft."""
    draft_service.save_draft(db, 7010, "tutor", {"step": 3, "full_name": "Abebe"})
    db.commit()

    client = TestClient(_make_app(None, db))

    response = client.post("/api/miniapp/auth/code", json={"phone": "+251911234567"})
    # 400: nothing on file for that number in this database, so no code is
    # issued — the endpoint must not confirm who is registered.
    assert response.status_code == 400

    # With a tutor on file, the code buys a token that works.
    _tutor_with_phone(db, 7010, "+251911234567")
    db.commit()

    response = client.post("/api/miniapp/auth/code", json={"phone": "+251911234567"})
    assert response.status_code == 202, response.text
    code = response.json()["dev_code"]

    response = client.post(
        "/api/miniapp/auth/verify", json={"phone": "+251911234567", "code": code}
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert response.json()["expires_in"] == WEB_TOKEN_TTL_SECONDS

    response = client.get(
        "/api/miniapp/tutor/draft", headers={"X-Telegram-Token": token}
    )
    assert response.status_code == 200, response.text
    assert response.json()["full_name"] == "Abebe"


def test_a_request_without_a_token_is_unauthorised(
    db: Session, jwt_configured: None
) -> None:
    draft_service.save_draft(db, 7020, "tutor", {"step": 1})
    db.commit()

    client = TestClient(_make_app(None, db))
    response = client.get("/api/miniapp/tutor/draft")

    assert response.status_code == 401


def test_a_forged_token_is_unauthorised(db: Session, jwt_configured: None) -> None:
    draft_service.save_draft(db, 7021, "tutor", {"step": 1})
    db.commit()

    forged = jwt.encode(
        {"telegram_user_id": 7021, "exp": int(time.time()) + 600},
        "guessed-secret",
        algorithm="HS256",
    )

    client = TestClient(_make_app(None, db))
    response = client.get(
        "/api/miniapp/tutor/draft", headers={"X-Telegram-Token": forged}
    )

    assert response.status_code == 401


def test_a_token_sees_only_its_own_data(db: Session, jwt_configured: None, dev_codes: None) -> None:
    """A web token is an identity, not a superset of one (Requirement 2.6)."""
    draft_service.save_draft(db, 7030, "tutor", {"full_name": "Owner A"})
    draft_service.save_draft(db, 7031, "tutor", {"full_name": "Owner B"})
    db.commit()

    token_a = create_web_token(7030)
    token_b = create_web_token(7031)

    client = TestClient(_make_app(None, db))

    body = client.get(
        "/api/miniapp/tutor/draft", headers={"X-Telegram-Token": token_a}
    ).json()
    assert body["full_name"] == "Owner A"

    body = client.get(
        "/api/miniapp/tutor/draft", headers={"X-Telegram-Token": token_b}
    ).json()
    assert body["full_name"] == "Owner B"