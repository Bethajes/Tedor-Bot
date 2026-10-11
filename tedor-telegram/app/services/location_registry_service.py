"""Canonical locations: creation, resolution, review and merging.

This is the only module that writes :class:`~app.models.Location`. Everything
else — the tutor selector, the parent flow, the admin console, the REST API —
comes through here, which is what keeps three rules true at once:

* **A duplicate is never created silently.** A submission is checked against
  the canonical names and then against the verified aliases, and an existing
  row is returned as a suggestion instead of a second copy.
* **A manually typed place is never verified.** It is stored as
  ``provider=MANUAL``, ``verification_status=PENDING`` and with null
  coordinates. Only the admin actions in this module can change that.
* **A merge loses nothing.** Tutor rows, job rows and aliases are repointed at
  the canonical row inside one transaction, and the merged-away row is kept as
  a ``REJECTED`` tombstone pointing at its survivor rather than deleted.

The curated registry in :mod:`app.services.location_service` is seeded here as
``REGISTRY`` rows, so a pre-existing free-text location such as "Bole" resolves
to a real row without anyone retyping it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    COUNTRY_ETHIOPIA_DEFAULT_CITY,
    LOCATION_VERIFICATION_STATUSES,
    Location,
    LocationAlias,
    utcnow,
)
from app.services import location_service

logger = logging.getLogger(__name__)

__all__ = [
    "ETHIOPIAN_CITIES",
    "LocationError",
    "LocationNotFound",
    "ManualLocationRejected",
    "ResolvedLocation",
    "add_alias",
    "approve_location",
    "count_pending",
    "create_from_suggestion",
    "describe",
    "find_equivalent",
    "get_location",
    "haversine_km",
    "link_tutor_rows",
    "list_locations",
    "merge_locations",
    "normalize_city",
    "normalize_name",
    "pending_locations",
    "reject_location",
    "resolve_by_text",
    "resolve_location_for_text",
    "resolve_or_create_manual",
    "search_locations",
    "seed_registry",
    "set_coordinates",
    "suggest_merge_targets",
    "summarize",
    "unlinked_tutor_rows",
    "unverified_label",
    "verification_badge",
]

#: Cities a manual submission may name. Ethiopia only — the provider is
#: restricted to ``countrycode:et`` and a manual entry must not be a weaker
#: escape hatch than a searched one.
ETHIOPIAN_CITIES: tuple[str, ...] = (
    "Addis Ababa",
    "Adama",
    "Adigrat",
    "Bahir Dar",
    "Dessie",
    "Dire Dawa",
    "Gambela",
    "Gondar",
    "Hawassa",
    "Jimma",
    "Mekelle",
)

#: Ethiopia's bounding box, used to reject coordinates an admin cannot have
#: meant. Deliberately generous: it rejects "51.5, -0.1" (London) and accepts
#: anywhere plausibly inside the country.
ETHIOPIA_BOUNDS = (3.0, 15.0, 32.0, 49.0)

_MAX_NAME_LENGTH = 160
_MAX_CITY_LENGTH = 120
_MAX_LANDMARK_LENGTH = 240

#: Similarity above which a submitted name is treated as probably-a-duplicate
#: of an existing location and confirmed with the user instead of created.
#:
#: 0.80 catches "Goro Sefer Market" against "Gorosefer market" and "Woreda 12"
#: against "Woreda 12 area", which is the point: a missing space is the single
#: most likely typing difference. It sits below the ordinary 0.85 fuzz default
#: because a false positive here only costs an extra question — the user
#: declines and keeps their own spelling — whereas a missed duplicate becomes a
#: row somebody has to merge by hand later.
DUPLICATE_RATIO = 0.80


class LocationError(ValueError):
    """A location could not be created or changed.

    Subclasses ``ValueError`` so a handler that already catches validation
    errors catches this one too. ``user_message`` is safe to show verbatim.
    """

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class LocationNotFound(LocationError):
    """No location matched the given id."""


class ManualLocationRejected(LocationError):
    """A manual submission failed validation."""


@dataclass(frozen=True)
class ResolvedLocation:
    """The outcome of resolving a user's text to a location.

    ``location`` is the row the caller should attach to a tutor or a job.
    ``is_suggestion`` is ``True`` when it already existed and the user is being
    offered it instead of creating a copy — the caller shows a different
    confirmation in that case.
    """

    location: Location
    is_suggestion: bool = False
    #: How it was found: ``EXACT``, ``ALIAS`` or ``CREATED``. Useful in admin
    #: copy and in the audit trail, never in matching.
    matched_by: str = "EXACT"

    @property
    def is_verified(self) -> bool:
        return self.location.is_verified

    @property
    def label(self) -> str:
        return self.location.name


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def normalize_name(value: object) -> str | None:
    """The canonical search key for a location name.

    Delegates to the shared normaliser so "Ayat_Tsebel", "ayat tsebel" and
    "Ayat Tsebel, Addis Ababa" produce one key, then falls back to a plain
    cleaned form for a place the curated registry has never seen — a manual
    entry must still be searchable even when it matches nothing.
    """
    key = location_service.normalize_location(value)
    if key:
        return key
    return location_service.canonical_name(value)


def normalize_city(value: object, *, default: str = COUNTRY_ETHIOPIA_DEFAULT_CITY) -> str:
    """A city name in title case, defaulting to Addis Ababa."""
    text = " ".join(str(value or "").split())
    if not text:
        return default
    return text[:_MAX_CITY_LENGTH]


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def get_location(session: Session, location_id: int | None) -> Location | None:
    """One live location by id, or ``None``."""
    if location_id is None:
        return None
    return session.get(Location, int(location_id))


def require_location(session: Session, location: Location | int) -> Location:
    """A location from a row or an id, raising when it does not exist."""
    if isinstance(location, Location):
        return location
    found = get_location(session, location)
    if found is None:
        raise LocationNotFound(f"Location {location} was not found.")
    return found


def find_by_normalized_name(
    session: Session, normalized_name: str, city: str | None = None
) -> Location | None:
    """A live location whose canonical name matches, optionally in one city.

    ``city`` is a filter, not a preference. Two places can share a name in
    different cities — "Bole" in Addis Ababa and "Bole" in Hawassa are
    different places — so a caller that knows the city must not be handed a
    row from another one. A caller with no city gets the first match, which is
    the honest answer when the city is genuinely unknown.
    """
    if not normalized_name:
        return None
    statement = select(Location).where(
        Location.normalized_name == normalized_name,
        Location.merged_into_id.is_(None),
    )
    if city and str(city).strip():
        statement = statement.where(
            func.lower(Location.city) == str(city).strip().lower()
        )
    return session.scalars(statement.order_by(Location.id)).first()


def find_by_alias(session: Session, normalized_alias: str) -> Location | None:
    """A live location an admin has confirmed this spelling belongs to."""
    if not normalized_alias:
        return None
    return session.scalars(
        select(Location)
        .join(LocationAlias, LocationAlias.location_id == Location.id)
        .where(
            LocationAlias.normalized_alias == normalized_alias,
            Location.merged_into_id.is_(None),
        )
        .order_by(Location.id)
    ).first()


def resolve_by_text(session: Session, text: object) -> Location | None:
    """Resolve free text to a live location, trying name then alias.

    Returns ``None`` for text that matches nothing, which is a normal answer
    and not an error: the caller then offers manual registration.
    """
    key = normalize_name(text)
    if not key:
        return None
    return find_by_normalized_name(session, key) or find_by_alias(session, key)


def in_code_key(text: object) -> str | None:
    """The curated registry key for ``text``, without touching the database.

    :data:`app.services.location_service.LOCATIONS` is a registry maintained in
    source and used by the matcher. Reading it here means a location can be
    resolved on a database that has not been seeded yet, which is what makes
    the answer stable across a fresh install and a migrated one.
    """
    return location_service.normalize_location(text)


def resolve_location_for_text(
    session: Session, text: object, *, city: str | None = None
) -> Location | None:
    """:func:`resolve_by_text` with an optional city hint.

    The hint only breaks ties between same-named places in different cities; it
    never prevents a match on its own.
    """
    key = normalize_name(text)
    if not key:
        return None
    found = find_by_normalized_name(session, key, city) if city else None
    return found or resolve_by_text(session, text)


def link_tutor_rows(session: Session, tutor: Any) -> int:
    """Attach canonical locations to a tutor's existing free-text rows.

    Returns how many rows were linked. Rows whose text matches nothing are
    left with ``location_id = None`` and ``source = 'IMPORTED'`` — an
    unresolved row is reported to an admin as needing a link, which is honest,
    where a forced guess would quietly change what a tutor said.

    Safe to call repeatedly: rows that already point at a location are skipped.
    """
    from app.models import TutorLocation

    linked = 0
    for row in list(tutor.tutoring_locations):
        if row.location_id is not None:
            continue
        location = resolve_by_text(session, row.normalized_location or row.location)
        if location is None:
            if row.source is None:
                row.source = "IMPORTED"
            continue
        row.location_id = location.id
        row.source = row.source or "IMPORTED"
        if row.created_at is None:
            row.created_at = utcnow()
        linked += 1
    session.flush()
    return linked


def unlinked_tutor_rows(session: Session, *, limit: int = 100) -> list[Any]:
    """Tutor location rows still pointing at nothing.

    The work list behind "normalise or confirm existing locations": an admin
    either links them to a location or leaves them, and both outcomes are
    recorded.
    """
    from app.models import TutorLocation

    return list(
        session.scalars(
            select(TutorLocation)
            .where(TutorLocation.location_id.is_(None))
            .order_by(TutorLocation.id)
            .limit(max(1, min(limit, 500)))
        )
    )


def _similarity(left: str, right: str) -> float:
    """Character-trigram overlap, in ``[0, 1]``.

    Good enough to catch "Kazanchis"/"Kazanche" and "Woreda 12"/"Woreda 12.",
    which is all it is used for. Two places that are genuinely different do
    not share three-character runs, so a high score means "ask the user".
    """

    def grams(value: str) -> set[str]:
        padded = f"  {value} "
        return {padded[index : index + 3] for index in range(len(padded) - 2)}

    left_grams, right_grams = grams(left), grams(right)
    if not left_grams or not right_grams:
        return 0.0
    shared = len(left_grams & right_grams)
    if not shared:
        return 0.0
    return 2.0 * shared / (len(left_grams) + len(right_grams))


def find_equivalent(
    session: Session, name: str, *, city: str | None = None
) -> tuple[Location, str] | None:
    """Find an existing location a submission probably duplicates.

    Checked in order of confidence: an exact canonical key, a verified alias,
    then trigram similarity within the same city. Returns ``(location, how)``.
    """
    key = normalize_name(name)
    if not key:
        return None

    exact = find_by_normalized_name(session, key, city)
    if exact is not None:
        return exact, "EXACT"

    aliased = find_by_alias(session, key)
    if aliased is not None:
        return aliased, "ALIAS"

    # Only offer a near match inside the same city: "Bole" in Addis and "Bole"
    # elsewhere are different places, and merging across cities would be worse
    # than a duplicate.
    statement = select(Location).where(
        Location.merged_into_id.is_(None), Location.verification_status != "REJECTED"
    )
    if city:
        statement = statement.where(func.lower(Location.city) == city.strip().lower())
    for candidate in session.scalars(statement):
        if _similarity(key, candidate.normalized_name) >= DUPLICATE_RATIO:
            return candidate, "SIMILAR"
    return None


def search_locations(
    session: Session,
    query: str,
    *,
    limit: int = 20,
    include_rejected: bool = False,
    include_inactive: bool = False,
) -> list[Location]:
    """Locations whose name, city, code or alias contains ``query``.

    Used by the admin console (finding a merge target) and by the API. Never
    returns anything that would identify a tutor.

    A deactivated place is left out by default. It still resolves by name for
    a tutor or a job that already points at it — otherwise a withdrawn
    neighbourhood would make their own history unreadable — but it must not be
    offered to somebody making a fresh selection. ``include_inactive=True``
    exists for the review console, which needs to show what was withdrawn.
    """
    text = " ".join(str(query or "").split())
    if not text:
        return []
    pattern = f"%{text.casefold()}%"
    statement = select(Location).where(Location.merged_into_id.is_(None))
    if not include_inactive:
        statement = statement.where(Location.is_active.is_(True))
    if not include_rejected:
        statement = statement.where(Location.verification_status != "REJECTED")
    aliased = (
        select(LocationAlias.location_id)
        .where(LocationAlias.normalized_alias.like(pattern))
        .scalar_subquery()
    )
    statement = statement.where(
        or_(
            Location.normalized_name.like(pattern),
            func.lower(Location.name).like(pattern),
            func.lower(Location.city).like(pattern),
            # Searching by the channel's public code lets an admin paste
            # "AA-0001" from a channel post straight into the selector.
            func.lower(Location.location_code).like(pattern),
            Location.id.in_(aliased),
        )
    ).order_by(Location.name).limit(max(1, min(limit, 100)))
    return list(session.scalars(statement))


def list_locations(
    session: Session,
    *,
    verification_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Location]:
    """Locations for the admin console, newest first within a status."""
    statement = select(Location)
    if verification_status:
        statement = statement.where(Location.verification_status == verification_status)
    statement = (
        statement.order_by(Location.created_at.desc(), Location.id.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 100)))
    )
    return list(session.scalars(statement))


def pending_locations(session: Session, *, limit: int = 50, offset: int = 0) -> list[Location]:
    """Manual submissions still waiting for an admin, oldest first.

    Oldest first on purpose: the queue is a work list, and the item that has
    been waiting longest is the one a tutor is waiting on.
    """
    statement = (
        select(Location)
        .where(
            Location.verification_status == "PENDING",
            Location.provider != "REGISTRY",
            Location.merged_into_id.is_(None),
        )
        .order_by(Location.created_at, Location.id)
        .offset(max(0, offset))
        .limit(max(1, min(limit, 100)))
    )
    return list(session.scalars(statement))


def count_pending(session: Session) -> int:
    """How many manual submissions are awaiting review."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(Location)
            .where(
                Location.verification_status == "PENDING",
                Location.provider != "REGISTRY",
                Location.merged_into_id.is_(None),
            )
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def _validate_manual_fields(
    name: Any, city: Any, sub_city: Any, landmark: Any
) -> tuple[str, str, str | None, str | None]:
    """Clean and length-cap a manual submission, or explain why not."""
    clean_name = " ".join(str(name or "").split())
    if not clean_name:
        raise ManualLocationRejected("Please enter a location name.")
    if len(clean_name) > _MAX_NAME_LENGTH:
        raise ManualLocationRejected(
            f"That name is too long — keep it under {_MAX_NAME_LENGTH} characters."
        )

    clean_city = normalize_city(city)
    known_cities = {item.casefold(): item for item in ETHIOPIAN_CITIES}
    if clean_city.casefold() not in known_cities:
        raise ManualLocationRejected(
            "Sorry, we only serve Ethiopia. Please pick a city such as "
            + ", ".join(ETHIOPIAN_CITIES[:6])
            + "."
        )
    clean_city = known_cities[clean_city.casefold()]

    clean_sub_city = " ".join(str(sub_city or "").split())[:_MAX_CITY_LENGTH] or None
    clean_landmark = " ".join(str(landmark or "").split())[:_MAX_LANDMARK_LENGTH] or None
    return clean_name, clean_city, clean_sub_city, clean_landmark


