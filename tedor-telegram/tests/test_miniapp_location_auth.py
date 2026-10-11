"""The Mini App's location search is authenticated, but not by the API secret.

``GET /api/locations/search`` spends the geocoding quota, so it is guarded — but
its first-class caller is the Mini App, which authenticates with signed Init
Data rather than the shared secret. Both credentials are accepted, both are
verified, and neither is optional.

Validates: Requirements 4.1, 12.4
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import verify_api_secret, verify_telegram_init_data
from app.api.routes.locations import router
from app.config import settings
from app.database import get_db
from sqlalchemy.orm import Session

BOT_TOKEN = "123456789:TESTTOKENTESTTOKENTESTTOKENTEST"


def signed_init_data(user_id: int, **extra: str) -> str:
    """Build Init Data Telegram would have signed for this user."""
    params = {
        "auth_date": "1700000000",
        "query_id": "AAE-test",
        "user": json.dumps({"id": user_id, "first_name": "Abebe"}),
        **extra,
    }
    check = "\n".join(f"{key}={params[key]}" for key in sorted(params))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(params)


@pytest.fixture
def search_app(db: Session) -> FastAPI:
    app = FastAPI()

    def _fixed_db():
        yield db

    app.dependency_overrides[get_db] = _fixed_db
    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "api_secret", "test-secret-value", raising=False)
    monkeypatch.setattr(settings, "bot_token", BOT_TOKEN, raising=False)
    # No provider key: search falls back to our own records and says so.
    monkeypatch.setattr(settings, "geoapify_enabled", False, raising=False)


def test_a_signed_init_data_header_is_accepted(search_app: FastAPI, db: Session) -> None:
    with TestClient(search_app) as client:
        response = client.get(
            "/api/locations/search?q=Bole",
            headers={"X-Telegram-Init-Data": signed_init_data(555)},
        )

    assert response.status_code == 200, response.text
    assert "suggestions" in response.json()


def test_the_api_secret_still_works(search_app: FastAPI, db: Session) -> None:
    """Accepting a second credential must not break the first."""
    with TestClient(search_app) as client:
        response = client.get(
            "/api/locations/search?q=Bole",
            headers={"X-API-Secret": "test-secret-value"},
        )

    assert response.status_code == 200, response.text


def test_no_credential_is_refused(search_app: FastAPI, db: Session) -> None:
    with TestClient(search_app) as client:
        response = client.get("/api/locations/search?q=Bole")

    assert response.status_code == 401


def test_a_tampered_init_data_is_refused(search_app: FastAPI, db: Session) -> None:
    init_data = signed_init_data(555).replace("auth_date=1700000000", "auth_date=1700000001")

    with TestClient(search_app) as client:
        response = client.get(
            "/api/locations/search?q=Bole",
            headers={"X-Telegram-Init-Data": init_data},
        )

    assert response.status_code == 401


def test_an_empty_init_data_is_refused(search_app: FastAPI, db: Session) -> None:
    with TestClient(search_app) as client:
        response = client.get(
            "/api/locations/search?q=Bole",
            headers={"X-Telegram-Init-Data": ""},
        )

    assert response.status_code == 401


def test_verification_helpers_agree_with_the_endpoint() -> None:
    """The dependency and the endpoint must not accept different things."""
    assert verify_telegram_init_data(signed_init_data(777)) == 777
    assert verify_telegram_init_data("") is None
    assert verify_telegram_init_data("hash=nonsense") is None
    assert verify_api_secret("test-secret-value") is True
    assert verify_api_secret("wrong") is False
    assert verify_api_secret(None) is False