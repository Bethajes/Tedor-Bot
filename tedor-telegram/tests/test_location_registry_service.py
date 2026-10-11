"""Locations: manual registration, duplicate detection, and admin review.

The behaviour worth pinning here is everything that stops the same place from
ending up in the database twice, and everything that stops a hand-typed place
from being presented as a confirmed one.
"""

from __future__ import annotations

import itertools

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    JobPost,
    Location,
    LocationAlias,
    StudentRequest,
    Tutor,
    TutorLocation,
    utcnow,
)
from app.services import location_registry_service as locations
from app.services.location_registry_service import (
    LocationError,
    LocationNotFound,
)


@pytest.fixture(autouse=True)
def _seeded(db: Session):
    """A database holding the curated registry, as a running app would.

    Seeded explicitly rather than relying on ``init_db`` so the tests state the
    precondition instead of inheriting it.
    """
    locations.seed_registry(db)
    db.commit()
    return db


# ---------------------------------------------------------------------------
# the curated registry
# ---------------------------------------------------------------------------


def test_the_registry_becomes_locations(db: Session):
    assert db.scalar(select(locations.func.count()).select_from(Location)) >= 20


def test_seeding_twice_adds_nothing(_seeded):
    first = _seeded.scalar(select(locations.func.count()).select_from(Location))
    locations.seed_registry(_seeded)
    _seeded.commit()
    assert _seeded.scalar(select(locations.func.count()).select_from(Location)) == first


def test_registry_entries_are_verified_not_pending(_seeded):
    row = locations.resolve_by_text(_seeded, "Ayat Tsebel")
    assert row is not None and row.is_verified
    assert row.provider == "REGISTRY"


# ---------------------------------------------------------------------------
# resolving free text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["Ayat Tsebel", "ayat tsebel", "Ayat_Tsebel", "  Ayat   Tsebel  ", "AYAT TSEBEL"],
)
def test_a_spelling_fold_resolves_to_one_location(db: Session, text: str):
    assert locations.resolve_by_text(db, text) is not None


def test_an_unknown_place_resolves_to_nothing(db: Session):
    assert locations.resolve_by_text(db, "Woreda 12") is None


def test_the_city_suffix_does_not_change_a_lookup(db: Session, _seeded):
    assert locations.resolve_by_text(db, "Bole").name == locations.resolve_by_text(
        db, "Bole, Addis Ababa"
    ).name


# ---------------------------------------------------------------------------
# manual registration
# ---------------------------------------------------------------------------


def test_a_new_place_is_created_pending_and_without_coordinates(db: Session):
    resolved = locations.resolve_or_create_manual(
        db,
        name="Woreda 12, local name Goro Sefer",
        city="Addis Ababa",
        sub_city="Woreda 12",
        landmark="near the local market",
        created_by_user_id=7,
    )
    location = resolved.location
    assert resolved.is_suggestion is False
    assert location.name == "Woreda 12, local name Goro Sefer"
    assert location.verification_status == "PENDING"
    assert location.provider == "MANUAL"
    assert location.provider_place_id is None
    assert location.sub_city == "Woreda 12"
    assert location.landmark == "near the local market"
    assert location.has_coordinates is False


def test_the_submitted_text_is_kept_verbatim(db: Session):
    locations.resolve_or_create_manual(db, name="  Goro   Sefer ", city="Addis Ababa")
    row = db.scalar(select(Location).where(Location.provider == "MANUAL"))
    assert row is not None and row.name == "Goro Sefer"


def test_a_second_submission_of_the_same_place_is_not_a_duplicate(db: Session):
    first = locations.resolve_or_create_manual(db, name="Goro Sefer", city="Addis Ababa")
    db.commit()
    second = locations.resolve_or_create_manual(db, name="Goro Sefer", city="Addis Ababa")
    assert first.location.id == second.location.id
    assert second.is_suggestion


def test_a_manual_place_in_a_different_city_is_a_different_place(db: Session):
    """The same name in two cities is two places, not one record with a typo."""
    addis = locations.resolve_or_create_manual(
        db, name="Goro Sefer 14", city="Addis Ababa"
    )
    db.commit()
    hawassa = locations.resolve_or_create_manual(
        db, name="Goro Sefer 14", city="Hawassa"
    )
    assert addis.location.id != hawassa.location.id


def test_a_city_outside_ethiopia_is_refused(db: Session):
    with pytest.raises(LocationError) as exc:
        locations.resolve_or_create_manual(db, name="London Bridge", city="London")
    assert "Ethiopia" in exc.value.user_message


def test_an_empty_name_is_refused(db: Session):
    with pytest.raises(LocationError):
        locations.resolve_or_create_manual(db, name="   ", city="Addis Ababa")


