"""Geoapify Geocoding Autocomplete.

Endpoint: ``GET /v1/geocode/autocomplete`` — the autocomplete endpoint rather
than ``/places``, because it ranks partial input (including the informal and
transliterated spellings Addis Ababa is full of) without needing a
place-detail call for every result.

Request shaping:

* ``filter=countrycode:et`` restricts results to Ethiopia. Unfiltered, a query
  for "Bole" happily returns a neighbourhood in Lagos.
* ``bias=proximity:9.0309,38.7461`` puts Addis Ababa first without excluding
  anywhere else in the country, so a job in Adama still ranks correctly.
* ``format=json`` avoids Geoapify's GeoJSON default, which would make every
  hit pay for geometry nobody reads.

The API key is sent as the ``apiKey`` query parameter, which is what the
provider requires. It is held in a private attribute, is never written to a log
line, and never appears in an exception message — :mod:`app.logging_config`
masks the parameter name as a second line of defence.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.services.geocoding.base import (
    DEFAULT_COUNTRY_CODE,
    MIN_KEY_LENGTH,
    GeocodingError,
    GeocodingNotConfigured,
    GeocodingProviderError,
    GeocodingRateLimited,
    GeocodingTimeout,
    PlaceSuggestion,
    clean_query,
)
from app.services.geocoding.cache import TTLCache, build_cache_key

logger = logging.getLogger(__name__)

#: Shown wherever results are rendered, as Geoapify's terms require.
GEOAPIFY_ATTRIBUTION = "🔎 Search powered by Geoapify"

#: Central Addis Ababa (Piazza), used only to bias ranking.
ADDIS_ABABA: tuple[float, float] = (9.0309, 38.7461)

#: Address components kept when breaking Geoapify's ``formatted`` apart. Anything
#: not listed is left in the formatted address rather than guessed at.
_CITY_KEYS = ("city", "town", "village", "municipality", "county")
_DISTRICT_KEYS = ("district", "suburb", "city_district", "subcity")


class GeoapifyProvider:
    """Place autocomplete backed by Geoapify."""

    name = "GEOAPIFY"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        country_code: str = DEFAULT_COUNTRY_CODE,
        proximity: tuple[float, float] | None = ADDIS_ABABA,
        cache: TTLCache | None = None,
        client: httpx.AsyncClient | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._api_key = (api_key if api_key is not None else settings.geoapify_api_key)
        self._base_url = base_url or settings.geoapify_base_url
        self._timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.geoapify_timeout_seconds
        )
        self._country_code = country_code
        self._proximity = proximity
        self._enabled = settings.geoapify_enabled if enabled is None else enabled
        self._cache = cache
        #: Injected in the tests with ``httpx.MockTransport``; created lazily
        #: otherwise so importing this module never opens a socket.
        self._client = client
        self._owns_client = client is None

    # -- configuration --------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return bool(self._enabled) and len(self._api_key.strip()) >= MIN_KEY_LENGTH

    # -- public API -----------------------------------------------------

    async def autocomplete(
        self,
        text: str,
        *,
        limit: int = 8,
        country_code: str = DEFAULT_COUNTRY_CODE,
        proximity: tuple[float, float] | None = None,
    ) -> list[PlaceSuggestion]:
        """Places matching ``text``, best first.

        An empty result means the provider answered and knows nothing, which is
        different from every failure mode, all of which raise.
        """
        if not self.is_configured:
            raise GeocodingNotConfigured

        query = clean_query(text)
        if not query:
            return []

        country = (country_code or self._country_code).strip().lower()
        bias = self._proximity if proximity is None else proximity
        capped = max(1, min(int(limit), 25))
        key = build_cache_key(
            query, limit=capped, country_code=country, proximity=bias
        )
        if self._cache is not None:
            cached = self._cache.get(key)
            if cached is not None:
                # The cache holds a list of frozen dataclasses, so handing it
                # straight back is safe: nothing can mutate a caller's copy.
                return list(cached)  # type: ignore[arg-type]

        suggestions = await self._request(query, capped, country, bias)
        if self._cache is not None:
            self._cache.set(key, suggestions)
        # Deliberately logs the query and the count only — never the URL, which
        # carries the API key as a query parameter.
        logger.info(
            "Geocoding search completed",
            extra={"query": query, "results": len(suggestions), "cached": False},
        )
        return suggestions

    # -- internals ------------------------------------------------------

    async def _request(
        self,
        query: str,
        limit: int,
        country_code: str,
        proximity: tuple[float, float] | None,
    ) -> list[PlaceSuggestion]:
        params = {
            "text": query,
            "format": "json",
            "limit": str(limit),
            "lang": "en",
            "filter": f"countrycode:{country_code}",
            "apiKey": self._api_key,
        }
        if proximity is not None:
            params["bias"] = f"proximity:{proximity[0]},{proximity[1]}"

        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)
            self._client = client
        try:
            try:
                response = await client.get(self._base_url, params=params)
            except httpx.TimeoutException as exc:
                raise GeocodingTimeout from exc
            except httpx.HTTPError as exc:
                # Covers connection resets and DNS failures. The message is
                # dropped rather than attached: it can echo the request URL.
                logger.warning("Geocoding request failed", extra={"query": query})
                raise GeocodingProviderError from exc

            status = response.status_code
            if status == httpx.codes.TOO_MANY_REQUESTS:
                logger.warning("Geocoding provider rate limited the bot")
                raise GeocodingRateLimited
            if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
                # Almost always a wrong or exhausted key. The user is told
                # search is unavailable; the operator finds out from this log
                # line, which names no secret.
                logger.error(
                    "Geocoding provider rejected the credentials (HTTP %s)", status
                )
                raise GeocodingNotConfigured
            if status >= 400:
                logger.warning("Geocoding provider error (HTTP %s)", status)
                raise GeocodingProviderError
            try:
                payload = response.json()
            except ValueError as exc:
                raise GeocodingProviderError from exc
        finally:
            if self._owns_client and client is not self._client:
                await client.aclose()

        return self._parse(payload, country_code)

    def _parse(self, payload: Any, country_code: str) -> list[PlaceSuggestion]:
        """Turn a response body into suggestions, skipping unusable entries.

        One malformed feature must not fail the whole search, so each entry is
        validated on its own and dropped if it has no usable identifier.
        """
        if not isinstance(payload, dict):
            raise GeocodingProviderError
        features = payload.get("features")
        if features is None:
            # ``features`` is always present on success; a body without it is
            # not a response we know how to read.
            raise GeocodingProviderError
        if not isinstance(features, list):
            raise GeocodingProviderError

        suggestions: list[PlaceSuggestion] = []
        for feature in features:
            suggestion = self._parse_feature(feature, country_code)
            if suggestion is not None:
                suggestions.append(suggestion)
        return suggestions

    def _parse_feature(
        self, feature: Any, country_code: str
    ) -> PlaceSuggestion | None:
        if not isinstance(feature, dict):
            return None
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            return None
        place_id = _as_text(properties.get("place_id"))
        if not place_id:
            return None

        formatted = _as_text(properties.get("formatted"))
        name = _as_text(properties.get("name")) or _first_part(formatted)
        if not name:
            return None

        city = _first_present(properties, _CITY_KEYS)
        district = _first_present(properties, _DISTRICT_KEYS)
        return PlaceSuggestion(
            place_id=place_id,
            name=name,
            formatted_address=formatted or name,
            city=city,
            sub_city=district,
            country_code=_as_text(properties.get("country_code")).lower()
            or country_code,
            latitude=_as_float(properties.get("lat")),
            longitude=_as_float(properties.get("lon")),
        )

    async def aclose(self) -> None:
        """Release the lazily created client."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# Response coercion
# ---------------------------------------------------------------------------
#
# Every helper below returns ``None``/``""`` for anything unusable rather than
# raising, so one odd field degrades that field only.


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _as_float(value: Any) -> float | None:
    """A coordinate in range, or ``None``.

    Zero is rejected on purpose. A geocoder that has no position for a place
    reports ``0`` (the origin, off the Gulf of Guinea) as readily as ``null``,
    and scoring a distance from there would be fiction.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not -90.0 <= number <= 90.0:
        return None
    return number or None


def _first_present(properties: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = _as_text(properties.get(key))
        if text:
            return text
    return ""


def _first_part(formatted: str) -> str:
    """The leading comma-separated component of a formatted address."""
    if not formatted:
        return ""
    return _as_text(formatted.split(",")[0])


__all__ = [
    "ADDIS_ABABA",
    "GEOAPIFY_ATTRIBUTION",
    "GeoapifyProvider",
    "GeocodingError",
]