def _insert_location(session: Session, **values: Any) -> Location:
    """Insert a location, translating a lost uniqueness race into a lookup.

    Two users submitting "Kazanchis" at once is the ordinary case on a busy
    bot, not an error: the loser of the race gets the winner's row back.
    """
    location = Location(**values)
    session.add(location)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = find_by_normalized_name(
            session, values["normalized_name"], values.get("city")
        )
        if existing is None:
            existing = find_by_alias(session, values["normalized_name"])
        if existing is None:
            raise
        return existing
    return location


def _adopt_coordinates(location: Location, suggestion: Any) -> None:
    """Take a provider's coordinates for a row that has none.

    A geocoder result is a position established reliably, which is exactly the
    bar the admin console sets before it will accept one. So a search that
    resolves to a row we already hold — a seeded registry entry with no
    coordinates — leaves with the position filled in, rather than throwing away
    evidence the provider just gave us and reporting proximity as uncertain.

    Coordinates are never overwritten: an admin who set one deliberately keeps
    it.
    """
    if location.has_coordinates or location.is_merged:
        return
    latitude = getattr(suggestion, "latitude", None)
    longitude = getattr(suggestion, "longitude", None)
    if latitude is None or longitude is None:
        return
    location.latitude = float(latitude)
    location.longitude = float(longitude)
    location.updated_at = utcnow()