def test_a_city_defaults_to_addis_ababa(db: Session):
    resolved = locations.resolve_or_create_manual(db, name="Somewhere")
    assert resolved.location.city == "Addis Ababa"


# ---------------------------------------------------------------------------
# duplicate detection
# ---------------------------------------------------------------------------


def test_a_near_match_in_the_same_city_is_offered_instead(db: Session):
    locations.resolve_or_create_manual(db, name="Goro Sefer Market", city="Addis Ababa")
    db.commit()
    resolved = locations.resolve_or_create_manual(db, name="Gorosefer Market", city="Addis Ababa")
    assert resolved.is_suggestion
    assert resolved.matched_by in ("EXACT", "ALIAS", "SIMILAR")


def test_an_abbreviation_is_not_treated_as_a_near_match(db: Session):
    """"Mkt" is not "Market" as far as characters go, so it is its own place.

    A person would know these are the same; a character measure cannot, and
    guessing is how two rows get merged that should not be. An admin merges it
    if it turns out to be a duplicate.
    """
    locations.resolve_or_create_manual(db, name="Goro Sefer Market", city="Addis Ababa")
    db.commit()
    resolved = locations.resolve_or_create_manual(db, name="Goro Sefer Mkt", city="Addis Ababa")
    assert resolved.matched_by == "CREATED"


def test_a_near_match_across_cities_is_not_offered(db: Session):
    locations.resolve_or_create_manual(db, name="Goro Sefer", city="Addis Ababa")
    db.commit()
    resolved = locations.resolve_or_create_manual(
        db, name="Goro Sefer A", city="Hawassa"
    )
    assert resolved.matched_by == "CREATED"


def test_an_alias_means_the_next_spelling_finds_the_same_row(db: Session):
    location = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    db.commit()
    locations.add_alias(db, location, "Goro Sefer Market")
    db.commit()
    assert locations.resolve_by_text(db, "Goro Sefer Market").id == location.id


def test_adding_a_spelling_that_already_belongs_elsewhere_is_refused(db: Session):
    first = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    locations.resolve_or_create_manual(db, name="Ayat Tsebel", city="Addis Ababa")
    db.commit()
    locations.add_alias(db, locations.resolve_by_text(db, "Ayat Tsebel"), "Goro Sefer")
    db.commit()
    with pytest.raises(LocationError):
        locations.add_alias(db, first, "Goro Sefer")


def test_a_locations_own_name_cannot_be_added_as_an_alias(db: Session):
    location = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    with pytest.raises(LocationError):
        locations.add_alias(db, location, "Goro Sefer")


# ---------------------------------------------------------------------------
# pending and verified
# ---------------------------------------------------------------------------


def test_the_pending_queue_lists_manual_submissions_only(db: Session):
    locations.resolve_or_create_manual(db, name="Goro Sefer", city="Addis Ababa")
    db.commit()
    queue = locations.pending_locations(db)
    assert [row.name for row in queue] == ["Goro Sefer"]

    locations.approve_location(db, queue[0].id)
    db.commit()
    assert locations.pending_locations(db) == []


def test_the_queue_is_oldest_first(db: Session):
    locations.resolve_or_create_manual(db, name="Older", city="Addis Ababa")
    first = db.scalar(select(Location).where(Location.name == "Older"))
    first.created_at = utcnow().replace(year=2025)
    locations.resolve_or_create_manual(db, name="Newer", city="Addis Ababa")
    db.commit()
    assert [row.name for row in locations.pending_locations(db)] == ["Older", "Newer"]


def test_a_curated_place_is_never_in_the_review_queue(db):
    locations.resolve_or_create_manual(db, name="Goro Sefer", city="Addis Ababa")
    db.commit()
    names = {row.name for row in locations.pending_locations(db)}
    assert "Ayat Tsebel" not in names


# ---------------------------------------------------------------------------
# admin review
# ---------------------------------------------------------------------------


def test_approving_makes_a_location_verified(db: Session):
    location = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    db.commit()
    assert locations.approve_location(db, location.id).is_verified


def test_rejecting_keeps_the_row_but_stops_it_being_offered(db: Session):
    location = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    db.commit()
    rejected = locations.reject_location(db, location.id, "made up")
    assert rejected.is_rejected
    # A job that already points at it must still resolve.
    assert locations.resolve_by_text(db, "Goro Sefer") is not None


def test_an_unknown_status_is_refused(db: Session):
    location = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    with pytest.raises(LocationError):
        locations.set_verification_status(db, location.id, "MAYBE")


