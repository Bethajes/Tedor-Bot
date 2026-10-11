"""Location-aware tutor matching.

Two things are being pinned down here. First, that proximity is *evidence*
rather than a filter: a tutor is never dropped for being far away, and a tutor
with no location data is reported as unknown rather than scored as a mismatch.
Second, that what an administrator is shown about proximity is honest — the
nearest selected area, the straight-line distance, and an explicit statement
when the distance could not be measured.
"""

from __future__ import annotations

import pytest

from app.models import TutorLocation
from app.services import job_match_pipeline, location_registry_service, matching_service
from tests.test_matching_service import ideal_tutor, publish_job  # noqa: F401

AYAT = (9.0302, 38.7540)
KAZANCHIS = (9.0242, 38.7580)
BOLE = (8.9987, 38.7890)
CMC = (9.0208, 38.8380)
KALITY = (8.9129, 38.8007)


@pytest.fixture(autouse=True)
def _seeded(db):
    location_registry_service.seed_registry(db)
    db.commit()
    return db


def place(db, name, coords=None):
    resolved = location_registry_service.resolve_or_create_manual(
        db, name=name, city="Addis Ababa"
    )
    location = resolved.location
    if coords is not None:
        location.latitude, location.longitude = coords
    db.commit()
    return location


def tutor_with_areas(db, telegram_id, areas):
    """A tutor whose tutoring areas are the given canonical locations."""
    tutor = ideal_tutor(db, telegram_id)
    db.expire_all()
    rows = {row.location: row for row in tutor.tutoring_locations}
    for area in areas:
        key = location_registry_service.normalize_name(area.name)
        row = rows.get(area.name)
        if row is None:
            row = TutorLocation(
                tutor_id=tutor.id,
                location=area.name,
                normalized_location=key,
            )
            db.add(row)
        row.location_id = area.id
        row.is_confirmed = True
        row.source = "SEARCH"
    db.commit()
    db.refresh(tutor)
    return tutor


# ---------------------------------------------------------------------------
# the nearest selected area decides the distance
# ---------------------------------------------------------------------------


def test_a_tutor_with_several_areas_is_measured_from_the_closest(db):
    place(db, "Ayat Tsebel", AYAT)
    kazanchis = place(db, "Kazanchis", KAZANCHIS)
    bole = place(db, "Bole", BOLE)
    kality = place(db, "Kality", KALITY)

    post = publish_job(db, location="Ayat Tsebel")
    tutor = tutor_with_areas(db, -8101, [bole, kality, kazanchis])

    result = matching_service.match(db, post, limit=5)[0]
    assert result.public_tutor_id == tutor.public_tutor_id
    assert result.nearest_area["name"] == "Kazanchis"
    assert result.nearest_area["distanceKm"] == pytest.approx(0.8, abs=0.2)


def test_a_closer_tutor_outranks_a_further_one(db):
    place(db, "Ayat Tsebel", AYAT)
    near = place(db, "Kazanchis", KAZANCHIS)
    far = place(db, "Kality", KALITY)

    post = publish_job(db, location="Ayat Tsebel")
    near_tutor = tutor_with_areas(db, -8111, [near])
    far_tutor = tutor_with_areas(db, -8112, [far])

    by_id = {
        item.public_tutor_id: item for item in matching_service.match(db, post, limit=5)
    }
    assert by_id[near_tutor.public_tutor_id].score > (
        by_id[far_tutor.public_tutor_id].score
    )
    assert by_id[near_tutor.public_tutor_id].distance_km < (
        by_id[far_tutor.public_tutor_id].distance_km
    )


def test_the_job_location_is_resolved_by_id(db):
    """The admin picks a canonical place; matching must use that row."""
    post = publish_job(db, location="Bole")
    with_bold = place(db, "Bole", BOLE)
    post.location_id = with_bold.id
    db.commit()

    ayat = place(db, "Ayat Tsebel", AYAT)
    tutor_with_areas(db, -8121, [ayat])

    result = matching_service.match(db, post, limit=5)[0]
    assert result.distance_km == pytest.approx(5.2, abs=0.2)


