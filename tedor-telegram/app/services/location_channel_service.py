"""Import curated tutoring locations from the Telegram location channel.

The channel is the **editable source of truth**: an administrator posts a
neighbourhood once, and edits that post when a name or a coordinate changes.
This module mirrors those posts into the ``locations`` table, which is the
queryable index that matching reads. Nothing in the platform ever calls
Telegram to answer "where is this place?" — that is the whole point, and it is
what keeps a job post from depending on network latency.

Why a listener plus a file import, and not ``getChatHistory``
------------------------------------------------------------

Telegram's Bot API has **no method that returns a channel's message history**.
``getChatHistory`` does not exist in any published Bot API version, so a normal
bot token cannot be used to read posts that are already in the channel. What a
bot *can* receive is ``channel_post`` / ``edited_channel_post`` updates, but
only for posts made after it was made an administrator. Silently assuming
otherwise is the classic way to end up with an empty directory and a working
importer, so the two halves are separated and named honestly:

``sync_messages``
    Future posts and edits, delivered as updates to the running bot. No user
    session, no privileges beyond "administrator of the channel".

:func:`import_export_file`
    A one-time backfill of posts that already exist. It reads a
    `Telegram Desktop export <https://desktop.telegram.org>`_ JSON file, which
    the administrator produces from the channel they own. No personal account
    session is involved and no Telethon/Pyrogram dependency is introduced, so
    the platform keeps exactly one Telegram client, the bot it already runs.

Both halves call the same :func:`sync_message`, so a record behaves identically
however it arrived.

Record format
-------------

One record per channel post, key/value lines, order-independent::

    ID: AA-0001
    Name: Bole
    Aliases: Bole, Bole Medhanialem, Bole Kerfe
    Latitude: 8.9987
    Longitude: 38.7890
    Sub City: Bole
    Woreda: 03
    Status: verified

Keys are case-insensitive and separators are ignored, so ``Name:`` and
``name =`` both work. Every field except ``ID`` and ``Name`` is optional.

The rules the format exists to enforce:

* ``ID`` is **required** and is the stable key. Editing a post keeps the same
  ``ID`` and updates one row in place. A post with a missing or malformed ``ID``
  is rejected with an explanation rather than guessed at, because inventing a
  key is how one neighbourhood becomes two rows.
* ``Latitude`` and ``Longitude`` are both-or-neither. A record with neither is
  stored without coordinates and matching reports its proximity as uncertain.
  **Coordinates are never inferred** — a missing coordinate stays missing, and
  a coordinate the administrator did not write is never filled in from a
  geocoder behind their back.
* ``Status`` is ``verified`` or ``pending``, defaulting to ``pending``.

Deletion
--------

Deleting a channel post produces no Telegram update, so a live bot cannot
observe it. :func:`reconcile` handles it during a backfill: rows whose source
message is absent from a fresh export are **deactivated**
(:attr:`~app.models.Location.is_active`), never deleted, because a location may
already be referenced by a tutor's tutoring areas and by a published job.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    LOCATION_PROVIDER_CHANNEL,
    Location,
    LocationAlias,
    utcnow,
)
from app.services import location_registry_service, location_service

logger = logging.getLogger(__name__)

__all__ = [
    "CHANNEL_STATUS_DISABLED",
    "CHANNEL_STATUS_PENDING",
    "CHANNEL_STATUS_VERIFIED",
    "ETHIOPIA_BOUNDS",
    "ImportSummary",
    "LocationChannelError",
    "LocationRecord",
    "RecordError",
    "RejectedRecord",
    "SyncOutcome",
    "format_summary",
    "import_export_file",
    "iter_export_messages",
    "location_code",
    "parse_record",
    "reconcile",
    "sync_message",
    "sync_messages",
]

#: Ethiopia's bounding box as ``(min_latitude, max_latitude, min_longitude,
#: max_longitude)``. A record outside it is almost certainly a typo — "51.5,
#: -0.1" is London — so it is rejected loudly instead of quietly poisoning
#: every distance calculation.
ETHIOPIA_BOUNDS = (3.0, 15.0, 32.0, 49.0)

CHANNEL_STATUS_VERIFIED = "verified"
CHANNEL_STATUS_PENDING = "pending"
#: Not a verification status: a way for the administrator to withdraw a place
#: without deleting the post that tutors and jobs already point at.
CHANNEL_STATUS_DISABLED = "disabled"

#: ``AA-0001``. Two letters, a hyphen, at least three digits.
CODE_PATTERN = re.compile(r"^[A-Z]{2}-\d{3,}$")

#: Accepted spellings for each field. Keys are matched after lowercasing and
#: stripping everything that is not a letter, so ``Sub-City`` and ``sub_city``
#: both land on ``subcity``.
_KEY_ALIASES: dict[str, str] = {
    "id": "id",
    "code": "id",
    "locationid": "id",
    "locationcode": "id",
    "name": "name",
    "location": "name",
    "aliases": "aliases",
    "alias": "aliases",
    "alternatenames": "aliases",
    "aliasnames": "aliases",
    "latitude": "latitude",
    "lat": "latitude",
    "longitude": "longitude",
    "lon": "longitude",
    "lng": "longitude",
    "long": "longitude",
    "subcity": "sub_city",
    "sub_city": "sub_city",
    "sube_city": "sub_city",
    "woreda": "woreda",
    "district": "woreda",
    "status": "status",
    "state": "status",
    "verification": "status",
    "verificationstatus": "status",
}

_MAX_NAME = 160
_MAX_ALIAS = 160
_MAX_TEXT = 120
_MAX_ALIASES = 25

_SEPARATOR = re.compile(r"[,;\n]+")


class LocationChannelError(RuntimeError):
    """The channel could not be synchronised at all."""


class RecordError(ValueError):
    """One channel record is not usable.

    ``user_message`` is written for the administrator and is safe to send back
    into Telegram as-is: it says what is wrong and, where possible, how to fix
    it.
    """

    def __init__(self, message: str, *, field_name: str | None = None) -> None:
        super().__init__(message)
        self.user_message = message
        self.field_name = field_name


def location_code(value: object) -> str | None:
    """Normalise a location code to ``AA-0001`` form, or ``None`` if invalid."""
    if value is None:
        return None
    text = str(value).strip().upper().replace(" ", "")
    if not CODE_PATTERN.match(text):
        return None
    return text


def _canonical_key(raw_key: str) -> str | None:
    folded = re.sub(r"[^a-z]", "", raw_key.strip().lower())
    return _KEY_ALIASES.get(folded)


def _split_field(line: str) -> tuple[str, str, str]:
    """Split ``Key: value`` or ``Key = value`` into its three parts.

    Either separator is accepted so a record pasted from a spreadsheet row or a
    YAML-ish note still imports. Whichever comes first wins, which keeps a value
    containing the other separator (``Aliases: Bole, near: me``) intact.
    """
    colon = line.find(":")
    equals = line.find("=")
    positions = [index for index in (colon, equals) if index > 0]
    if not positions:
        return line, "", ""
    index = min(positions)
    return line[:index], line[index], line[index + 1 :]


@dataclass(frozen=True)
class LocationRecord:
    """A validated channel record, ready to be mirrored into the database."""

    id: str
    name: str
    aliases: tuple[str, ...] = ()
    latitude: float | None = None
    longitude: float | None = None
    sub_city: str | None = None
    woreda: str | None = None
    status: str = CHANNEL_STATUS_PENDING
    #: Telegram provenance, absent for a record read from a file.
    chat_id: int | None = None
    message_id: int | None = None
    #: Keys that were present but not understood, so a typo is reported rather
    #: than silently dropped.
    unknown_keys: tuple[str, ...] = ()
    #: Aliases that folded onto the place's own canonical key and so were not
    #: stored. Reported rather than dropped silently: "Bole Medhanialem" is a
    #: deliberate synonym of "Bole" in the curated registry, and an
    #: administrator who typed it deserves to be told it changed nothing.
    redundant_aliases: tuple[str, ...] = ()

    @property
    def wants_verified(self) -> bool:
        """Whether the post asks for this place to be treated as verified.

        Honours ``LOCATION_CHANNEL_TRUST_VERIFIED``: with it off, a channel
        post can only ever *create* a pending location and the in-bot review
        queue is the only route to verified, exactly as for a manually typed
        one.
        """
        if not settings.location_channel_trust_verified:
            return False
        return self.status == CHANNEL_STATUS_VERIFIED

    @property
    def is_disabled(self) -> bool:
        return self.status == CHANNEL_STATUS_DISABLED

    @property
    def normalized_name(self) -> str:
        return location_service.normalize_location(self.name) or ""

    @property
    def city(self) -> str:
        """Channel records describe Addis Ababa; other cities are not curated."""
        return "Addis Ababa"


@dataclass(frozen=True)
class RejectedRecord:
    """Why one record did not make it into the database."""

    reason: str
    chat_id: int | None = None
    message_id: int | None = None
    excerpt: str = ""

    def describe(self) -> str:
        where = ""
        if self.chat_id is not None and self.message_id is not None:
            where = f" [chat {self.chat_id}, message {self.message_id}]"
        body = self.excerpt.strip().splitlines()[0] if self.excerpt.strip() else ""
        return f"{self.reason}{where}" + (f" — “{body[:80]}”" if body else "")


@dataclass
class SyncOutcome:
    """The result of mirroring one channel record."""

    record: LocationRecord
    location: Location | None
    #: ``created`` | ``updated`` | ``unchanged`` | ``linked`` | ``deactivated``
    action: str
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.action != "unchanged"


@dataclass
class ImportSummary:
    """Administrator-visible tally of one synchronisation run."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    linked: int = 0
    deactivated: int = 0
    #: Posts in the export that a newer live channel update had already
    #: superseded, and which were therefore left alone.
    stale_skipped: int = 0
    rejected: list[RejectedRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            self.created
            + self.updated
            + self.unchanged
            + self.linked
            + self.deactivated
            + self.stale_skipped
            + len(self.rejected)
        )

    @property
    def accepted(self) -> int:
        return self.created + self.updated + self.unchanged + self.linked

    def absorb(self, outcome: SyncOutcome) -> None:
        setattr(self, outcome.action, getattr(self, outcome.action) + 1)
        self.warnings.extend(outcome.notes)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _parse_optional_number(raw: str, field_name: str) -> float | None:
    text = raw.strip().replace("°", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise RecordError(
            f"{field_name} must be a number, got “{raw.strip()}”.",
            field_name=field_name,
        ) from exc


def parse_record(
    text: str,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> LocationRecord:
    """Parse and validate one channel post into a :class:`LocationRecord`.

    Raises :class:`RecordError` — and only that — for anything unusable. It
    never raises for an unrelated exception, because a malformed post from the
    channel must not be able to stop the bot.
    """
    if not isinstance(text, str) or not text.strip():
        raise RecordError("The message is empty.", field_name="text")

    fields: dict[str, str] = {}
    unknown: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key_text, separator, value = _split_field(stripped)
        if not separator:
            # Not a key/value line. A heading or a sentence of commentary is
            # tolerated; only the fields below are required.
            continue
        key = _canonical_key(key_text)
        value = _clean(value)
        if key is None:
            if value:
                unknown.append(f"{_clean(key_text)}: {value}")
            continue
        # Last one wins, so a corrected line appended below an earlier one
        # behaves the way an administrator editing in place expects.
        fields[key] = value

    raw_code = fields.get("id", "")
    code = location_code(raw_code)
    if code is None:
        if not raw_code:
            raise RecordError(
                "Missing required field “ID”. Every record must start with a "
                "code such as “ID: AA-0001”.",
                field_name="id",
            )
        raise RecordError(
            f"“{raw_code}” is not a valid location code. Use the form AA-0001.",
            field_name="id",
        )

    name = _clean(fields.get("name", ""))
    if not name:
        raise RecordError(
            f"Record {code} is missing required field “Name”.", field_name="name"
        )
    if len(name) > _MAX_NAME:
        raise RecordError(
            f"Record {code}: the name is longer than {_MAX_NAME} characters.",
            field_name="name",
        )

    latitude = _parse_optional_number(fields.get("latitude", ""), "Latitude")
    longitude = _parse_optional_number(fields.get("longitude", ""), "Longitude")
    if (latitude is None) != (longitude is None):
        raise RecordError(
            f"Record {code}: give both Latitude and Longitude, or neither. "
            "One alone cannot be used.",
            field_name="latitude" if latitude is None else "longitude",
        )
    if latitude is not None and longitude is not None:
        if not -90.0 <= latitude <= 90.0:
            raise RecordError(
                f"Record {code}: latitude {latitude} is outside -90…90.",
                field_name="latitude",
            )
        if not -180.0 <= longitude <= 180.0:
            raise RecordError(
                f"Record {code}: longitude {longitude} is outside -180…180.",
                field_name="longitude",
            )
        if latitude == 0.0 and longitude == 0.0:
            raise RecordError(
                f"Record {code}: 0, 0 is not a location. Leave both fields out "
                "if the position is unknown.",
                field_name="latitude",
            )
        if settings.location_channel_enforce_bounds and not _within_ethiopia(
            latitude, longitude
        ):
            raise RecordError(
                f"Record {code}: {latitude}, {longitude} is outside Ethiopia. "
                "Check the fields were not swapped.",
                field_name="latitude",
            )

    aliases: list[str] = []
    redundant: list[str] = []
    name_key = location_service.normalize_location(name) or ""
    seen_aliases: set[str] = {name_key}
    for chunk in _SEPARATOR.split(fields.get("aliases", "")):
        alias = _clean(chunk)
        if not alias:
            continue
        if len(alias) > _MAX_ALIAS:
            raise RecordError(
                f"Record {code}: the alias “{alias[:40]}…” is longer than "
                f"{_MAX_ALIAS} characters.",
                field_name="aliases",
            )
        # An alias identical to the name, or repeated in the same post, is
        # redundant rather than wrong: collapse it instead of rejecting the
        # record over a cosmetic detail.
        key = location_service.normalize_location(alias)
        if not key:
            continue
        if key in seen_aliases:
            redundant.append(alias)
            continue
        seen_aliases.add(key)
        aliases.append(alias)
        if len(aliases) >= _MAX_ALIASES:
            break

    status = _clean(fields.get("status", "")).lower() or CHANNEL_STATUS_PENDING
    if status not in {
        CHANNEL_STATUS_VERIFIED,
        CHANNEL_STATUS_PENDING,
        CHANNEL_STATUS_DISABLED,
    }:
        raise RecordError(
            f"Record {code}: “{status}” is not a valid status. Use one of "
            f"{CHANNEL_STATUS_VERIFIED}, {CHANNEL_STATUS_PENDING}, "
            f"{CHANNEL_STATUS_DISABLED}.",
            field_name="status",
        )

    return LocationRecord(
        id=code,
        name=name,
        aliases=tuple(aliases),
        latitude=latitude,
        longitude=longitude,
        sub_city=_optional_text(fields.get("sub_city"), _MAX_TEXT),
        woreda=_optional_text(fields.get("woreda"), _MAX_TEXT),
        status=status,
        chat_id=chat_id,
        message_id=message_id,
        unknown_keys=tuple(unknown),
        redundant_aliases=tuple(redundant),
    )


def _optional_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = _clean(value)
    if not cleaned:
        return None
    return cleaned[:limit]


def _as_datetime(value: Any) -> datetime | None:
    """A Telegram export's ISO-8601 ``date``, or ``None`` if unreadable.

    An export entry without a usable date is simply not stale-protected; that
    degrades to importing it, which is the pre-existing behaviour.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _superseded_by_live_sync(
    session: Session, message_id: int, posted_at: datetime
) -> bool:
    """Whether a live update already moved this row past ``posted_at``."""
    rows = session.scalars(
        select(Location).where(
            Location.source_chat_id.is_not(None),
            Location.source_message_id == message_id,
        )
    ).all()
    for row in rows:
        synced_at = row.synced_at
        if synced_at is None:
            continue
        if synced_at.tzinfo is None:
            synced_at = synced_at.replace(tzinfo=timezone.utc)
        if synced_at > posted_at:
            logger.info(
                "Skipping an exported location post superseded by a live sync",
                extra={"location_code": row.location_code, "message_id": message_id},
            )
            return True
    return False


def _target_status(record: LocationRecord) -> str:
    """The stored verification status a record asks for.

    ``disabled`` always wins: withdrawing a place matters more than whether it
    was ever verified. The stored vocabulary is the uppercase one in
    ``app.models``; the channel's lowercase words are only the wire format.
    """
    if record.is_disabled:
        return "REJECTED"
    return "VERIFIED" if record.wants_verified else "PENDING"


def _within_ethiopia(latitude: float, longitude: float) -> bool:
    min_lat, max_lat, min_lon, max_lon = ETHIOPIA_BOUNDS
    return min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon


# ---------------------------------------------------------------------------
# Writing to the database
# ---------------------------------------------------------------------------


def _find_by_code(session: Session, code: str) -> Location | None:
    return session.scalar(
        select(Location).where(Location.location_code == code)
    )


def _find_by_source(session: Session, chat_id: int, message_id: int) -> Location | None:
    return session.scalar(
        select(Location).where(
            Location.source_chat_id == chat_id,
            Location.source_message_id == message_id,
        )
    )


def _find_by_name(session: Session, normalized: str, city: str) -> Location | None:
    return session.scalar(
        select(Location).where(
            Location.normalized_name == normalized, Location.city == city
        )
    )


def _alias_owner(session: Session, normalized: str) -> Location | None:
    return session.scalar(
        select(Location)
        .join(LocationAlias, LocationAlias.location_id == Location.id)
        .where(LocationAlias.normalized_alias == normalized)
    )


def _check_alias_collisions(
    session: Session, record: LocationRecord, target: Location | None
) -> None:
    """Reject a record whose alias is another location's name or alias.

    An alias is a claim that two spellings mean one place. Letting two
    locations claim the same spelling would make resolution depend on which
    row the query happened to reach first, so it is refused up front rather
    than left for an admin to unpick later.
    """
    for alias in record.aliases:
        normalized = location_service.normalize_location(alias) or ""
        if not normalized:
            continue
        owner = _alias_owner(session, normalized)
        if owner is not None and (target is None or owner.id != target.id):
            raise RecordError(
                f"Record {record.id}: the alias “{alias}” is already an alias "
                f"of “{owner.name}”. Merge them in the admin console instead.",
                field_name="aliases",
            )
        clash = _find_by_name(session, normalized, record.city)
        if clash is not None and (target is None or clash.id != target.id):
            if location_service.normalize_location(clash.name) != normalized:
                continue
            raise RecordError(
                f"Record {record.id}: the alias “{alias}” is the name of "
                f"“{clash.name}”. Merge them in the admin console instead.",
                field_name="aliases",
            )


def _existing_code_conflict(
    session: Session, record: LocationRecord, target: Location | None
) -> None:
    """Refuse a code that another location already carries under a new name.

    Re-posting a record with a typo'd code would otherwise mint a second row
    for one place, which is exactly the fork this module exists to prevent.
    """
    clash = _find_by_code(session, record.id)
    if clash is not None and target is not None and clash.id != target.id:
        raise RecordError(
            f"Location code {record.id} already belongs to “{clash.name}”. "
            "Each place keeps one code for its whole life.",
            field_name="id",
        )


def _apply_aliases(session: Session, record: LocationRecord, location: Location) -> None:
    """Make this record's aliases resolve to ``location`` and nothing else.

    Aliases removed from the post are removed here too, so the directory
    mirrors the channel rather than accumulating history. The canonical name is
    always protected — it is not an alias and is never dropped by a sync.
    """
    wanted = {
        location_registry_service.normalize_name(alias) or "" for alias in record.aliases
    }
    wanted.discard("")
    wanted.discard(location.normalized_name)

    existing = list(location.aliases)
    for alias_row in existing:
        if alias_row.normalized_alias not in wanted:
            others = session.scalar(
                select(LocationAlias).where(
                    LocationAlias.normalized_alias == alias_row.normalized_alias,
                    LocationAlias.location_id != location.id,
                )
            )
            if others is None:
                session.delete(alias_row)
    session.flush()

    for alias in record.aliases:
        normalized = location_registry_service.normalize_name(alias) or ""
        if not normalized or normalized == location.normalized_name:
            continue
        if normalized in wanted and not any(
            row.normalized_alias == normalized for row in existing
        ):
            location_registry_service.add_alias(session, location, alias)


def _write_record(
    session: Session,
    record: LocationRecord,
    previous: Location,
) -> tuple[Location, bool]:
    """Apply a record to an existing row. Returns ``(row, anything_changed)``."""
    changed = False
    if previous.name != record.name:
        previous.name = record.name[:_MAX_NAME]
        previous.normalized_name = record.normalized_name
        changed = True
    if previous.sub_city != record.sub_city:
        previous.sub_city = record.sub_city
        changed = True
    if previous.woreda != record.woreda:
        previous.woreda = record.woreda
        changed = True
    if previous.latitude != record.latitude or previous.longitude != record.longitude:
        # Coordinates are replaced, never averaged, and a field the
        # administrator removed leaves the row without coordinates again.
        previous.latitude = record.latitude
        previous.longitude = record.longitude
        changed = True
    if previous.provider != LOCATION_PROVIDER_CHANNEL:
        previous.provider = LOCATION_PROVIDER_CHANNEL
        changed = True
    # A code is assigned once and then belongs to that place for life. Adopting
    # an existing row therefore never steals its code: two posts claiming one
    # neighbourhood converge on the first code rather than the second one
    # silently replacing it.
    if previous.location_code is None:
        previous.location_code = record.id
        changed = True
    if record.chat_id is not None and previous.source_chat_id != record.chat_id:
        previous.source_chat_id = record.chat_id
        changed = True
    if record.message_id is not None and previous.source_message_id != record.message_id:
        previous.source_message_id = record.message_id
        changed = True
    if previous.is_active != (not record.is_disabled):
        previous.is_active = not record.is_disabled
        changed = True

    wanted_status = _target_status(record)
    if previous.verification_status != wanted_status:
        previous.verification_status = wanted_status
        changed = True

    _apply_aliases(session, record, previous)

    previous.synced_at = utcnow()
    previous.updated_at = utcnow()
    return previous, changed


def sync_message(
    session: Session,
    text: str,
    *,
    chat_id: int,
    message_id: int,
    record: LocationRecord | None = None,
) -> SyncOutcome:
    """Mirror one channel post into the database.

    Idempotent: importing the same post again — which is what an edited
    message, a repeated backfill, or a bot restart all look like — updates the
    same row and creates nothing new.

    Raises :class:`RecordError` for an unusable record. Callers that import
    many records should use :func:`sync_messages`, which collects rejections
    instead of stopping at the first one.
    """
    parsed = record or parse_record(text, chat_id=chat_id, message_id=message_id)
    notes: list[str] = []
    if parsed.unknown_keys:
        notes.append(
            f"{parsed.id}: ignored unrecognised field(s) "
            f"{'; '.join(parsed.unknown_keys)}."
        )
    if parsed.redundant_aliases:
        notes.append(
            f"{parsed.id}: “{'”, “'.join(parsed.redundant_aliases)}” already "
            "resolves to this place and was not stored as an extra alias."
        )

    normalized = parsed.normalized_name
    if not normalized:
        raise RecordError(
            f"Record {parsed.id}: the name “{parsed.name}” has no letters or "
            "digits in it.",
            field_name="name",
        )

    existing = _find_by_code(session, parsed.id)
    if existing is None:
        existing = _find_by_source(session, chat_id, message_id)

    action = "updated"
    if existing is None:
        # An existing row for the same place — usually a seeded REGISTRY row,
        # but possibly one a tutor or job already points at — is adopted
        # rather than duplicated. This is what lets curated coordinates join
        # an existing place without a second row appearing in search.
        by_name = _find_by_name(session, normalized, parsed.city)
        if by_name is not None:
            existing = by_name
            action = "linked"
        else:
            _check_alias_collisions(session, parsed, None)
            created = _create_record(session, parsed)
            return SyncOutcome(
                record=parsed, location=created, action="created", notes=notes
            )

    _existing_code_conflict(session, parsed, existing)
    _check_alias_collisions(session, parsed, existing)

    before = _snapshot(existing)
    was_linked = action == "linked"
    row, changed = _write_record(session, parsed, existing)
    session.flush()
    # "Unchanged" is only claimed when the database row genuinely did not move.
    # ``synced_at`` is deliberately excluded from the comparison: it advances on
    # every run by design, and treating that as a change would make every
    # import look like an edit.
    if was_linked:
        # "Linked" is the more useful fact — the record joined a place that was
        # already in the directory — so it is reported whether or not any
        # field also moved.
        action = "linked"
    elif not changed and _snapshot(row) == before:
        action = "unchanged"
    return SyncOutcome(
        record=parsed, location=row, action=action, notes=notes
    )


def _snapshot(row: Location) -> tuple[Any, ...]:
    """The fields a sync owns, for change detection."""
    return (
        row.name,
        row.normalized_name,
        row.city,
        row.sub_city,
        row.woreda,
        row.latitude,
        row.longitude,
        row.provider,
        row.verification_status,
        row.location_code,
        row.source_chat_id,
        row.source_message_id,
        row.is_active,
        tuple(sorted(alias.normalized_alias for alias in row.aliases)),
    )


def _create_record(session: Session, record: LocationRecord) -> Location:
    location = Location(
        name=record.name[:_MAX_NAME],
        normalized_name=record.normalized_name,
        city=record.city,
        sub_city=record.sub_city,
        woreda=record.woreda,
        latitude=record.latitude,
        longitude=record.longitude,
        provider=LOCATION_PROVIDER_CHANNEL,
        provider_place_id=None,
        verification_status=_target_status(record),
        location_code=record.id,
        source_chat_id=record.chat_id,
        source_message_id=record.message_id,
        is_active=not record.is_disabled,
        synced_at=utcnow(),
    )
    if record.is_disabled:
        location.verification_status = "REJECTED"
    session.add(location)
    session.flush()
    _apply_aliases(session, record, location)
    return location


def sync_messages(
    session: Session,
    messages: Iterable[tuple[Any, ...]],
    *,
    chat_id: int,
    skip_stale: bool | None = None,
) -> ImportSummary:
    """Import many posts, collecting every rejection instead of stopping.

    ``messages`` is an iterable of ``(message_id, text)``, optionally with a
    third element carrying the post's date. One bad record must never prevent
    the rest from loading, so each is parsed inside its own ``try`` and a
    failure is recorded on the summary for the administrator.

    ``skip_stale`` defaults to ``LOCATION_CHANNEL_IMPORT_SKIP_STALE``. When on,
    a post older than the row's last live update is counted as unchanged and
    ignored, so re-running an old backfill cannot roll back a correction that
    arrived as a real ``edited_channel_post`` update afterwards.
    """
    if skip_stale is None:
        skip_stale = settings.location_channel_import_skip_stale
    summary = ImportSummary()
    for entry in messages:
        message_id, text = int(entry[0]), entry[1]
        posted_at = _as_datetime(entry[2]) if len(entry) > 2 else None
        try:
            if (
                skip_stale
                and posted_at is not None
                and _superseded_by_live_sync(session, message_id, posted_at)
            ):
                summary.stale_skipped += 1
                continue
            outcome = sync_message(
                session, text, chat_id=chat_id, message_id=message_id
            )
        except RecordError as exc:
            summary.rejected.append(
                RejectedRecord(
                    reason=exc.user_message,
                    chat_id=chat_id,
                    message_id=message_id,
                    excerpt=text if isinstance(text, str) else "",
                )
            )
            logger.warning(
                "Rejected channel record",
                extra={"chat_id": chat_id, "message_id": message_id, "reason": exc.user_message},
            )
        except SQLAlchemyError:
            # A constraint violation rolls back only this record, so one bad
            # row cannot abort the rest of the import.
            session.rollback()
            summary.rejected.append(
                RejectedRecord(
                    reason="The record conflicted with the database and was skipped.",
                    chat_id=chat_id,
                    message_id=message_id,
                    excerpt=text if isinstance(text, str) else "",
                )
            )
            logger.warning(
                "Channel record conflicted with the database",
                extra={"chat_id": chat_id, "message_id": message_id},
            )
        except Exception:  # pragma: no cover - defensive
            session.rollback()
            logger.exception(
                "Unexpected failure importing a channel record",
                extra={"chat_id": chat_id, "message_id": message_id},
            )
            summary.rejected.append(
                RejectedRecord(
                    reason="An unexpected error stopped this record from importing.",
                    chat_id=chat_id,
                    message_id=message_id,
                )
            )
        else:
            summary.absorb(outcome)
            if outcome.action == "unchanged":
                logger.debug("Channel record unchanged", extra={"code": outcome.record.id})
    return summary


# ---------------------------------------------------------------------------
# One-time backfill from a Telegram Desktop export
# ---------------------------------------------------------------------------


def iter_export_messages(path: str | Path) -> Iterator[tuple[int, str, Any]]:
    """Yield ``(message_id, text, posted_at)`` from a Telegram Desktop export.

    Accepts both shapes the export uses: the modern ``result.json`` (an object
    with ``messages``, each entry carrying ``id``, ``text`` and ``date``) and a
    bare list of the same entries. ``posted_at`` is the raw date value — a
    string or ``None`` — and is interpreted by :func:`_as_datetime` at the
    point of use.
    """
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        entries = payload.get("messages", [])
    elif isinstance(payload, list):
        entries = payload
    else:  # pragma: no cover - defensive
        raise LocationChannelError(
            f"{path} is not a Telegram export (expected an object or a list)."
        )
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if entry.get("type") not in (None, "message"):
            continue
        raw_id = entry.get("id")
        try:
            message_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        yield message_id, text, entry.get("date")


def import_export_file(
    session: Session,
    path: str | Path,
    *,
    chat_id: int,
    reconcile_missing: bool = False,
) -> ImportSummary:
    """Backfill locations from a channel export.

    ``reconcile_missing`` deactivates channel rows whose message is absent from
    the export, which is the only way a deleted post can be noticed — Telegram
    sends no update when a message is removed. Deactivation preserves the row
    so tutors and jobs that already reference it stay explainable.
    """
    messages = list(iter_export_messages(path))
    if not messages:
        raise LocationChannelError(
            f"{path} contained no text messages. Export the channel as JSON "
            "(not HTML) and try again."
        )
    summary = sync_messages(session, messages, chat_id=chat_id)
    if reconcile_missing:
        summary.deactivated += reconcile(
            session, seen_message_ids={message_id for message_id, _, _ in messages}
        )
    return summary


def reconcile(session: Session, *, seen_message_ids: set[int]) -> int:
    """Deactivate channel rows missing from a complete export.

    Never deletes. A row that is still referenced by a tutor or a job stays in
    the table with ``is_active=False`` so the reference remains resolvable and
    the history stays correct; it simply stops appearing in search results.
    """
    rows = session.scalars(
        select(Location).where(
            Location.provider == LOCATION_PROVIDER_CHANNEL,
            Location.is_active.is_(True),
        )
    ).all()
    deactivated = 0
    for row in rows:
        if row.source_message_id is None:
            continue
        if row.source_message_id in seen_message_ids:
            continue
        row.is_active = False
        row.updated_at = utcnow()
        deactivated += 1
        logger.info(
            "Deactivated location missing from the channel export",
            extra={"location_code": row.location_code, "location_name": row.name},
        )
    session.flush()
    return deactivated


def format_summary(summary: ImportSummary) -> str:
    """Render a summary for an administrator, rejections first."""
    lines = [
        "🗺 <b>Location channel synchronised</b>",
        f"Posts read: {summary.total}",
        f"✅ Created: {summary.created}",
        f"♻️ Updated: {summary.updated}",
        f"➕ Linked to an existing place: {summary.linked}",
        f"➖ Unchanged: {summary.unchanged}",
    ]
    if summary.deactivated:
        lines.append(
            f"⛔ Deactivated (no longer in the channel): {summary.deactivated}"
        )
    if summary.stale_skipped:
        lines.append(
            f"⏭ Skipped as older than a live update: {summary.stale_skipped}"
        )
    if summary.warnings:
        lines.append("")
        lines.append("<b>Warnings</b>")
        lines.extend(f"• {_escape(item)}" for item in summary.warnings[:10])
    if summary.rejected:
        lines.append("")
        lines.append(f"❌ <b>Rejected: {len(summary.rejected)}</b>")
        lines.extend(
            f"• {_escape(rejected.describe())}" for rejected in summary.rejected[:15]
        )
        if len(summary.rejected) > 15:
            lines.append(f"… and {len(summary.rejected) - 15} more.")
    return "\n".join(lines)


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