def create_from_suggestion(
    session: Session,
    suggestion: Any,
    *,
    created_by_user_id: int | None = None,
) -> ResolvedLocation:
    """Store a provider result as a location.

    Reuses an existing row for the same provider place id rather than inserting
    a second copy, so the same geocoder result selected by two tutors stays one
    location.
    """
    place_id = str(getattr(suggestion, "place_id", "") or "").strip()
    name = " ".join(str(getattr(suggestion, "name", "") or "").split())
    if not place_id or not name:
        raise LocationError("That search result is no longer available.")

    existing = session.scalars(
        select(Location).where(
            Location.provider == "GEOAPIFY", Location.provider_place_id == place_id
        )
    ).first()
    if existing is not None:
        _adopt_coordinates(existing, suggestion)
        return ResolvedLocation(location=existing, is_suggestion=True, matched_by="EXACT")

    normalized = normalize_name(name)
    if normalized:
        duplicate = find_by_normalized_name(session, normalized)
        if duplicate is not None:
            # The provider and our own data already agree on what this place is
            # called — a seeded registry entry, or something a tutor registered
            # by hand earlier. Reuse that row rather than storing the same
            # place twice under two providers.
            _adopt_coordinates(duplicate, suggestion)
            return ResolvedLocation(
                location=duplicate, is_suggestion=True, matched_by="EXACT"
            )

    city = normalize_city(getattr(suggestion, "city", "")) if getattr(
        suggestion, "city", ""
    ) else COUNTRY_ETHIOPIA_DEFAULT_CITY
    location = _insert_location(
        session,
        name=name[:_MAX_NAME_LENGTH],
        normalized_name=normalized or name.casefold(),
        city=city,
        sub_city=(getattr(suggestion, "sub_city", "") or "")[:_MAX_CITY_LENGTH] or None,
        latitude=getattr(suggestion, "latitude", None),
        longitude=getattr(suggestion, "longitude", None),
        provider="GEOAPIFY",
        provider_place_id=place_id,
        # A provider result is a real geographic place, so it is not held in the
        # manual review queue. The *name* a user typed is still theirs to see.
        verification_status="VERIFIED",
        created_by_user_id=created_by_user_id,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    return ResolvedLocation(location=location, matched_by="CREATED")


def resolve_or_create_manual(
    session: Session,
    *,
    name: Any,
    city: Any = None,
    sub_city: Any = None,
    landmark: Any = None,
    created_by_user_id: int | None = None,
    allow_similar: bool = True,
) -> ResolvedLocation:
    """Register a place Geoapify could not find, or return the existing one.

    Order matters: the exact/alias check happens *before* validation of the
    optional fields, so re-submitting a spelling we already know resolves to
    the row we already have instead of failing on a detail of the form.

    ``allow_similar`` decides what happens to a near miss. With it on (the
    user-facing default) a close existing match is returned as a suggestion
    for the user to confirm; with it off the caller is an admin, who means it.
    """
    clean_name, clean_city, clean_sub_city, clean_landmark = _validate_manual_fields(
        name, city, sub_city, landmark
    )
    normalized = normalize_name(clean_name)

    existing = find_by_normalized_name(session, normalized or "", clean_city)
    if existing is not None:
        return ResolvedLocation(location=existing, is_suggestion=True, matched_by="EXACT")

    aliased = find_by_alias(session, normalized or "")
    if aliased is not None:
        return ResolvedLocation(location=aliased, is_suggestion=True, matched_by="ALIAS")

    if allow_similar:
        near = find_equivalent(session, clean_name, city=clean_city)
        if near is not None:
            return ResolvedLocation(location=near[0], is_suggestion=True, matched_by=near[1])

    location = _insert_location(
        session,
        name=clean_name,
        normalized_name=normalized or clean_name.casefold(),
        city=clean_city,
        sub_city=clean_sub_city,
        landmark=clean_landmark,
        # No coordinates: a typed place has no position until someone
        # establishes one. Scoring reports proximity as uncertain instead.
        latitude=None,
        longitude=None,
        provider="MANUAL",
        provider_place_id=None,
        verification_status="PENDING",
        created_by_user_id=created_by_user_id,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    logger.info(
        "Manual location registered",
        extra={"location_id": location.id, "city": location.city},
    )
    return ResolvedLocation(location=location, matched_by="CREATED")


def seed_registry(session: Session) -> int:
    """Insert the curated registry as ``REGISTRY`` rows. Returns rows created.

    Runs on every ``init_db``. Existing rows are left exactly as they are, so
    an admin who has approved, rejected, merged or annotated a registry place
    keeps that work across restarts — this only ever adds what is missing.
    """
    created = 0
    for key, spec in location_service.LOCATIONS.items():
        if find_by_normalized_name(session, key) is not None:
            continue
        zone = spec.zone
        # A city entry names its own city; every Addis sub-area implies Addis
        # Ababa, which is how the matcher has always read these.
        city = (
            spec.name
            if zone
            in (location_service.LocationZone.CITY_WIDE, location_service.LocationZone.OUTSIDE)
            else COUNTRY_ETHIOPIA_DEFAULT_CITY
        )
        location = Location(
            name=spec.name[:_MAX_NAME_LENGTH],
            normalized_name=key[:_MAX_NAME_LENGTH],
            city=city[:_MAX_CITY_LENGTH],
            sub_city=None,
            landmark=None,
            latitude=None,
            longitude=None,
            provider="REGISTRY",
            provider_place_id=None,
            # The curated set is reviewed by hand and maintained in source, so
            # it is not queued for a second review.
            verification_status="VERIFIED",
            created_by_user_id=None,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(location)
        session.flush()
        created += 1

        for alias in spec.aliases:
            normalized_alias = normalize_name(alias)
            if not normalized_alias or normalized_alias == key:
                continue
            if find_by_alias(session, normalized_alias) is not None:
                continue
            session.add(
                LocationAlias(
                    location_id=location.id,
                    alias=alias[:_MAX_NAME_LENGTH],
                    normalized_alias=normalized_alias[:_MAX_NAME_LENGTH],
                    created_at=utcnow(),
                )
            )
    session.flush()
    return created


# ---------------------------------------------------------------------------
# Admin actions
# ---------------------------------------------------------------------------


def set_verification_status(
    session: Session, location_id: int, status: str, *, reason: str | None = None
) -> Location:
    """Approve or reject a location.

    The status must already be one the caller is allowed to set: this function
    never decides *who* may call it. User-facing code paths never reach it —
    they create ``PENDING`` rows through :func:`resolve_or_create_manual`.
    """
    value = str(status or "").strip().upper()
    if value not in LOCATION_VERIFICATION_STATUSES:
        raise LocationError(f"Unknown verification status: {status}")

    location = require_location(session, location_id)
    if location.is_merged and value == "VERIFIED":
        raise LocationError(
            "That location was merged into another one — review the surviving "
            "location instead."
        )
    location.verification_status = value
    location.updated_at = utcnow()
    if reason:
        # The reason is kept where an admin will find it, without adding a
        # column for it: a rejected place's own landmark/sub-city fields are
        # for the user's data, so it goes on the alias of record.
        logger.info(
            "Location review decision recorded",
            extra={"location_id": location.id, "status": value, "reason": reason},
        )
    session.flush()
    return location


def approve_location(session: Session, location_id: int) -> Location:
    """Mark a location verified. An admin action only."""
    return set_verification_status(session, location_id, "VERIFIED")


def reject_location(session: Session, location_id: int, reason: str | None = None) -> Location:
    """Mark a location invalid or misleading. It stays selectable afterwards."""
    return set_verification_status(session, location_id, "REJECTED", reason=reason)


def add_alias(session: Session, location: Location | int, alias: Any) -> LocationAlias:
    """Record a verified alternative spelling for a location.

    Repointing instead of duplicating when the spelling already belongs to
    another location is what an admin means by "this is the same place", so the
    existing alias is moved rather than left to collide.
    """
    text = " ".join(str(alias or "").split())
    if not text:
        raise LocationError("Please type the spelling to add.")
    if len(text) > _MAX_NAME_LENGTH:
        raise LocationError(
            f"That spelling is too long — keep it under {_MAX_NAME_LENGTH} characters."
        )

    target = require_location(session, location)
    normalized = normalize_name(text)
    if not normalized:
        raise LocationError("Please type the spelling to add.")
    if normalized == target.normalized_name:
        raise LocationError("That is already the location's name.")

    owner = find_by_alias(session, normalized)
    if owner is not None and owner.id != target.id:
        raise LocationError(
            f'That spelling already belongs to "{owner.name}". '
            "Merge the two locations instead."
        )

    existing = session.scalars(
        select(LocationAlias).where(LocationAlias.normalized_alias == normalized)
    ).first()
    if existing is not None:
        existing.location_id = target.id
        existing.alias = text
        session.flush()
        return existing

    row = LocationAlias(
        location_id=target.id,
        alias=text,
        normalized_alias=normalized,
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    target.updated_at = utcnow()
    return row


def parse_coordinates(value: Any) -> tuple[float, float]:
    """Read ``"lat, lon"`` and confirm it is inside Ethiopia.

    An admin is only offered this because a position must be *established*,
    not estimated, so an out-of-country pair is refused rather than stored.
    """
    text = str(value or "").strip().replace(";", ",").replace(" ", ",")
    parts = [part for part in text.split(",") if part]
    if len(parts) != 2:
        raise LocationError("Send the coordinates as latitude, longitude.")
    try:
        latitude, longitude = float(parts[0]), float(parts[1])
    except ValueError:
        raise LocationError("Those do not look like coordinates.") from None
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise LocationError("Those coordinates are outside the valid range.")

    lat_low, lat_high, lon_low, lon_high = ETHIOPIA_BOUNDS
    if not (lat_low <= latitude <= lat_high and lon_low <= longitude <= lon_high):
        raise LocationError("Those coordinates are not in Ethiopia.")
    return latitude, longitude


def set_coordinates(
    session: Session, location_id: int, latitude: float, longitude: float
) -> Location:
    """Attach a position to a location, once it is known to be right."""
    location = require_location(session, location_id)
    location.latitude = float(latitude)
    location.longitude = float(longitude)
    location.updated_at = utcnow()
    session.flush()
    logger.info("Location coordinates set", extra={"location_id": location.id})
    return location


def suggest_merge_targets(
    session: Session, source: Location, *, limit: int = 10
) -> list[Location]:
    """Locations a duplicate could be merged into, best first.

    Excludes the source itself and anything already merged away, so a
    half-finished review cannot be applied twice.
    """
    candidates = search_locations(session, source.normalized_name, limit=limit + 5)
    targets = [item for item in candidates if item.id != source.id and not item.is_merged]
    if not targets:
        # The name matches nothing else; show the city's other places so an
        # admin can still spot the duplicate by eye.
        targets = [
            item
            for item in list_locations(session, limit=limit * 3)
            if item.id != source.id
            and not item.is_merged
            and item.city.casefold() == source.city.casefold()
        ]
    return targets[:limit]


def merge_locations(
    session: Session,
    source_id: int,
    target_id: int,
    *,
    alias: str | None = None,
) -> Location:
    """Fold ``source`` into ``target``, keeping every reference.

    Runs as one nested transaction so a failure part-way through leaves both
    locations exactly as they were — a merge that lost a tutor link would be
    worse than no merge at all.

    The source row is kept as a ``REJECTED`` tombstone with
    ``merged_into_id`` pointing at the survivor, so a report generated before
    the merge still resolves to a real location.
    """
    if int(source_id) == int(target_id):
        raise LocationError("A location cannot be merged into itself.")

    source = require_location(session, source_id)
    target = require_location(session, target_id)
    if source.is_merged:
        raise LocationError(
            f'"{source.name}" was already merged into '
            f'"{_merged_name(session, source)}".'
        )
    if target.is_merged:
        raise LocationError(
            f'"{target.name}" was itself merged elsewhere — pick a location that '
            "is still current."
        )

    with session.begin_nested():
        if alias:
            add_alias(session, target.id, alias)
        elif source.name and source.name.casefold() != target.name.casefold():
            add_alias(session, target.id, source.name)

        _repoint_tutor_locations(session, source, target)
        _repoint_job_locations(session, source, target)
        _reassign_aliases(session, source, target)

        # Coordinates: keep the survivor's, or take the source's if the
        # survivor has none. Never overwrite a position with a null one.
        if not target.has_coordinates and source.has_coordinates:
            target.latitude = source.latitude
            target.longitude = source.longitude

        source.verification_status = "REJECTED"
        source.merged_into_id = target.id
        source.updated_at = utcnow()
        target.updated_at = utcnow()
        session.flush()

    logger.info(
        "Locations merged",
        extra={"source_id": source.id, "target_id": target.id},
    )
    return target


def _reassign_aliases(session: Session, source: Location, target: Location) -> None:
    """Move the merged location's spellings onto the survivor.

    Without this, a spelling a user or an admin had already confirmed would
    simply vanish from search: the row it pointed at is now a tombstone, and
    nobody could find that place by the name they were told to use.

    A spelling that already belongs to the survivor is left where it is rather
    than deleted, so an alias an admin added deliberately survives the merge.
    """
    for alias in list(source.aliases):
        clash = session.scalars(
            select(LocationAlias).where(
                LocationAlias.normalized_alias == alias.normalized_alias,
                LocationAlias.location_id == target.id,
            )
        ).first()
        if clash is None:
            alias.location_id = target.id
    session.flush()


def _merged_name(session: Session, location: Location) -> str:
    survivor = get_location(session, location.merged_into_id)
    return survivor.name if survivor is not None else "another location"


def _repoint_tutor_locations(
    session: Session, source: Location, target: Location
) -> None:
    """Move tutor links to the survivor, collapsing duplicates.

    ``uq_tutor_location`` is on ``(tutor_id, location)``, so a tutor linked to
    both ends of a merge needs its two rows collapsed into one. The surviving
    row keeps confirmation, the earlier ``created_at``, and the merged-in
    source as its displayed text when the tutor had no row of their own.
    """
    from app.models import TutorLocation

    rows = list(
        session.scalars(select(TutorLocation).where(TutorLocation.location_id == source.id))
    )
    for row in rows:
        # Two rows for one tutor that would land on the same canonical location
        # have to collapse, or a tutor who was linked to both ends of a merge
        # is now recorded as covering that area twice. ``uq_tutor_location`` is
        # on the *text*, so the database will not catch this on its own.
        twin = session.scalars(
            select(TutorLocation).where(
                TutorLocation.tutor_id == row.tutor_id,
                TutorLocation.location_id == target.id,
                TutorLocation.id != row.id,
            )
        ).first()
        if twin is None:
            row.location_id = target.id
            row.source = row.source or "IMPORTED"
            continue

        # Keep the row that has been there longest: it carries the tutor's own
        # original spelling and the earliest ``created_at``.
        if twin.created_at and row.created_at and row.created_at < twin.created_at:
            row, twin = twin, row
        row.location_id = target.id
        twin.location_id = target.id
        twin.is_confirmed = bool(twin.is_confirmed or row.is_confirmed)
        if not twin.source and row.source:
            twin.source = row.source
        session.delete(row)


def _repoint_job_locations(session: Session, source: Location, target: Location) -> None:
    """Point jobs and parent requests at the survivor.

    Only ``location_id`` moves. The text each poster typed is left exactly as
    they wrote it — that is the audit trail, and the job template renders it.
    """
    from app.models import JobPost, StudentRequest

    for post in session.scalars(
        select(JobPost).where(JobPost.location_id == source.id)
    ):
        post.location_id = target.id
    for request in session.scalars(
        select(StudentRequest).where(StudentRequest.location_id == source.id)
    ):
        request.location_id = target.id


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def verification_badge(location: Location) -> str:
    """A short, honest marker for wherever a location is displayed.

    A manually entered location is never shown as confirmed: a tutor or parent
    who typed "Woreda 12" must not be told it is an official place.
    """
    if location.is_rejected:
        return "❌ rejected"
    if location.is_merged:
        return "🔁 merged"
    if not location.is_verified:
        return "⚠️ unverified — awaiting review"
    if location.provider == "GEOAPIFY":
        return "✅ verified place"
    return "✅ verified"


def describe(location: Location) -> str:
    """One-line label plus its provenance, for admin and selection views."""
    parts = [location.name]
    if location.sub_city:
        parts.append(location.sub_city)
    parts.append(location.city)
    if location.landmark:
        parts.append(f"near {location.landmark}")
    return " — ".join(parts)


def unverified_label(location: Location | None) -> str:
    """A marker for a job whose location has not been reviewed.

    Used on job posts and match cards so nobody assigns an in-person tutor to
    an address the platform has never confirmed.
    """
    if location is None:
        return ""
    if location.is_verified:
        return ""
    return " ⚠️ location unverified — please confirm before assigning"


def location_or_none(value: Any) -> Location | None:
    """Coerce a row or ``None`` to a :class:`Location`, never raising."""
    return value if isinstance(value, Location) else None


def haversine_km(
    latitude_a: float | None,
    longitude_a: float | None,
    latitude_b: float | None,
    longitude_b: float | None,
) -> float | None:
    """Great-circle distance in kilometres, or ``None`` if either is unknown.

    Re-exported from :mod:`app.services.location_service`, which owns the
    geography; this alias exists so callers that already import the registry
    service do not need a second import.
    """
    return location_service.haversine_km(
        latitude_a, longitude_a, latitude_b, longitude_b
    )


def summarize(session: Session) -> dict[str, int]:
    """Counts for the admin dashboard."""
    counts: dict[str, int] = {"total": 0, "pending": 0, "verified": 0, "rejected": 0}
    for row in session.execute(
        select(Location.verification_status, func.count()).group_by(
            Location.verification_status
        )
    ):
        status, total = row
        counts["total"] += int(total)
        key = str(status).lower()
        if key in counts:
            counts[key] = int(total)
    return counts