# ---------------------------------------------------------------------------
# unknown is not zero
# ---------------------------------------------------------------------------


def test_a_tutor_with_no_recorded_area_is_not_given_a_zero_distance(db):
    post = publish_job(db, location="Ayat Tsebel")
    tutor = ideal_tutor(db, -8131)
    tutor.tutoring_locations.clear()
    db.commit()

    result = matching_service.match(db, post, limit=5)[0]
    assert result.public_tutor_id == tutor.public_tutor_id
    assert result.distance_km is None
    # The city is used as a city-wide hint, explicitly labelled as not being a
    # tutoring area — never silently promoted to one.
    assert any("not their tutoring areas" in note for note in result.missing)


def test_an_area_without_coordinates_reports_an_unknown_distance(db):
    post = publish_job(db, location="Ayat Tsebel")
    place(db, "Ayat Tsebel", AYAT)
    unplaced = place(db, "Arada", None)          # no coordinates at all
    tutor_with_areas(db, -8141, [unplaced])

    result = matching_service.match(db, post, limit=5)[0]
    assert result.distance_km is None
    assert result.nearest_area["uncertain"] is True
    assert any("uncertain" in note for note in result.missing)


def test_an_unverified_place_is_still_usable_but_marked(db):
    """Location is evidence, not a gate: the tutor still appears."""
    post = publish_job(db, location="Ayat Tsebel")
    place(db, "Ayat Tsebel", AYAT)
    pending = place(db, "Koye", BOLE)
    assert pending.verification_status == "PENDING", "not a curated registry row"

    tutor = tutor_with_areas(db, -8151, [pending])
    results = matching_service.match(db, post, limit=5)
    assert tutor.public_tutor_id in {item.public_tutor_id for item in results}


def test_an_online_job_has_no_location_distance(db):
    post = publish_job(db, location="Ayat Tsebel", teaching_mode="ONLINE")
    place(db, "Ayat Tsebel", AYAT)
    bole = place(db, "Bole", BOLE)
    tutor_with_areas(db, -8161, [bole])

    result = matching_service.match(db, post, limit=5)[0]
    assert result.distance_km is None
    assert result.criterion("location").weight == 0.0


# ---------------------------------------------------------------------------
# fewer than five, and ties
# ---------------------------------------------------------------------------


def test_fewer_than_five_eligible_tutors_returns_fewer(db):
    post = publish_job(db, location="Ayat Tsebel")
    for index in range(3):
        ideal_tutor(db, -8200 - index)
    assert len(matching_service.match(db, post, limit=5)) == 3


def test_no_eligible_tutors_returns_an_empty_list(db):
    post = publish_job(db, location="Ayat Tsebel")
    assert matching_service.match(db, post, limit=5) == []


def test_ranking_ties_break_deterministically_on_the_tutor_id(db):
    post = publish_job(db, location="Ayat Tsebel")
    for index in range(5):
        ideal_tutor(db, -8300 - index)
    first = [item.public_tutor_id for item in matching_service.match(db, post, limit=5)]
    second = [item.public_tutor_id for item in matching_service.match(db, post, limit=5)]
    assert first == second
    assert first == sorted(first), "identical profiles must order by TDR id"


def test_ranks_are_assigned_in_order(db):
    post = publish_job(db, location="Ayat Tsebel")
    for index in range(3):
        ideal_tutor(db, -8400 - index)
    results = matching_service.match(db, post, limit=5)
    assert [item.rank for item in results] == [1, 2, 3]


# ---------------------------------------------------------------------------
# what the administrator is shown
# ---------------------------------------------------------------------------