def test_an_unknown_location_id_is_reported(db: Session):
    with pytest.raises(LocationNotFound):
        locations.approve_location(db, 9999)


def test_coordinates_are_only_accepted_inside_ethiopia(db: Session):
    location = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    latitude, longitude = locations.parse_coordinates("9.03, 38.75")
    locations.set_coordinates(db, location.id, latitude, longitude)
    db.commit()
    assert location.has_coordinates

    with pytest.raises(LocationError):
        locations.parse_coordinates("51.5, -0.1")
    with pytest.raises(LocationError):
        locations.parse_coordinates("nine, thirty")
    with pytest.raises(LocationError):
        locations.parse_coordinates("9.03")


# ---------------------------------------------------------------------------
# merging
# ---------------------------------------------------------------------------


def _tutor(db: Session, telegram_id: int = -1000) -> Tutor:
    tutor = Tutor(
        public_tutor_id="TDR-000001",
        telegram_user_id=telegram_id,
        name="Abebe Bekele",
        display_name="Abebe",
        phone="+251911234567",
        email="",
        country="Ethiopia",
        city="Addis Ababa",
        teaching_mode="IN_PERSON",
    )
    db.add(tutor)
    db.flush()
    return tutor


def test_merging_keeps_tutor_links_and_preserves_the_display_text(db: Session):
    keeper = locations.resolve_or_create_manual(
        db, name="Goro Sefer Market", city="Addis Ababa"
    ).location
    dup = locations.resolve_or_create_manual(
        db, name="Woreda 12 corner", city="Addis Ababa"
    ).location
    db.commit()
    tutor = _tutor(db)
    db.add(
        TutorLocation(
            tutor_id=tutor.id,
            location=dup.name,
            normalized_location=dup.normalized_name,
            location_id=dup.id,
            source="MANUAL",
        )
    )
    db.flush()

    locations.merge_locations(db, dup.id, keeper.id)
    db.commit()

    rows = list(db.scalars(select(TutorLocation).where(TutorLocation.tutor_id == tutor.id)))
    assert len(rows) == 1
    assert rows[0].location_id == keeper.id
    # What the tutor typed is preserved even though the row changed hands.
    assert rows[0].location == "Woreda 12 corner"


def test_merging_keeps_job_links(db: Session):
    keeper = locations.resolve_or_create_manual(
        db, name="Goro Sefer Market", city="Addis Ababa"
    ).location
    dup = locations.resolve_or_create_manual(
        db, name="Woreda 12 corner", city="Addis Ababa"
    ).location
    db.commit()
    db.add(
        JobPost(
            public_post_id="JOB-000001",
            telegram_user_id=-1000,
            location=dup.name,
            location_id=dup.id,
            student_level="Grade 4",
            tutor_category="Both",
            duration="1 hour",
            frequency="2 days",
            subjects="Mathematics",
            compensation="300 ETB",
        )
    )
    db.flush()

    locations.merge_locations(db, dup.id, keeper.id)
    db.commit()

    post = db.scalar(select(JobPost))
    assert post.location_id == keeper.id


def test_merging_keeps_parent_request_links(db: Session):
    keeper = locations.resolve_or_create_manual(
        db, name="Goro Sefer Market", city="Addis Ababa"
    ).location
    dup = locations.resolve_or_create_manual(
        db, name="Woreda 12 corner", city="Addis Ababa"
    ).location
    db.commit()
    db.add(
        StudentRequest(
            public_request_id="REQ-000001",
            telegram_user_id=-1000,
            parent_name="Asnake",
            student_name="Abel",
            country="Ethiopia",
            education_level="Grade 4",
            subject="Mathematics",
            mode="IN_PERSON",
            location=dup.name,
            location_id=dup.id,
        )
    )
    db.flush()

    locations.merge_locations(db, dup.id, keeper.id)
    db.commit()

    request = db.scalar(select(StudentRequest))
    assert request.location_id == keeper.id


def test_the_merged_away_row_is_a_tombstone_not_a_deletion(db: Session):
    keeper = locations.resolve_or_create_manual(
        db, name="Goro Sefer Market", city="Addis Ababa"
    ).location
    dup = locations.resolve_or_create_manual(
        db, name="Woreda 12 corner", city="Addis Ababa"
    ).location
    db.commit()
    locations.merge_locations(db, dup.id, keeper.id)
    db.commit()

    gone = db.get(Location, dup.id)
    assert gone is not None
    assert gone.is_rejected
    assert gone.merged_into_id == keeper.id


