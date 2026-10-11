"""The provider contract every geocoder implements.

The rest of the application only ever sees :class:`PlaceSuggestion` and the
:class:`GeocodingError` family, so swapping Geoapify for another service is a
new module plus one line in :mod:`app.services.geocoding` — no caller changes.

Two rules the concrete providers must honour:

* **Never raise anything but a :class:`GeocodingError`.** Callers show
  ``exc.user_message`` to a person, so it must be safe to display and must
  never contain the API key, the raw provider URL or a provider response body.
* **Never invent a result.** A place with no coordinates comes back with
  ``latitude``/``longitude`` as ``None``; the scoring layer treats that as
  "proximity unknown", which is a different and honest answer from a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "DEFAULT_COUNTRY_CODE",
    "MIN_KEY_LENGTH",
    "GeocodingError",
    "GeocodingNotConfigured",
    "GeocodingProvider",
    "GeocodingProviderError",
    "GeocodingRateLimited",
    "GeocodingTimeout",
    "PlaceSuggestion",
    "clean_query",
]


#: Results are restricted to one country. Ethiopia is the only market the
#: platform operates in, and an unfiltered geocoder will happily return
#: "Bole" in Lagos.
DEFAULT_COUNTRY_CODE = "et"

#: A key shorter than this is treated as unset rather than sent to a provider
#: that will answer 401, which would surface to the user as a broken search.
MIN_KEY_LENGTH = 8

_WHITESPACE_RE = re.compile(r"\s+")


def clean_query(value: object) -> str:
    """Trim and collapse a user-typed search string.

    Whitespace is collapsed because ``"ayat   tsebel"`` and ``"ayat tsebel"``
    are the same request and must share a cache entry.
    """
    text = _WHITESPACE_RE.sub(" ", str(value or "").strip())
    return text[:120]


@dataclass(frozen=True)
class PlaceSuggestion:
    """One place a provider offered, plus where it came from."""

    #: The provider's own identifier. Stored so the same result is never
    #: fetched or inserted twice. It is *not* comparable with any other
    #: provider's identifiers, and a manual location has none.
    place_id: str
    #: Short label for a button, e.g. "Ayat Tsebel".
    name: str
    #: Full one-line address for disambiguation, e.g. "Ayat Tsebel, Addis
    #: Ababa, Ethiopia".
    formatted_address: str
    city: str = ""
    sub_city: str = ""
    country_code: str = ""
    latitude: float | None = None
    longitude: float | None = None

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def label(self) -> str:
        """What a button shows when the two lines would be too long."""
        if self.sub_city and self.sub_city.lower() not in self.name.lower():
            return f"{self.name} — {self.sub_city}"
        return self.name


class GeocodingError(Exception):
    """A search could not be completed.

    ``user_message`` is the only part intended for a person to read.
    """

    #: Safe to show verbatim in the chat.
    user_message = (
        "The location search is unavailable right now. "
        "Please add your location manually below."
    )

    def __init__(self, user_message: str | None = None) -> None:
        super().__init__(user_message or type(self).user_message)
        if user_message:
            self.user_message = user_message


class GeocodingNotConfigured(GeocodingError):
    """No API key is configured, so search is switched off entirely.

    This is a deployment state, not a failure: the selector hides search and
    offers manual entry, and the application runs normally.
    """

    user_message = (
        "Location search is not set up on this bot yet. "
        "Please add your location manually below."
    )


class GeocodingRateLimited(GeocodingError):
    """The provider's quota is exhausted for now."""

    user_message = (
        "We are searching a lot of locations right now. "
        "Please try again in a minute, or add your location manually below."
    )


class GeocodingTimeout(GeocodingError):
    """The provider did not answer in time."""

    user_message = (
        "The location search took too long. "
        "Please try again, or add your location manually below."
    )


class GeocodingProviderError(GeocodingError):
    """The provider failed, or answered with something we cannot read.

    The provider's own response is deliberately not carried on the exception:
    it would reach a log line and can contain request metadata.
    """

    user_message = (
        "The location search could not be completed. "
        "Please add your location manually below."
    )


@runtime_checkable
class GeocodingProvider(Protocol):
    """What the application needs from a place-search service."""

    #: Stored on :class:`~app.models.Location` as ``provider``.
    name: str
    #: Required before ``autocomplete`` will do anything.
    is_configured: bool

    async def autocomplete(
        self,
        text: str,
        *,
        limit: int = 8,
        country_code: str = DEFAULT_COUNTRY_CODE,
        proximity: tuple[float, float] | None = None,
    ) -> list[PlaceSuggestion]:
        """Places matching ``text``, best first.

        Raises :class:`GeocodingError` (or a subclass) on every failure path;
        an empty list means "the provider answered and knows nothing".
        """