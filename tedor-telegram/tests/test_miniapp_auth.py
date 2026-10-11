"""Property-based tests for Mini App Init Data authentication.

Covers:
  Property 1:  Init Data HMAC validation          (Requirements 2.2, 2.3)
  Property 2:  Init Data user ID extraction       (Requirements 2.4)
  Property 13: Mini App endpoints return 401      (Requirements 9.10, 2.3)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from app.api.miniapp.auth import (
    AuthError,
    TelegramUser,
    get_current_telegram_user,
    validate_init_data,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_BOT_TOKEN = "123456789:TESTTOKENTESTTOKENTESTTOKENTEST"


def _sign_init_data(params: dict[str, str], bot_token: str = TEST_BOT_TOKEN) -> str:
    """Build a correctly-signed initData string from a dict of params."""
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(params.items())
    )
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode(),
        digestmod=hashlib.sha256,
    ).digest()
    computed_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    params_with_hash = {**params, "hash": computed_hash}
    return urllib.parse.urlencode(params_with_hash)


def _make_init_data(user_id: int, extra: dict | None = None) -> str:
    """Build a valid signed initData embedding the given user_id."""
    user = {"id": user_id, "first_name": "Test", "is_bot": False}
    if extra:
        user.update(extra)
    params = {"user": json.dumps(user, separators=(",", ":"))}
    return _sign_init_data(params)


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

_user_id = st.integers(min_value=100_000, max_value=2_147_483_647)

_user_dict = st.fixed_dictionaries(
    {"id": _user_id},
    optional={
        "first_name": st.text(max_size=64),
        "last_name": st.text(max_size=64),
        "username": st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="_"), max_size=32),
    },
)


# ---------------------------------------------------------------------------
# Property 1: Init Data HMAC validation
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 1: Init Data HMAC validation
@given(user=_user_dict)
@hyp_settings(max_examples=100, deadline=None)
def test_property_hmac_valid_signature_passes(user: dict) -> None:
    """For any user dict signed with the real bot token, validate_init_data succeeds.

    Validates: Requirements 2.2, 2.3
    """
    params = {"user": json.dumps(user, separators=(",", ":"))}
    init_data = _sign_init_data(params, TEST_BOT_TOKEN)

    result = validate_init_data(init_data, TEST_BOT_TOKEN)
    assert result["id"] == user["id"]


# Feature: tedor-mini-app, Property 1: Init Data HMAC validation
@given(user=_user_dict, garbage=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=64))
@hyp_settings(max_examples=100, deadline=None)
def test_property_hmac_tampered_hash_raises(user: dict, garbage: str) -> None:
    """For any initData where the hash is replaced with a different value, AuthError is raised.

    Validates: Requirements 2.2, 2.3
    """
    params = {"user": json.dumps(user, separators=(",", ":"))}
    valid_data = _sign_init_data(params, TEST_BOT_TOKEN)

    # Replace the hash with garbage
    parsed = dict(urllib.parse.parse_qsl(valid_data, keep_blank_values=True))
    original_hash = parsed["hash"]

    # Only test when garbage actually differs from the real hash
    if garbage == original_hash:
        return

    parsed["hash"] = garbage
    tampered_data = urllib.parse.urlencode(parsed)

    with pytest.raises(AuthError):
        validate_init_data(tampered_data, TEST_BOT_TOKEN)


# ---------------------------------------------------------------------------
# Property 2: Init Data user ID extraction round-trip
# ---------------------------------------------------------------------------

# Feature: tedor-mini-app, Property 2: Init Data user ID extraction round-trip
@given(user_id=_user_id)
@hyp_settings(max_examples=100, deadline=None)
def test_property_user_id_round_trip(user_id: int) -> None:
    """For any validly-constructed initData, validate_init_data extracts the original user ID.

    Validates: Requirements 2.4
    """
    init_data = _make_init_data(user_id)
    result = validate_init_data(init_data, TEST_BOT_TOKEN)
    assert result["id"] == user_id


# ---------------------------------------------------------------------------
# Property 13: Mini App endpoint returns 401 without Init Data
# ---------------------------------------------------------------------------

def _make_test_app() -> FastAPI:
    """Build a minimal FastAPI app with a protected route for testing."""
    from fastapi import Depends

    app = FastAPI()

    @app.get("/api/miniapp/test-protected")
    def _protected(user: TelegramUser = Depends(get_current_telegram_user)):
        return {"user_id": user.id}

    return app


@pytest.fixture(scope="module")
def miniapp_client(api_env) -> TestClient:
    """TestClient wired to a minimal protected app."""
    app = _make_test_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_property_missing_init_data_returns_401(miniapp_client: TestClient) -> None:
    """A request with no X-Telegram-Init-Data header returns HTTP 401.

    Validates: Requirements 9.10, 2.3
    """
    response = miniapp_client.get("/api/miniapp/test-protected")
    assert response.status_code == 401


# Feature: tedor-mini-app, Property 13: Mini App endpoint returns 401 without Init Data
@given(garbage=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=200))
@hyp_settings(max_examples=100, deadline=None)
def test_property_tampered_init_data_returns_401(
    miniapp_client: TestClient,
    garbage: str,
) -> None:
    """For any tampered or random X-Telegram-Init-Data value, the endpoint returns HTTP 401.

    Validates: Requirements 9.10, 2.3
    """
    response = miniapp_client.get(
        "/api/miniapp/test-protected",
        headers={"X-Telegram-Init-Data": garbage},
    )
    assert response.status_code == 401


def test_property_valid_init_data_returns_200(miniapp_client: TestClient, api_env) -> None:
    """A valid signed initData passes authentication and returns 200.

    Validates: Requirements 2.2, 2.4
    """
    init_data = _make_init_data(12345678)
    response = miniapp_client.get(
        "/api/miniapp/test-protected",
        headers={"X-Telegram-Init-Data": init_data},
    )
    assert response.status_code == 200
    assert response.json()["user_id"] == 12345678
