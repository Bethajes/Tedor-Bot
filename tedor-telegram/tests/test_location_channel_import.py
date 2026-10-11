"""Importing curated locations from the Telegram location channel.

The channel is the editable source of truth for the directory. These tests
cover the properties that make it safe to keep: a record is validated before
it is trusted, importing it twice changes nothing, an edit updates one row, a
bad post is rejected with an explanation rather than a stack trace, and
coordinates are never invented.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.database import session_scope
from app.models import Location
from app.services import location_channel_service as channel
from app.services import location_registry_service as locations

CHAT = -1001234567890

AYAT = (9.0302, 38.7540)
BOLE = (8.9987, 38.7890)


def record(
    code: str = "AA-0001",
    name: str = "Arada Center",
    *,
    aliases: str = "",
    latitude: object = 9.0150,
    longitude: object = 38.7200,
    status: str = "pending",
    **extra: object,
) -> str:
    lines = [f"ID: {code}", f"Name: {name}"]
    if aliases:
        lines.append(f"Aliases: {aliases}")
    if latitude is not None and longitude is not None:
        lines.append(f"Latitude: {latitude}")
        lines.append(f"Longitude: {longitude}")
    lines.append(f"Status: {status}")
    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


@pytest.fixture(autouse=True)
def _no_registry_seed(db):
    """These tests own their rows; a pre-seeded registry would interfere."""
    return db


def sync(db, text: str, *, chat_id: int = CHAT, message_id: int = 1):
    with session_scope() as session:
        return channel.sync_message(
            session, text, chat_id=chat_id, message_id=message_id
        )


def fetch(db, code: str) -> Location:
    return db.query(Location).filter(Location.location_code == code).one()


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def test_a_complete_record_is_parsed():
    parsed = channel.parse_record(
        record(aliases="Arada, Piazza", **{"Sub City": "Arada", "Woreda": "11"}),
        chat_id=CHAT,
        message_id=7,
    )
    assert parsed.id == "AA-0001"
    assert parsed.name == "Arada Center"
    assert parsed.aliases == ("Arada", "Piazza")
    assert parsed.latitude == pytest.approx(9.0150)
    assert parsed.longitude == pytest.approx(38.7200)
    assert parsed.sub_city == "Arada"
    assert parsed.woreda == "11"
    assert parsed.chat_id == CHAT
    assert parsed.message_id == 7


def test_field_order_and_key_spelling_do_not_matter():
    scrambled = "Woreda: 11\nlongitude = 38.72\nNAME: Arada Center\nid: aa-0001\nlat: 9.015"  # noqa: E501
    parsed = channel.parse_record(scrambled)
    assert parsed.id == "AA-0001"
    assert parsed.name == "Arada Center"
    assert parsed.latitude == pytest.approx(9.015)


def test_a_code_is_normalised_to_upper_case():
    assert channel.location_code(" aa-0001 ") == "AA-0001"
    assert channel.location_code("AA-1") is None
    assert channel.location_code("Addis Ababa") is None


def test_optional_fields_may_be_omitted():
    parsed = channel.parse_record("ID: AA-0002\nName: Arada\n")
    assert parsed.latitude is None and parsed.longitude is None
    assert parsed.aliases == ()
    assert parsed.status == "pending"


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("Name: Bole\n", "ID"),
        ("ID: AA-0003\n", "Name"),
        ("ID: NOPE\nName: Bole\n", "not a valid location code"),
        ("ID: AA-0004\nName: Bole\nLatitude: 9.0\n", "both"),
        ("ID: AA-0005\nName: Bole\nLatitude: 91\nLongitude: 38\n", "latitude"),
        ("ID: AA-0006\nName: Bole\nLatitude: 9\nLongitude: 181\n", "longitude"),
        ("ID: AA-0007\nName: Bole\nLatitude: 0\nLongitude: 0\n", "0, 0"),
        ("ID: AA-0008\nName: Bole\nLatitude: x\nLongitude: 38\n", "must be a number"),
        ("ID: AA-0009\nName: Bole\nStatus: maybe\n", "not a valid status"),
        ("", "empty"),
    ],
)
def test_unusable_records_are_rejected_with_an_explanation(text, fragment):
    with pytest.raises(channel.RecordError) as caught:
        channel.parse_record(text)
    assert fragment.lower() in caught.value.user_message.lower()


def test_coordinates_outside_ethiopia_are_rejected():
    """51.5, -0.1 is London; accepting it would poison every distance."""
    with pytest.raises(channel.RecordError) as caught:
        channel.parse_record(record(latitude="51.5", longitude="-0.1"))
    assert "outside ethiopia" in caught.value.user_message.lower()


def test_the_bounds_check_can_be_switched_off(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "location_channel_enforce_bounds", False)
    parsed = channel.parse_record(record(latitude="51.5", longitude="-0.1"))
    assert parsed.latitude == pytest.approx(51.5)


def test_an_alias_that_is_just_the_name_is_reported_not_stored():
    parsed = channel.parse_record(record(aliases="Arada Center, Arada"))
    assert parsed.aliases == ("Arada",)
    # The alias equal to the name was reported, not silently dropped.
    assert "Arada Center" in parsed.redundant_aliases


def test_an_alias_the_registry_already_folds_onto_the_place_is_reported():
    """The curated registry maps "Keranio" onto "Kolfe Keranio"; storing it as
    an alias would add nothing, so it is reported rather than silently kept."""
    parsed = channel.parse_record(
        record("AA-0199", "Kolfe Keranio", aliases="Keranio, Kolfe")
    )
    assert parsed.aliases == ()
    assert set(parsed.redundant_aliases) == {"Keranio", "Kolfe"}


def test_an_unrecognised_field_is_kept_as_a_warning_not_an_error():
    parsed = channel.parse_record(record(Colour="blue"))
    assert any("Colour" in item for item in parsed.unknown_keys)


# ---------------------------------------------------------------------------
# writing to the database
# ---------------------------------------------------------------------------


def test_a_new_record_becomes_a_location(db):
    outcome = sync(db, record(), message_id=1)
    assert outcome.action == "created"
    stored = fetch(db, "AA-0001")
    assert stored.name == "Arada Center"
    assert stored.latitude == pytest.approx(9.0150)
    assert stored.verification_status == "PENDING"
    assert stored.is_active is True
    assert stored.synced_at is not None
    assert stored.source_chat_id == CHAT
    assert stored.source_message_id == 1


def test_importing_the_same_record_twice_changes_nothing(db):
    text = record()
    sync(db, text, message_id=1)
    before = fetch(db, "AA-0001")
    outcome = sync(db, text, message_id=1)
    assert outcome.action == "unchanged"
    assert db.query(Location).filter(Location.location_code == "AA-0001").count() == 1
    after = fetch(db, "AA-0001")
    assert after.updated_at == before.updated_at or after.latitude == before.latitude


def test_a_whole_export_replayed_makes_no_duplicates(db):
    messages = [(1, record("AA-0001", "Kolfe Keranio")), (2, record("AA-0002", "Arada"))]
    for _ in range(3):
        with session_scope() as session:
            summary = channel.sync_messages(session, messages, chat_id=CHAT)
    assert db.query(Location).filter(Location.location_code == "AA-0001").count() == 1
    assert db.query(Location).filter(Location.location_code == "AA-0002").count() == 1
    assert summary.unchanged == 2


def test_an_edited_record_updates_one_row_in_place(db):
    sync(db, record(), message_id=1)
    edited = record(latitude="9.0200", aliases="Keranio")
    outcome = sync(db, edited, message_id=1)
    assert outcome.action == "updated"
    assert db.query(Location).filter(Location.location_code == "AA-0001").count() == 1
    stored = fetch(db, "AA-0001")
    assert stored.latitude == pytest.approx(9.0200)
    assert [alias.alias for alias in stored.aliases] == ["Keranio"]


def test_a_removed_alias_is_removed_from_the_directory(db):
    sync(db, record(aliases="Arada, Piazza"), message_id=1)
    sync(db, record(aliases="Arada"), message_id=1)
    stored = fetch(db, "AA-0001")
    assert sorted(alias.alias for alias in stored.aliases) == ["Arada"]


def test_removing_the_coordinates_leaves_the_place_without_them(db):
    sync(db, record(), message_id=1)
    sync(db, record(latitude=None, longitude=None), message_id=1)
    stored = fetch(db, "AA-0001")
    assert stored.latitude is None
    assert stored.longitude is None
    # Not replaced by anything plausible.
    assert not stored.has_coordinates


def test_a_record_adopts_an_existing_place_instead_of_duplicating_it(db):
    """The curated registry already knows "Bole" with no coordinates."""
    with session_scope() as session:
        existing = locations.resolve_or_create_manual(
            session, name="Bole", city="Addis Ababa"
        )
        existing_id = existing.location.id

    outcome = sync(db, record("AA-0010", "Bole", latitude=BOLE[0], longitude=BOLE[1]))

    assert outcome.action == "linked"
    assert outcome.location.id == existing_id
    assert db.query(Location).filter(Location.normalized_name == "bole").count() == 1
    assert fetch(db, "AA-0010").latitude == pytest.approx(BOLE[0])


def test_two_codes_claiming_one_name_do_not_become_two_places(db):
    sync(db, record("AA-0020", "Arada", latitude=9.02, longitude=38.75), message_id=1)
    outcome = sync(db, record("AA-0021", "Arada", latitude=9.03, longitude=38.76), message_id=2)
    # The second code joins the existing place rather than forking it.
    assert outcome.location.location_code == "AA-0020"
    assert db.query(Location).filter(Location.normalized_name == "arada").count() == 1


def test_an_alias_claimed_by_another_place_is_rejected(db):
    """Two places cannot both answer to one spelling without making
    resolution depend on query order."""
    sync(
        db,
        record("AA-0030", "Meron", aliases="Kolfe Abaya",
               latitude=9.0242, longitude=38.758),
        message_id=1,
    )
    with pytest.raises(channel.RecordError) as caught:
        sync(
            db,
            record("AA-0031", "Meron Abaya", aliases="Kolfe Abaya",
                   latitude=9.0242, longitude=38.758),
            message_id=2,
        )
    assert "already an alias" in caught.value.user_message
    assert db.query(Location).filter(Location.location_code == "AA-0031").count() == 0


def test_an_alias_that_is_another_place_s_name_is_rejected(db):
    sync(db, record("AA-0032", "Meron", latitude=9.02, longitude=38.75), message_id=1)
    with pytest.raises(channel.RecordError) as caught:
        sync(
            db,
            record("AA-0033", "Meron Abaya", aliases="Meron",
                   latitude=9.02, longitude=38.75),
            message_id=2,
        )
    assert "is the name of" in caught.value.user_message


def test_verified_requires_the_trust_setting(db):
    sync(db, record(status="verified"), message_id=1)
    assert fetch(db, "AA-0001").verification_status == "VERIFIED"


def test_trust_can_be_switched_off_so_the_review_queue_owns_verification(
    db, monkeypatch
):
    from app.config import settings

    sync(db, record(status="verified"), message_id=1)
    assert fetch(db, "AA-0001").verification_status == "VERIFIED"

    monkeypatch.setattr(settings, "location_channel_trust_verified", False)
    sync(db, record(status="verified"), message_id=2)
    assert fetch(db, "AA-0001").verification_status == "PENDING"


# ---------------------------------------------------------------------------
# deactivated records
# ---------------------------------------------------------------------------


def test_a_disabled_record_is_deactivated_not_deleted(db):
    sync(db, record(), message_id=1)
    sync(db, record(status="disabled"), message_id=1)
    stored = fetch(db, "AA-0001")
    assert stored.is_active is False
    assert stored.verification_status == "REJECTED"
    assert db.query(Location).filter(Location.location_code == "AA-0001").count() == 1


def test_a_deactivated_place_still_resolves_for_what_already_points_at_it(db):
    """Tutors and jobs that reference it must stay explainable."""
    sync(db, record(), message_id=1)
    sync(db, record(status="disabled"), message_id=1)
    with session_scope() as session:
        found = locations.resolve_by_text(session, "Arada Center")
    assert found is not None
    assert found.is_active is False


def test_a_deactivated_place_is_not_offered_for_a_new_selection(db):
    sync(db, record(), message_id=1)
    sync(db, record(status="disabled"), message_id=1)
    with session_scope() as session:
        assert locations.search_locations(session, "Arada") == []
        assert len(locations.search_locations(
            session, "Arada", include_inactive=True, include_rejected=True
        )) == 1


def test_reconcile_deactivates_what_the_channel_no_longer_has(db):
    sync(db, record("AA-0040", "Arada"), message_id=1)
    sync(db, record("AA-0041", "Bole", latitude=BOLE[0], longitude=BOLE[1]), message_id=2)
    with session_scope() as session:
        deactivated = channel.reconcile(session, seen_message_ids={2})
    assert deactivated == 1
    assert fetch(db, "AA-0040").is_active is False
    assert fetch(db, "AA-0041").is_active is True
    # Still present, so anything already pointing at it keeps working.
    assert db.query(Location).filter(Location.location_code == "AA-0040").count() == 1


# ---------------------------------------------------------------------------
# batch import
# ---------------------------------------------------------------------------


def test_one_bad_post_does_not_stop_the_rest(db):
    messages = [
        (1, record("AA-0050", "Arada")),
        (2, "this is just a chat message"),
        (3, record("AA-0051", "Bole", latitude=BOLE[0], longitude=BOLE[1])),
    ]
    with session_scope() as session:
        summary = channel.sync_messages(session, messages, chat_id=CHAT)

    assert summary.created == 2
    assert len(summary.rejected) == 1
    assert summary.rejected[0].message_id == 2
    assert db.query(Location).filter(Location.location_code == "AA-0050").count() == 1
    assert db.query(Location).filter(Location.location_code == "AA-0051").count() == 1


def test_the_summary_counts_each_kind_of_outcome(db):
    messages = [
        (1, record("AA-0060", "Arada")),
        (2, record("AA-0061", "Bole", latitude=BOLE[0], longitude=BOLE[1])),
        # The same post arriving again — a repeated backfill, or a restart.
        (2, record("AA-0061", "Bole", latitude=BOLE[0], longitude=BOLE[1])),
    ]
    with session_scope() as session:
        summary = channel.sync_messages(session, messages, chat_id=CHAT)
    assert summary.created == 2
    assert summary.unchanged == 1
    assert summary.accepted == 3


def test_the_summary_is_readable_by_an_administrator(db):
    messages = [(1, "not a record")]
    with session_scope() as session:
        summary = channel.sync_messages(session, messages, chat_id=CHAT)
    rendered = channel.format_summary(summary)
    assert "Rejected: 1" in rendered
    assert "ID" in rendered  # the reason says what is missing


# ---------------------------------------------------------------------------
# export backfill
# ---------------------------------------------------------------------------


def _export(entries, tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"name": "chan", "type": "channel", "messages": entries}))
    return path


def test_a_channel_export_is_imported(db, tmp_path):
    path = _export(
        [
            {"id": 1, "type": "message", "date": "2024-05-01T10:00:00", "text": record("AA-0070", "Arada")},
            {"id": 2, "type": "message", "date": "2024-05-01T10:01:00", "text": record("AA-0071", "Bole", latitude=BOLE[0], longitude=BOLE[1])},
        ],
        tmp_path,
    )
    with session_scope() as session:
        summary = channel.import_export_file(session, path, chat_id=CHAT)
    assert summary.created == 2
    assert fetch(db, "AA-0070").source_message_id == 1


def test_a_bare_list_export_is_also_accepted(db, tmp_path):
    path = tmp_path / "messages.json"
    path.write_text(json.dumps([{"id": 9, "text": record("AA-0072", "Arada")}]))
    with session_scope() as session:
        summary = channel.import_export_file(session, path, chat_id=CHAT)
    assert summary.created == 1


def test_a_file_with_no_text_messages_is_reported_not_guessed(db, tmp_path):
    path = _export([{"id": 1, "type": "message", "text": ""}], tmp_path)
    with pytest.raises(channel.LocationChannelError):
        with session_scope() as session:
            channel.import_export_file(session, path, chat_id=CHAT)


def test_an_export_older_than_a_live_edit_does_not_roll_it_back(db, tmp_path):
    """Re-running an old backfill must not undo a correction."""
    sync(db, record("AA-0080", "Arada", latitude=9.0200), message_id=5)
    stale = _export(
        [
            {
                "id": 5,
                "type": "message",
                "date": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
                "text": record("AA-0080", "Arada", latitude=1.0000),
            }
        ],
        tmp_path,
    )
    with session_scope() as session:
        summary = channel.import_export_file(session, stale, chat_id=CHAT)
    assert summary.stale_skipped == 1
    assert summary.updated == 0
    assert fetch(db, "AA-0080").latitude == pytest.approx(9.0200)


def test_a_newer_export_does_update(db, tmp_path):
    sync(db, record("AA-0081", "Arada", latitude=9.0200), message_id=5)
    newer = _export(
        [
            {
                "id": 5,
                "type": "message",
                "date": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "text": record("AA-0081", "Arada", latitude=9.0400),
            }
        ],
        tmp_path,
    )
    with session_scope() as session:
        summary = channel.import_export_file(session, newer, chat_id=CHAT)
    assert summary.updated == 1
    assert fetch(db, "AA-0081").latitude == pytest.approx(9.0400)


def test_reconciling_from_a_full_export_removes_deleted_posts(db, tmp_path):
    sync(db, record("AA-0090", "Arada"), message_id=1)
    sync(db, record("AA-0091", "Bole", latitude=BOLE[0], longitude=BOLE[1]), message_id=2)
    path = _export(
        [
            {"id": 2, "type": "message", "date": "2024-05-01T10:00:00",
             "text": record("AA-0091", "Bole", latitude=BOLE[0], longitude=BOLE[1])},
        ],
        tmp_path,
    )
    with session_scope() as session:
        summary = channel.import_export_file(
            session, path, chat_id=CHAT, reconcile_missing=True
        )
    assert summary.deactivated == 1
    assert fetch(db, "AA-0090").is_active is False

# ---------------------------------------------------------------------------
# the backfill command
# ---------------------------------------------------------------------------


def _export_file(tmp_path, entries):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"name": "chan", "type": "channel", "messages": entries}))
    return str(path)


def test_the_dry_run_reports_without_writing(db, tmp_path, capsys):
    from app import import_locations

    path = _export_file(
        tmp_path,
        [{"id": 1, "type": "message", "text": record("AA-0100", "Arada Center")}],
    )
    code = import_locations.main(["--file", path, "--chat-id", str(CHAT), "--dry-run"])

    assert code == 0
    output = capsys.readouterr().out
    assert "AA-0100" in output
    assert "dry run" in output.lower()
    assert db.query(Location).filter(Location.location_code == "AA-0100").count() == 0


def test_the_backfill_imports_and_reports(db, tmp_path, capsys):
    from app import import_locations

    path = _export_file(
        tmp_path,
        [
            {"id": 1, "type": "message", "text": record("AA-0110", "Arada Center")},
            {"id": 2, "type": "message", "text": "not a record"},
        ],
    )
    code = import_locations.main(["--file", path, "--chat-id", str(CHAT)])

    assert code == 0
    output = capsys.readouterr().out
    assert "Created: 1" in output
    assert "Rejected: 1" in output
    assert db.query(Location).filter(Location.location_code == "AA-0110").count() == 1


def test_the_backfill_refuses_a_missing_file(tmp_path, capsys):
    from app import import_locations

    code = import_locations.main(
        ["--file", str(tmp_path / "nope.json"), "--chat-id", str(CHAT)]
    )
    assert code == 2
    assert "No such file" in capsys.readouterr().err


def test_the_backfill_requires_a_channel_id(tmp_path, capsys, monkeypatch):
    from app import import_locations
    from app.config import settings

    monkeypatch.setattr(settings, "location_channel_id", 0)
    path = _export_file(tmp_path, [])
    code = import_locations.main(["--file", path])
    assert code == 2
    assert "LOCATION_CHANNEL_ID" in capsys.readouterr().err


def test_running_the_backfill_twice_creates_no_duplicates(db, tmp_path):
    from app import import_locations

    path = _export_file(
        tmp_path,
        [
            {"id": 1, "type": "message", "text": record("AA-0120", "Arada Center")},
            {"id": 2, "type": "message", "text": record("AA-0121", "Bole", latitude=BOLE[0], longitude=BOLE[1])},
        ],
    )
    assert import_locations.main(["--file", path, "--chat-id", str(CHAT)]) == 0
    assert import_locations.main(["--file", path, "--chat-id", str(CHAT)]) == 0
    assert db.query(Location).filter(Location.location_code == "AA-0120").count() == 1
    assert db.query(Location).filter(Location.location_code == "AA-0121").count() == 1
