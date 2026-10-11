"""Location search and registration over HTTP.

Three endpoints, guarded to different degrees:

* ``GET /api/locations/search`` — spends the geocoding quota, so it requires
  either the API secret or a signed Mini App Init Data, and is rate limited by
  client address. A public endpoint here would hand anonymous traffic a way to
  drain the quota.
* ``POST /api/locations`` — guarded the same way, and rate limited harder: it
  writes a durable row that a person has to review.
* ``GET /api/locations/{id}`` — unauthenticated, and returns only the location's
  own fields. No tutor, job or user is ever included, so there is nothing on
  this route that a public caller should not see.

Only ``search`` talks to a provider, and only ever to the autocomplete
endpoint: a suggestion already carries coordinates, so a second place-detail
call per result would double the cost for information we already have.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.auth import require_api_secret_or_telegram_user
from app.api.schemas import LocationRegistrationRequest
from app.config import settings
from app.database import get_db
from app.logging_config import get_logger
from app.services import geocoding, location_registry_service, rate_limit
from app.services.geocoding.base import GeocodingError
from app.services.location_registry_service import LocationError
from sqlalchemy.orm import Session

logger = get_logger(__name__)

router = APIRouter(prefix="/api/locations", tags=["locations"])

DbSession = Annotated[Session, Depends(get_db)]
Secret = Annotated[bool, Depends(require_api_secret_or_telegram_user)]

#: Longest query accepted. Longer is not a place name anyone typed.
MAX_QUERY_LENGTH = 120


def _client_key(request: Request) -> str:
    """The rate-limit key for a request.

    The forwarded address is honoured because the deployment terminates TLS in
    front of this app, so ``request.client`` would otherwise name the proxy and
    collapse every caller into one bucket.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _enforce_limit(request: Request, scope: str, limit: int, window: int) -> None:
    verdict = rate_limit.allow(scope, _client_key(request), limit=limit, window=window)
    if not verdict.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=verdict.message,
            headers={"Retry-After": str(verdict.retry_after)},
        )


@router.get("/search")
async def search_locations(
    request: Request,
    q: Annotated[str, Query(max_length=MAX_QUERY_LENGTH)],
    limit: Annotated[int, Query(ge=1, le=25)] = 8,
    _secret: Secret = None,
    session: DbSession = None,
) -> dict[str, Any]:
    """Places matching ``q``, restricted to Ethiopia and biased toward Addis.

    Returns a recognised location in our own records as well as the provider's
    suggestions, so a caller gets a canonical row to store even when the
    geocoder has nothing to offer.
    """
    _enforce_limit(
        request,
        "location_api_search",
        settings.location_api_rate_limit,
        settings.location_api_rate_window,
    )
    query = " ".join(str(q or "").split())
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A search term is required.",
        )

    known = location_registry_service.resolve_by_text(session, query)
    suggestions: list[dict[str, Any]] = []
    try:
        places = await geocoding.search_places(query, limit=limit)
    except GeocodingError as exc:
        # The provider's own message is never returned to a client: it can
        # describe the account or the request.
        logger.warning("Location search over HTTP failed")
        places = []
        error = exc.user_message
    else:
        error = None
        suggestions = [
            {
                "placeId": place.place_id,
                "name": place.name,
                "formattedAddress": place.formatted_address,
                "city": place.city,
                "subCity": place.sub_city,
                "countryCode": place.country_code,
                "latitude": place.latitude,
                "longitude": place.longitude,
            }
            for place in places
        ]

    return {
        "query": query,
        "known": _location_payload(known) if known is not None else None,
        "suggestions": suggestions,
        "error": error,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_location(
    request: Request,
    payload: LocationRegistrationRequest,
    _secret: Secret = None,
    session: DbSession = None,
) -> dict[str, Any]:
    """Register a place the geocoder could not find.

    An existing equivalent location is returned instead of a copy, with
    ``duplicate: true``. A newly created one is always ``PENDING``: the caller
    is told it is unverified, because it is. Coordinates are not accepted —
    a typed place has no position until an admin establishes one.
    """
    _enforce_limit(
        request,
        "location_api_manual",
        settings.location_manual_rate_limit,
        settings.location_manual_rate_window,
    )

    try:
        resolved = location_registry_service.resolve_or_create_manual(
            session,
            name=payload.name,
            city=payload.city,
            sub_city=payload.sub_city,
            landmark=payload.landmark,
        )
    except LocationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=exc.user_message
        ) from None
    session.commit()

    result = _location_payload(resolved.location)
    result["duplicate"] = resolved.is_suggestion
    return result


@router.get("/{location_id}")
def get_location(
    location_id: int,
    session: DbSession = None,
) -> dict[str, Any]:
    """One location's public fields.

    No tutor or job is ever attached, so the response cannot disclose who works
    where.
    """
    location = location_registry_service.get_location(session, location_id)
    if location is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found."
        )
    return _location_payload(location)


def _location_payload(location: Any) -> dict[str, Any]:
    """The DTO for a location.

    ``normalizedName`` is included so a caller can store the same key the
    matching engine compares on, rather than re-deriving it.
    """
    return {
        "id": location.id,
        "name": location.name,
        "normalizedName": location.normalized_name,
        "city": location.city,
        "subCity": location.sub_city,
        "landmark": location.landmark,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "provider": location.provider,
        "verificationStatus": location.verification_status,
    }
