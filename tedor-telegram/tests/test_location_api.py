"""The location REST endpoints: guards, quota and privacy.

Search and registration are behind the same API secret as the admin endpoints,
because both spend money: search spends the geocoding quota, and registration
writes a row a person has to review. The one public endpoint returns a
location's own fields only, so it cannot be used to discover who works where.
"""

from __future__ import annotations

import pytest

from app.api.routes import locations as routes
from app.config import settings
from app.services import location_registry_service, rate_limit
from app.services.geocoding.base import PlaceSuggestion
from app.services import geocoding


@pytest.fixture(autouse=True)
def _reset():
    rate_limit.reset()
    yield
    rate_limit.reset()


@pytest.fixture
def secret(api_env, monkeypatch) -> str:
    """A known API secret for this test only.

    ``monkeypatch`` rather than an assignment: ``settings`` is a process-wide
    singleton, so writing to it would change the secret for every test that
    runs afterwards.
    """
    monkeypatch.setattr(api_env, "api_secret", "test-secret", raising=False)
    return "test-secret"


@pytest.fixture
def stub_places(monkeypatch):
    places = [
        PlaceSuggestion(
            place_id="g1",
            name="Ayat Tsebel",
            formatted_address="Ayat Tsebel, Addis Ababa, Ethiopia",
            city="Addis Ababa",
            latitude=9.03,
            longitude=38.75,
        )
    ]

    class _Stub:
        name = "STUB"
        is_configured = True

        async def autocomplete(self, text, *, limit=8, country_code="et", proximity=None):
            return list(places)

    monkeypatch.setattr(geocoding, "get_provider", lambda: _Stub())
    return places


# ---------------------------------------------------------------------------
# authorization
# ---------------------------------------------------------------------------


def test_search_requires_the_secret(client):
    response = client.get("/api/locations/search", params={"q": "Ayat"})
    assert response.status_code == 401


def test_search_accepts_the_right_secret(client, secret, stub_places):
    response = client.get(
        "/api/locations/search",
        params={"q": "Ayat"},
        headers={"X-API-Secret": secret},
    )
    assert response.status_code == 200


def test_a_blank_search_is_refused(client, secret):
    response = client.get(
        "/api/locations/search",
        params={"q": "  "},
        headers={"X-API-Secret": secret},
    )
    assert response.status_code == 400


def test_registering_requires_the_secret(client):
    response = client.post("/api/locations", json={"name": "Woreda 12"})
    assert response.status_code == 401


def test_registering_accepts_the_right_secret(client, secret):
    response = client.post(
        "/api/locations",
        json={"name": "Woreda 12", "city": "Addis Ababa"},
        headers={"X-API-Secret": secret},
    )
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# quota
# ---------------------------------------------------------------------------


def test_search_is_rate_limited(client, secret, monkeypatch):
    monkeypatch.setattr(settings, "location_api_rate_limit", 2)
    monkeypatch.setattr(settings, "location_api_rate_window", 60)
    seen = []
    for _ in range(4):
        response = client.get(
            "/api/locations/search",
            params={"q": "Ayat Tzbel"},
            headers={"X-API-Secret": secret},
        )
        seen.append(response.status_code)
    assert 429 in seen
    assert seen.count(200) == 2


def test_a_limited_search_says_how_long_to_wait(client, secret, monkeypatch):
    monkeypatch.setattr(settings, "location_api_rate_limit", 1)
    client.get(
        "/api/locations/search",
        params={"q": "Ayat Tzbel"},
        headers={"X-API-Secret": secret},
    )
    response = client.get(
        "/api/locations/search",
        params={"q": "Ayat Tzbel"},
        headers={"X-API-Secret": secret},
    )
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert "try again" in response.json()["detail"].lower()


def test_registration_is_limited_more_strictly_than_search(client, secret, monkeypatch):
    """Registration writes a row somebody has to review, so it is slower."""
    monkeypatch.setattr(settings, "location_manual_rate_limit", 2)
    monkeypatch.setattr(settings, "location_manual_rate_window", 3600)
    seen = []
    for index in range(4):
        response = client.post(
            "/api/locations",
            json={"name": f"Place {index}"},
            headers={"X-API-Secret": secret},
        )
        seen.append(response.status_code)
    assert seen.count(201) == 2
    assert 429 in seen


# ---------------------------------------------------------------------------
# search behaviour
# ---------------------------------------------------------------------------