def test_merging_keeps_aliases(db: Session):
    keeper = locations.resolve_or_create_manual(
        db, name="Goro Sefer Market", city="Addis Ababa"
    ).location
    dup = locations.resolve_or_create_manual(
        db, name="Woreda 12 corner", city="Addis Ababa"
    ).location
    db.commit()
    locations.add_alias(db, dup, "Woreda 12 market")
    db.commit()

    locations.merge_locations(db, dup.id, keeper.id)
    db.commit()

    spellings = {
        row.alias
        for row in db.scalars(select(LocationAlias).where(LocationAlias.location_id == keeper.id))
    }
    assert "Woreda 12 market" in spellings


def test_a_location_cannot_be_merged_into_itself(db: Session):
    location = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    with pytest.raises(LocationError):
        locations.merge_locations(db, location.id, location.id)


def test_an_already_merged_location_cannot_be_merged_again(db: Session):
    keeper = locations.resolve_or_create_manual(
        db, name="Goro Sefer Market", city="Addis Ababa"
    ).location
    dup = locations.resolve_or_create_manual(
        db, name="Woreda 12 corner", city="Addis Ababa"
    ).location
    db.commit()
    locations.merge_locations(db, dup.id, keeper.id)
    db.commit()

    with pytest.raises(LocationError) as exc:
        locations.merge_locations(db, dup.id, keeper.id)
    assert "already merged" in exc.value.user_message


def test_merging_keeps_the_surviving_coordinates(db: Session):
    keeper = locations.resolve_or_create_manual(
        db, name="Goro Sefer Market", city="Addis Ababa"
    ).location
    keeper.latitude, keeper.longitude = 9.01, 38.76
    dup = locations.resolve_or_create_manual(
        db, name="Woreda 12 corner", city="Addis Ababa"
    ).location
    dup.latitude, dup.longitude = 8.0, 38.0
    db.commit()

    locations.merge_locations(db, dup.id, keeper.id)
    db.commit()

    assert keeper.latitude == 9.01


def test_a_duplicate_tutor_link_collapses_to_one_row(db: Session):
    """A tutor linked to both ends of a merge ends up linked once."""
    keeper = locations.resolve_or_create_manual(
        db, name="Goro Sefer Market", city="Addis Ababa"
    ).location
    dup = locations.resolve_or_create_manual(
        db, name="Woreda 12 corner", city="Addis Ababa"
    ).location
    db.commit()
    tutor = _tutor(db)
    for row_location, row_location_id in ((keeper.name, keeper.id), (dup.name, dup.id)):
        db.add(
            TutorLocation(
                tutor_id=tutor.id,
                location=row_location,
                normalized_location=locations.normalize_name(row_location),
                location_id=row_location_id,
                source="MANUAL",
            )
        )
    db.flush()

    locations.merge_locations(db, dup.id, keeper.id)
    db.commit()

    rows = list(db.scalars(select(TutorLocation).where(TutorLocation.tutor_id == tutor.id)))
    assert len(rows) == 1
    assert rows[0].location_id == keeper.id


# ---------------------------------------------------------------------------
# presentation
# ---------------------------------------------------------------------------


def test_a_manual_location_is_never_labelled_verified(db: Session):
    location = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    assert "unverified" in locations.verification_badge(location).lower()


def test_a_rejected_location_says_so(db: Session):
    location = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    locations.reject_location(db, location.id)
    assert "rejected" in locations.verification_badge(location).lower()


def test_a_verified_location_says_so(db: Session):
    location = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    locations.approve_location(db, location.id)
    assert "verified" in locations.verification_badge(location).lower()


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "Ayat Tsebel",
        "ayat tsebel",
        "AYAT TSEBEL",
        "  Ayat   Tsebel  ",
        "Ayat_Tsebel",
        "ayat-tsebel",
        "Ayat Tsebel!!!",
    ],
)
def test_normalisation_folds_case_whitespace_and_punctuation(db: Session, value: str):
    """All of these are one place, however they were typed."""
    assert locations.normalize_name(value) == "ayat tsebel"


def test_the_normalised_key_is_stored_on_every_row(db: Session):
    locations.resolve_or_create_manual(db, name="  Goro   Sefer ", city="Addis Ababa")
    db.commit()
    row = db.scalar(select(Location).where(Location.name == "Goro Sefer"))
    assert row.normalized_name == "goro sefer"


def test_the_search_index_matches_on_punctuation_only(db: Session):
    locations.resolve_or_create_manual(db, name="Goro-Sefer", city="Addis Ababa")
    db.commit()
    assert locations.resolve_by_text(db, "goro sefer").name == "Goro-Sefer"


def test_search_finds_a_location_by_part_of_its_name(db: Session):
    locations.resolve_or_create_manual(db, name="Goro Sefer Market", city="Addis Ababa")
    db.commit()
    found = locations.search_locations(db, "sefer")
    assert [row.name for row in found] == ["Goro Sefer Market"]


