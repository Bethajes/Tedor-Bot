"""Place search, behind a replaceable provider.

Only :func:`search_places` should be called from outside this package. It owns
the three cross-cutting concerns — configuration, caching and rate limiting —
so a tutor in the bot, a parent in the bot and a REST client all cost the same
and get the same failure handling.

Adding a second provider means writing a module that satisfies
:class:`~app.services.geocoding.base.GeocodingProvider` and registering it in
:data:`PROVIDERS`. Nothing else in the application changes.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.services.geocoding.base import (
    DEFAULT_COUNTRY_CODE,
    GeocodingError,
    GeocodingNotConfigured,
    GeocodingProvider,
    GeocodingProviderError,
    GeocodingRateLimited,
    GeocodingTimeout,
    PlaceSuggestion,
    clean_query,
)
from app.services.geocoding.cache import TTLCache
from app.services.geocoding.geoapify import (
    ADDIS_ABABA,
    GEOAPIFY_ATTRIBUTION,
    GeoapifyProvider,
)
from app.services import rate_limit

logger = logging.getLogger(__name__)

__all__ = [
    "ADDIS_ABABA",
    "GEOAPIFY_ATTRIBUTION",
    "GeocodingError",
    "GeocodingNotConfigured",
    "GeocodingProvider",
    "GeocodingProviderError",
    "GeocodingRateLimited",
    "GeocodingTimeout",
    "PlaceSuggestion",
    "PROVIDERS",
    "attribution",
    "get_provider",
    "reset_provider",
    "search_places",
]

#: Registered providers, keyed by the value stored on ``Location.provider``.
#: Adding one here is the whole of "make the provider replaceable".
PROVIDERS: dict[str, str] = {"GEOAPIFY": "app.services.geocoding.geoapify"}

#: Fewest characters worth a provider request. Below this the answer is
#: noise, and it is the cheapest possible guard against spending quota on
#: single keystrokes.
MIN_QUERY_LENGTH = 3

_cache = TTLCache(ttl_seconds=float(settings.geoapify_cache_ttl_seconds))
_provider: GeocodingProvider | None = None


def attribution() -> str:
    """The provider credit line required by the current provider's terms."""
    return GEOAPIFY_ATTRIBUTION


def get_provider() -> GeocodingProvider:
    """The configured provider, built once per process.

    Constructed lazily so an import never reads configuration the tests have
    not set up yet, and memoised so the cache and any injected client survive.
    """
    global _provider
    if _provider is None:
        _provider = GeoapifyProvider(cache=_cache)
    return _provider


def reset_provider(provider: GeocodingProvider | None = None) -> None:
    """Swap the provider. Used by the tests and by a future config reload."""
    global _provider
    _provider = provider


async def search_places(
    text: str,
    *,
    limit: int | None = None,
    rate_key: str | None = None,
) -> list[PlaceSuggestion]:
    """Search for places, applying the rate limit and the cache first.

    ``rate_key`` identifies the caller (a Telegram user id, a client address)
    and is the only per-caller state; it is never logged or stored.
    """
    provider = get_provider()
    if not provider.is_configured:
        raise GeocodingNotConfigured

    query = clean_query(text)
    if len(query) < MIN_QUERY_LENGTH:
        # Not an error: the caller shows "keep typing".
        return []

    if rate_key is not None:
        verdict = rate_limit.allow(
            "location_search",
            rate_key,
            limit=settings.location_search_rate_limit,
            window=settings.location_search_rate_window,
        )
        if not verdict.allowed:
            logger.info("Location search rate limited")
            raise GeocodingRateLimited(verdict.message)

    return await provider.autocomplete(
        query,
        limit=limit if limit is not None else settings.geoapify_limit,
        country_code=DEFAULT_COUNTRY_CODE,
    )


def reset_cache() -> None:
    """Drop cached provider responses. Used by the tests."""
    _cache.clear()