"""The geocoding provider layer.

Every test here runs against an injected transport, so no API key and no
network are needed. The cases that matter are the failure ones: a provider that
errors, times out, rate-limits, or answers with something unreadable must all
produce a user-safe message and must never leak the key.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services import geocoding, location_registry_service
from app.services.geocoding import base as geocoding_base
from app.services.geocoding.geoapify import (
    ADDIS_ABABA,
    GEOAPIFY_ATTRIBUTION,
    GeoapifyProvider,
)
from app.services.geocoding.base import (
    GeocodingError,
    PlaceSuggestion,
    GeocodingNotConfigured,
    GeocodingProviderError,
    GeocodingRateLimited,
    GeocodingTimeout,
    clean_query,
)


def _feature(
    place_id: str = "p1",
    name: str = "Ayat Tsebel",
    formatted: str = "Ayat Tsebel, Addis Ababa, Ethiopia",
    city: str = "Addis Ababa",
    lat: float | None = 9.03,
    lon: float | None = 38.75,
    district: str = "",
) -> dict:
    properties = {
        "place_id": place_id,
        "name": name,
        "formatted": formatted,
        "city": city,
        "country_code": "et",
        "lat": lat,
        "lon": lon,
    }
    if district:
        properties["district"] = district
    return {"properties": properties}


def _transport(payload, *, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


def _provider(payload, *, status_code: int = 200, key: str = "a" * 32) -> GeoapifyProvider:
    """A provider wired to a stub transport, so nothing leaves the process."""
    client = httpx.AsyncClient(transport=_transport(payload, status_code=status_code))
    return GeoapifyProvider(
        api_key=key, client=client, cache=None, proximity=None
    )


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def test_no_api_key_means_no_search():
    provider = GeoapifyProvider(api_key="", client=None)
    assert provider.is_configured is False


def test_a_short_key_is_treated_as_unset():
    provider = GeoapifyProvider(api_key="nope", client=None)
    assert provider.is_configured is False


def test_disabling_search_is_respected():
    provider = GeoapifyProvider(api_key="a" * 32, enabled=False)
    assert provider.is_configured is False


@pytest.mark.asyncio
async def test_an_unconfigured_provider_never_contacts_the_provider():
    provider = GeoapifyProvider(api_key="", client=None)
    with pytest.raises(GeocodingNotConfigured):
        await provider.autocomplete("Ayat")


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_successful_search_is_returned_with_its_address():
    provider = _provider({"features": [_feature()]})
    places = await provider.autocomplete("Ayat", country_code="et")
    assert [place.name for place in places] == ["Ayat Tsebel"]
    assert places[0].formatted_address == "Ayat Tsebel, Addis Ababa, Ethiopia"
    assert places[0].place_id == "p1"
    assert places[0].has_coordinates


@pytest.mark.asyncio
async def test_an_empty_answer_is_not_an_error():
    """"We know nothing about that place" is a result, not a failure."""
    provider = _provider({"features": []})
    assert await provider.autocomplete("Nowhere") == []


@pytest.mark.asyncio
async def test_the_request_restricts_to_one_country_and_biases_addis():
    """Ethiopia only, Addis first — never an unfiltered geocoder."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        for key, value in request.url.params.items():
            seen[key] = value
        return httpx.Response(200, json={"features": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeoapifyProvider(
        api_key="a" * 32, client=client, proximity=ADDIS_ABABA
    )
    await provider.autocomplete("Bole", country_code="et")

    assert seen["filter"] == "countrycode:et"
    assert seen["bias"] == "proximity:9.0309,38.7461"
    assert seen["format"] == "json"


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------


def test_the_named_city_is_not_repeated_from_every_row():
    provider = _provider({"features": [_feature(city="Addis Ababa")]})
    place = provider._parse_feature(_feature(city="Addis Ababa"), "et")
    assert place.city == "Addis Ababa"


def test_a_village_component_becomes_the_city():
    place = GeoapifyProvider()._parse_feature(
        {
            "properties": {
                "place_id": "x",
                "name": "Gerji",
                "formatted": "Gerji, Addis Ababa",
                "village": "Gerji",
                "lat": None,
                "lon": None,
            }
        },
        "et",
    )
    assert place.city == "Gerji"


@pytest.mark.asyncio
async def test_a_zero_position_is_not_a_location():
    """"No position" is the provider's way of saying "we do not know where"."""
    place = GeoapifyProvider()._parse_feature(
        _feature(lat=0, lon=0), "et"
    )
    assert place.latitude is None
    assert place.longitude is None


@pytest.mark.asyncio
async def test_one_malformed_feature_does_not_fail_the_whole_search():
    payload = {"features": [{"properties": {"no_id": True}}, _feature()]}
    places = await _provider(payload).autocomplete("Ayat")
    assert [place.place_id for place in places] == ["p1"]


# ---------------------------------------------------------------------------
# failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rate_limit_raises_and_says_something_safe():
    provider = _provider({}, status_code=429)
    with pytest.raises(GeocodingRateLimited) as exc:
        await provider.autocomplete("Ayat")
    assert "third party" not in exc.value.user_message.lower()


@pytest.mark.asyncio
async def test_a_timeout_is_its_own_failure():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeoapifyProvider(api_key="a" * 32, client=client)
    with pytest.raises(GeocodingTimeout):
        await provider.autocomplete("Ayat")


@pytest.mark.asyncio
async def test_a_server_error_is_a_provider_error():
    provider = _provider({}, status_code=503)
    with pytest.raises(GeocodingProviderError):
        await provider.autocomplete("Ayat")


@pytest.mark.asyncio
async def test_unreadable_json_is_a_provider_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeoapifyProvider(api_key="a" * 32, client=client)
    with pytest.raises(GeocodingProviderError):
        await provider.autocomplete("Ayat")


@pytest.mark.asyncio
async def test_a_body_without_features_is_not_a_response_we_understand():
    provider = _provider({"status": "OK"})
    with pytest.raises(GeocodingProviderError):
        await provider.autocomplete("Ayat")


@pytest.mark.asyncio
async def test_a_rejected_key_is_reported_as_unconfigured_not_as_an_error():
    provider = _provider({}, status_code=401)
    with pytest.raises(GeocodingNotConfigured):
        await provider.autocomplete("Ayat")


def test_cli_raises_nothing_but_a_geocoding_error():
    """Every failure path must be catchable as one type by the caller."""
    for exception in (
        GeocodingNotConfigured,
        GeocodingProviderError,
        GeocodingRateLimited,
        GeocodingTimeout,
    ):
        assert issubclass(exception, GeocodingError)


def test_a_user_message_never_names_the_provider_or_the_key():
    for exception in (
        GeocodingNotConfigured,
        GeocodingProviderError,
        GeocodingRateLimited,
        GeocodingTimeout,
    ):
        message = exception.user_message.lower()
        assert "geoapify" not in message
        assert "api key" not in message


# ---------------------------------------------------------------------------
# caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_repeated_query_hits_the_cache_not_the_provider():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"features": [_feature()]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = geocoding.cache.TTLCache(ttl_seconds=3600)
    provider = GeoapifyProvider(api_key="a" * 32, client=client, cache=cache)
    for _ in range(3):
        await provider.autocomplete("Ayat", country_code="et")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_different_query_is_not_served_from_the_cache():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.params.get("text")))
        return httpx.Response(200, json={"features": [_feature()]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = geocoding.cache.TTLCache(ttl_seconds=3600)
    provider = GeoapifyProvider(api_key="a" * 32, client=client, cache=cache)
    await provider.autocomplete("Ayat", country_code="et")
    await provider.autocomplete("Bole", country_code="et")
    assert calls == ["Ayat", "Bole"]


@pytest.mark.asyncio
async def test_an_expired_entry_is_refetched():
    import time

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.params.get("text")))
        return httpx.Response(200, json={"features": [_feature()]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = geocoding.cache.TTLCache(ttl_seconds=0)
    provider = GeoapifyProvider(api_key="a" * 32, client=client, cache=cache)
    await provider.autocomplete("Ayat", country_code="et")
    await provider.autocomplete("Ayat", country_code="et")
    assert len(calls) == 2


def test_attribution_is_stated():
    assert "Geoapify" in GEOAPIFY_ATTRIBUTION


def test_query_whitespace_is_collapsed():
    assert clean_query("  ayat   tsebel  ") == "ayat tsebel"


# ---------------------------------------------------------------------------
# the factory
# ---------------------------------------------------------------------------


class _StubProvider:
    """A provider that records what it was asked, so the gating is visible."""

    name = "STUB"
    is_configured = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def autocomplete(self, text, *, limit=8, country_code="et", proximity=None):
        self.calls.append(text)
        return [PlaceSuggestion(place_id="s1", name=text, formatted_address=text)]


@pytest.mark.asyncio
async def test_a_very_short_query_is_never_sent_to_the_provider(monkeypatch):
    """Two keystrokes are not a request. This is the cheapest quota guard."""
    stub = _StubProvider()
    monkeypatch.setattr(geocoding, "reset_provider", lambda provider=None: None)
    monkeypatch.setattr(geocoding, "get_provider", lambda: stub)

    assert await geocoding.search_places("ay", rate_key=None) == []
    assert stub.calls == []

    found = await geocoding.search_places("ayat tsebel", rate_key=None)
    assert stub.calls == ["ayat tsebel"]
    assert [place.name for place in found] == ["ayat tsebel"]


@pytest.mark.asyncio
async def test_a_blank_query_is_never_sent(monkeypatch):
    stub = _StubProvider()
    monkeypatch.setattr(geocoding, "get_provider", lambda: stub)
    assert await geocoding.search_places("   ", rate_key=None) == []
    assert stub.calls == []


@pytest.mark.asyncio
async def test_the_caller_key_does_not_reach_the_provider(monkeypatch):
    """Only the query goes out; the rate-limit key stays in-process."""
    stub = _StubProvider()
    monkeypatch.setattr(geocoding, "get_provider", lambda: stub)
    await geocoding.search_places("Ayat", rate_key="+251911234567")
    assert stub.calls == ["Ayat"]
    assert "+251911234567" not in repr(stub.calls)


# ---------------------------------------------------------------------------
# a provider's coordinates landing on a row we already hold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_selecting_a_result_fills_in_missing_coordinates(db):
    location_registry_service.seed_registry(db)
    db.commit()
    # The curated set is maintained by hand with no positions at all.
    bole = location_registry_service.resolve_by_text(db, "Bole")
    assert bole.has_coordinates is False

    resolved = location_registry_service.create_from_suggestion(
        db,
        PlaceSuggestion(
            place_id="geoapify-bole",
            name="Bole",
            formatted_address="Bole, Addis Ababa, Ethiopia",
            city="Addis Ababa",
            latitude=8.9987,
            longitude=38.7890,
        ),
    )
    db.commit()
    assert resolved.location.id == bole.id
    assert bole.has_coordinates


@pytest.mark.asyncio
async def test_a_providers_coordinates_never_overwrite_an_admins(db):
    location_registry_service.seed_registry(db)
    db.commit()
    bole = location_registry_service.resolve_by_text(db, "Bole")
    location_registry_service.set_coordinates(db, bole.id, 9.01, 38.76)
    db.commit()

    location_registry_service.create_from_suggestion(
        db,
        PlaceSuggestion(
            place_id="geoapify-bole",
            name="Bole",
            formatted_address="Bole",
            city="Addis Ababa",
            latitude=8.0,
            longitude=38.0,
        ),
    )
    db.commit()
    assert bole.latitude == 9.01


@pytest.mark.asyncio
async def test_a_result_without_coordinates_changes_nothing(db):
    location_registry_service.seed_registry(db)
    db.commit()
    bole = location_registry_service.resolve_by_text(db, "Bole")

    location_registry_service.create_from_suggestion(
        db,
        PlaceSuggestion(
            place_id="geoapify-bole", name="Bole", formatted_address="Bole", city="Addis Ababa"
        ),
    )
    db.commit()
    assert bole.has_coordinates is False