def test_search_finds_a_location_by_a_confirmed_alias(db: Session):
    location = locations.resolve_or_create_manual(
        db, name="Goro Sefer", city="Addis Ababa"
    ).location
    db.commit()
    locations.add_alias(db, location, "Goro Sefer piazza")
    db.commit()
    # The curated "Piazza" is also a legitimate match, so only membership is
    # asserted: the point is that the alias's parent is found by its alias.
    assert location.id in {row.id for row in locations.search_locations(db, "piazza")}


def test_search_returns_nothing_for_a_rejected_match(db: Session):
    locations.resolve_or_create_manual(db, name="Goro Sefer", city="Addis Ababa")
    db.commit()
    locations.reject_location(db, 31)
    assert locations.search_locations(db, "goro") == []


# ---------------------------------------------------------------------------
# linking the areas a tutor already had (specification section 7)
# ---------------------------------------------------------------------------


_TUTOR_SEQUENCE = itertools.count(1)


def _tutor(db: Session, telegram_id: int | None = None) -> Tutor:
    sequence = next(_TUTOR_SEQUENCE)
    if telegram_id is None:
        telegram_id = -2000 - sequence
    tutor = Tutor(
        public_tutor_id=f"TDR-{sequence:06d}",
        telegram_user_id=telegram_id,
        name="Abebe Bekele",
        display_name="Abebe",
        phone="+251911234567",
        email="",
        country="Ethiopia",
        city="Addis Ababa",
        teaching_mode="IN_PERSON",
    )
    db.add(tutor)
    db.flush()
    return tutor


def _row(db: Session, tutor: Tutor, text: str, key: str | None = None) -> TutorLocation:
    row = TutorLocation(
        tutor_id=tutor.id,
        location=text,
        normalized_location=key or locations.normalize_name(text) or text.casefold(),
    )
    db.add(row)
    db.flush()
    return row


def test_a_tutors_existing_area_is_linked_without_changing_its_text(db: Session):
    """The spelling is theirs and stays theirs; only the link is added."""
    tutor = _tutor(db)
    row = _row(db, tutor, "Ayat Tsebel")
    db.commit()

    linked = locations.link_tutor_rows(db, tutor)
    db.commit()

    assert linked == 1
    assert row.location_id is not None
    assert row.location == "Ayat Tsebel"


def test_an_area_nobody_recognises_is_left_alone(db: Session):
    """Not matching anything is an honest outcome, not a failure."""
    tutor = _tutor(db)
    row = _row(db, tutor, "Somewhere Unheard Of")
    db.commit()

    assert locations.link_tutor_rows(db, tutor) == 0
    db.commit()
    assert row.location_id is None
    assert row.source == "IMPORTED"


def test_linking_is_idempotent(db: Session):
    tutor = _tutor(db)
    _row(db, tutor, "Bole")
    db.commit()

    assert locations.link_tutor_rows(db, tutor) == 1
    db.commit()
    assert locations.link_tutor_rows(db, tutor) == 0


def test_a_linked_row_is_never_re_assigned(db: Session):
    """The link is the tutor's answer; a later run must not overwrite it."""
    tutor = _tutor(db)
    row = _row(db, tutor, "Bole")
    locations.link_tutor_rows(db, tutor)
    other = locations.resolve_or_create_manual(
        db, name="Woreda 19", city="Addis Ababa"
    ).location
    row.location_id = other.id
    db.commit()

    locations.link_tutor_rows(db, tutor)
    db.commit()
    assert row.location_id == other.id


def test_the_unlinked_list_is_the_work_queue(db: Session):
    matched_tutor = _tutor(db)
    unmatched_tutor = _tutor(db)
    matched = _row(db, matched_tutor, "Bole")
    unmatched = _row(db, unmatched_tutor, "Somewhere Unheard Of")
    db.commit()

    locations.link_tutor_rows(db, matched_tutor)
    db.commit()

    queue = locations.unlinked_tutor_rows(db)
    assert [row.id for row in queue] == [unmatched.id]


def test_a_home_address_is_never_auto_linked(db: Session):
    """Specification section 7: never convert an address into a tutoring area.

    The linking reads only ``tutor.tutoring_locations``. An address lives on the
    tutor record, so it is never even considered here — and this pins that.
    """
    tutor = _tutor(db)
    tutor.current_address = "Ayat Tsebel"
    db.commit()

    assert locations.link_tutor_rows(db, tutor) == 0
    db.commit()
    assert not tutor.tutoring_locations