def test_the_card_labels_the_distance_as_straight_line(db):
    post = publish_job(db, location="Ayat Tsebel")
    place(db, "Ayat Tsebel", AYAT)
    bole = place(db, "Bole", BOLE)
    tutor_with_areas(db, -8501, [bole])

    card = job_match_pipeline.format_candidate_card(
        matching_service.match(db, post, limit=5)[0]
    )
    assert "straight-line" in card
    assert "5.2 km" in card
    assert "Bole" in card


def test_the_card_never_calls_it_driving_distance(db):
    post = publish_job(db, location="Ayat Tsebel")
    place(db, "Ayat Tsebel", AYAT)
    bole = place(db, "Bole", BOLE)
    tutor_with_areas(db, -8511, [bole])

    card = job_match_pipeline.format_candidate_card(
        matching_service.match(db, post, limit=5)[0]
    )
    for forbidden in ("driving", "travel time", "journey", "road distance"):
        assert forbidden not in card.lower()


def test_the_card_says_the_distance_is_unknown_when_it_cannot_be_measured(db):
    post = publish_job(db, location="Ayat Tsebel")
    place(db, "Ayat Tsebel", AYAT)
    unplaced = place(db, "Arada", None)
    tutor_with_areas(db, -8521, [unplaced])

    card = job_match_pipeline.format_candidate_card(
        matching_service.match(db, post, limit=5)[0]
    )
    assert "distance unknown" in card
    assert "straight-line" not in card


def test_the_card_names_the_closest_area_not_the_whole_list(db):
    post = publish_job(db, location="Ayat Tsebel")
    place(db, "Ayat Tsebel", AYAT)
    kazanchis = place(db, "Kazanchis", KAZANCHIS)
    kality = place(db, "Kality", KALITY)
    tutor_with_areas(db, -8531, [kality, kazanchis])

    card = job_match_pipeline.format_candidate_card(
        matching_service.match(db, post, limit=5)[0]
    )
    assert "Closest area: Kazanchis" in card


def test_the_stored_breakdown_survives_into_the_candidate_row(db):
    post = publish_job(db, location="Ayat Tsebel")
    place(db, "Ayat Tsebel", AYAT)
    bole = place(db, "Bole", BOLE)
    tutor_with_areas(db, -8541, [bole])

    job_match_pipeline.run_match_for_job(db, post)
    rows = job_match_pipeline.get_job_candidates(db, post.public_post_id)
    details = job_match_pipeline.candidate_details(rows[0])

    assert details["nearest_area"]["name"] == "Bole"
    assert details["nearest_area"]["distanceKind"] == "straight_line"
    assert details["criteria"]["location"]["detail"]["distanceKm"] == pytest.approx(
        5.2, abs=0.2
    )


def test_the_breakdown_renderer_shows_the_weights_that_mattered(db):
    post = publish_job(db, location="Ayat Tsebel")
    ideal_tutor(db, -8551)
    job_match_pipeline.run_match_for_job(db, post)
    details = job_match_pipeline.candidate_details(
        job_match_pipeline.get_job_candidates(db, post.public_post_id)[0]
    )
    rendered = job_match_pipeline.score_breakdown(details)
    assert "Location" in rendered
    assert "weight" in rendered


def test_the_proximity_line_is_empty_for_an_online_job(db):
    assert job_match_pipeline.proximity_line({}) == ""
    assert job_match_pipeline.proximity_line({"nearest_area": None}) == ""

def test_a_tutor_in_the_job_s_place_reports_zero_not_unknown(db):
    """The strongest possible match must not look like missing data."""
    post = publish_job(db, location="Bole")
    bole = place(db, "Bole", BOLE)
    post.location_id = bole.id
    db.commit()

    tutor_with_areas(db, -8601, [bole])
    result = matching_service.match(db, post, limit=5)[0]

    assert result.distance_km == 0.0
    assert "0.0 km" in job_match_pipeline.format_candidate_card(result)
    assert "straight-line" in job_match_pipeline.format_candidate_card(result)