def test_a_search_returns_the_providers_suggestions(client, secret, stub_places):
    response = client.get(
        "/api/locations/search",
        params={"q": "Ayat"},
        headers={"X-API-Secret": secret},
    )
    body = response.json()
    assert body["suggestions"][0]["name"] == "Ayat Tsebel"
    assert body["suggestions"][0]["formattedAddress"].startswith("Ayat Tsebel")
    assert body["suggestions"][0]["latitude"] == 9.03


def test_a_search_also_reports_a_known_location(client, secret, db):
    location_registry_service.seed_registry(db)
    db.commit()
    response = client.get(
        "/api/locations/search",
        params={"q": "Bole"},
        headers={"X-API-Secret": secret},
    )
    body = response.json()
    assert body["known"]["name"] == "Bole"
    assert body["known"]["verificationStatus"] == "VERIFIED"


def test_a_failed_provider_still_answers(client, secret, monkeypatch):
    """A provider outage costs the suggestions, not the whole request."""
    from app.services.geocoding.base import GeocodingError

    class _Broken:
        name = "STUB"
        is_configured = True

        async def autocomplete(self, *args, **kwargs):
            raise GeocodingError()

    monkeypatch.setattr(
        "app.services.geocoding.get_provider", lambda: _Broken()
    )
    response = client.get(
        "/api/locations/search",
        params={"q": "Ayat"},
        headers={"X-API-Secret": secret},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["suggestions"] == []
    assert body["error"]


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def test_registering_creates_a_pending_location(client, secret):
    response = client.post(
        "/api/locations",
        json={
            "name": "Woreda 12",
            "city": "Addis Ababa",
            "subCity": "Woreda 12",
            "landmark": "near the local market",
        },
        headers={"X-API-Secret": secret},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["duplicate"] is False
    assert body["verificationStatus"] == "PENDING"
    assert body["latitude"] is None
    assert body["landmark"] == "near the local market"


def test_registering_the_same_place_twice_returns_one_row(client, secret):
    for _ in range(2):
        response = client.post(
            "/api/locations",
            json={"name": "Goro Sefer"},
            headers={"X-API-Secret": secret},
        )
    assert response.json()["duplicate"] is True


def test_registering_a_location_outside_ethiopia_is_refused(client, secret):
    response = client.post(
        "/api/locations",
        json={"name": "London", "city": "London"},
        headers={"X-API-Secret": secret},
    )
    assert response.status_code == 400


def test_registering_without_a_name_is_refused(client, secret):
    response = client.post(
        "/api/locations", json={"city": "Addis Ababa"}, headers={"X-API-Secret": secret}
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# privacy
# ---------------------------------------------------------------------------


def test_the_public_endpoint_needs_no_secret(client, db):
    location_registry_service.seed_registry(db)
    location_registry_service.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    )
    db.commit()
    found = location_registry_service.resolve_by_text(db, "Goro Sefer")

    response = client.get(f"/api/locations/{found.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Goro Sefer"


def test_the_public_endpoint_exposes_no_tutor_data(client, db):
    """Nothing on the response may identify who works where."""
    from app.models import Tutor, TutorLocation

    location_registry_service.seed_registry(db)
    location = location_registry_service.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    db.add(
        Tutor(
            public_tutor_id="TDR-000001",
            telegram_user_id=-1111,
            name="Abebe Bekele",
            display_name="Abebe",
            phone="+251911234567",
            email="abebe@example.com",
            country="Ethiopia",
            city="Addis Ababa",
            teaching_mode="IN_PERSON",
        )
    )
    db.flush()
    tutor = db.scalars(
        __import__("sqlalchemy").select(Tutor).where(Tutor.telegram_user_id == -1111)
    ).first()
    db.add(
        TutorLocation(
            tutor_id=tutor.id,
            location=location.name,
            normalized_location=location.normalized_name,
            location_id=location.id,
        )
    )
    db.commit()

    response = client.get(f"/api/locations/{location.id}")
    body = response.json()
    for value in (body["name"], body["city"], body.get("subCity")):
        assert value is None or value
    assert "tutor" not in response.text.lower()
    assert "abebe" not in response.text.lower()
    assert "+251" not in response.text


def test_an_unknown_id_is_a_404(client):
    response = client.get("/api/locations/999999")
    assert response.status_code == 404
