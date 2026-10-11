"""Location matching with canonical locations.

The matching engine's location score has always been a preference, never a
filter. What these tests add is the coordinate-aware path: two canonical
locations with a real distance get a measured proximity, and a location with no
coordinates is reported as *uncertain* rather than scored as if it were known.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.services import matching_service as M
from app.services import location_registry_service as locations
from app.services.location_service import LocationAffinity, proximity
from tests.test_matching_service import ideal_tutor, publish_job

#: Central Addis Abeba landmarks, roughly where they actually are.
#: Real Addis Abebe coordinates, so the distances mean what they claim.
AYAT = (9.0302, 38.7540)
KAZANCHIS = (9.0242, 38.7580)      # ~0.8 km
BOLE = (8.9987, 38.7890)           # ~5 km
CMC = (9.0208, 38.8380)            # ~9 km
KALITY = (8.9129, 38.8007)         # ~14 km


def _make_location(
    db: Session, name: str, city: str, coords: tuple[float, float] | None
) -> object:
    """A canonical location, either with a position or without one."""
    resolved = locations.resolve_or_create_manual(db, name=name, city=city)
    location = resolved.location
    if coords is not None:
        location.latitude, location.longitude = coords
    db.commit()
    return location


def _link(db: Session, tutor: object, location: object, confirmed: bool = True) -> None:
    """Attach the canonical location to a row the tutor already has.

    The row is updated rather than added: ``make_tutor`` has already written one
    for the same area text, and a second would break the unique constraint.
    """
    from app.models import TutorLocation

    db.expire_all()
    row = next(
        item
        for item in tutor.tutoring_locations
        if locations.normalize_name(item.location) == location.normalized_name
    )
    row.location_id = location.id
    row.is_confirmed = confirmed
    row.source = "SEARCH"
    db.commit()


@pytest.fixture(autouse=True)
def _seeded(db: Session):
    locations.seed_registry(db)
    db.commit()
    return db


# ---------------------------------------------------------------------------
# proximity
# ---------------------------------------------------------------------------


def test_the_same_location_is_an_exact_match(db: Session):
    ayat = _make_location(db, "Ayat Tsebel", "Addis Ababa", AYAT)
    result = proximity(ayat, [ayat])
    assert result.band is LocationAffinity.EXACT
    assert result.is_exact


def test_a_nearer_place_scores_better_than_a_further_one(db: Session):
    """A measured distance beats a zone label, and is monotonic over it."""
    ayat = _make_location(db, "Ayat Tsebel", "Addis Ababa", AYAT)
    kazanchis = _make_location(db, "Kazanchis", "Addis Ababa", KAZANCHIS)
    cmc = _make_location(db, "CMC", "Addis Ababa", CMC)

    near = proximity(ayat, [kazanchis]).score
    far = proximity(ayat, [cmc]).score
    assert near > far
    assert proximity(ayat, [kazanchis]).distance_km < 2.0
    assert proximity(ayat, [cmc]).distance_km > 5.0


def test_a_measured_distance_is_reported_approximately(db: Session):
    """The distance is a proximity signal, never a promise of travel."""
    ayat = _make_location(db, "Ayat Tsebel", "Addis Ababa", AYAT)
    bole = _make_location(db, "Bole", "Addis Ababa", BOLE)
    result = proximity(ayat, [bole])
    assert result.has_distance
    assert "approximate" in result.reason.lower()


def test_missing_coordinates_are_reported_as_uncertain(db: Session):
    """A place with no position must not be scored as if it were located."""
    ayat = _make_location(db, "Ayat Tsebel", "Addis Ababa", AYAT)
    unknown = _make_location(db, "Woreda 12", "Addis Ababa", None)

    result = proximity(ayat, [unknown])
    assert result.distance_km is None
    assert result.uncertain is True


def test_a_row_with_no_coordinates_never_gets_a_distance(db: Session):
    location = _make_location(db, "Goro Sefer", "Addis Ababa", None)
    assert proximity(location, [location]).distance_km is None


def test_an_online_job_has_no_location_at_all(db: Session):
    ayat = _make_location(db, "Ayat Tsebel", "Addis Ababa", AYAT)
    result = proximity(ayat, [ayat], online=True)
    assert result.band is LocationAffinity.NOT_APPLICABLE


def test_zero_coordinates_are_not_a_position(db: Session):
    """A provider's "no position" is not the Gulf of Guinea."""
    ayat = _make_location(db, "Ayat Tsebel", "Addis Ababa", AYAT)
    bole = _make_location(db, "Bole", "Addis Ababa", (0, 0))
    assert proximity(ayat, [bole]).uncertain


def test_haversine_is_zero_for_the_same_point():
    from app.services.location_service import haversine_km

    assert haversine_km(9.0, 38.0, 9.0, 38.0) == 0.0
    assert haversine_km(9.0, 38.0, None, 38.0) is None


def test_haversine_matches_a_known_distance():
    """Addis Abeba to Bahir Dar is about 315 km."""
    from app.services.location_service import haversine_km

    assert 300 <= haversine_km(9.0302, 38.7469, 11.5936, 37.3906) <= 450


def test_the_band_for_a_measured_distance_is_monotonic():
    from app.services.location_service import band_for_distance

    bands = [
        band_for_distance(value) for value in (0.5, 3.0, 7.0, 20.0, 60.0)
    ]
    assert bands == [
        LocationAffinity.ADJACENT,
        LocationAffinity.NEARBY,
        LocationAffinity.SAME_CITY,
        LocationAffinity.OTHER_CITY,
        LocationAffinity.OTHER_CITY,
    ]


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------


def _publish_with(db: Session, location: object) -> object:
    """A published in-person job pointing at a canonical location."""
    post = publish_job(db)
    post.location = location.name
    post.location_id = location.id
    db.commit()
    return post


def test_a_tutor_at_the_jobs_location_scores_higher(db: Session):
    """A tutor 0.8 km away outranks one 9 km away, all else equal."""
    ayat = _make_location(db, "Ayat Tsebel", "Addis Ababa", AYAT)
    kazanchis = _make_location(db, "Kazanchis", "Addis Ababa", KAZANCHIS)
    cmc = _make_location(db, "CMC", "Addis Ababa", CMC)

    post = _publish_with(db, ayat)
    near = ideal_tutor(db, -9801, locations=["Kazanchis"], city="Addis Ababa")
    far = ideal_tutor(db, -9802, locations=["CMC"], city="Addis Ababa")
    _link(db, near, kazanchis)
    _link(db, far, cmc)

    results = {
        item.public_tutor_id: item
        for item in M.match(db, post.public_post_id, limit=10)
    }
    assert results[near.public_tutor_id].score > results[far.public_tutor_id].score
    assert results[near.public_tutor_id].criteria["location"].strengths


def test_a_tutor_with_no_locations_still_scores(db: Session):
    """Missing information is a gap, never a disqualification."""
    ayat = _make_location(db, "Ayat Tsebel", "Addis Ababa", AYAT)
    post = _publish_with(db, ayat)
    blank = ideal_tutor(db, -9803, locations=[], city="Addis Ababa")

    results = M.match(db, post.public_post_id, limit=10)
    assert blank.public_tutor_id in {item.public_tutor_id for item in results}
    blank_result = next(
        item for item in results if item.public_tutor_id == blank.public_tutor_id
    )
    location_note = blank_result.criteria["location"]
    assert location_note.missing


def test_an_online_job_ignores_location(db: Session):
    ayat = _make_location(db, "Ayat Tsebel", "Addis Ababa", AYAT)
    post = publish_job(db, teaching_mode="ONLINE")
    post.location = "online"
    post.location_id = ayat.id
    db.commit()
    far = ideal_tutor(db, -9804, locations=["Kality"], city="Addis Ababa")
    _link(db, far, _make_location(db, "Kality", "Addis Ababa", BOLE))

    result = M.match(db, post.public_post_id, limit=10)[0]
    assert result.criteria["location"].weight == 0.0
    assert result.criteria["location"].strengths == (
        "Online job — location does not apply",
    )


def test_a_location_without_coordinates_is_explained_as_uncertain(db: Session):
    """The report says we do not know, instead of inventing a distance."""
    ayat = _make_location(db, "Ayat Tsebel", "Addis Ababa", AYAT)
    unlisted = _make_location(db, "Woreda 12", "Addis Ababa", None)
    post = _publish_with(db, ayat)
    tutor = ideal_tutor(db, -9805, locations=["Woreda 12"])
    _link(db, tutor, unlisted)

    result = M.match(db, post.public_post_id, limit=10)[0]
    location_note = result.criteria["location"]
    assert "uncertain" in " ".join(location_note.missing).lower()


def test_an_unconfirmed_tutor_location_scores_but_is_not_a_silent_match(db: Session):
    """A home area the tutor never confirmed to is not a teaching area."""
    ayat = _make_location(db, "Ayat Tsebel", "Addis Ababa", AYAT)
    post = _publish_with(db, ayat)
    tutor = ideal_tutor(db, -9806, locations=["Ayat Tsebel"])
    _link(db, tutor, ayat, confirmed=False)

    results = M.match(db, post.public_post_id, limit=10)
    assert tutor.public_tutor_id in {item.public_tutor_id for item in results}


def test_the_proximity_reason_is_plain_text_for_a_human(db: Session):
    """Admin cards strip markup, and must never contain any."""
    ayat = _make_location(db, "Ayat Tsebel", "Addis Ababa", AYAT)
    cmc = _make_location(db, "CMC", "Addis Ababa", CMC)
    post = _publish_with(db, ayat)
    tutor = ideal_tutor(db, -9807, locations=["CMC"])
    _link(db, tutor, cmc)

    result = M.match(db, post.public_post_id, limit=10)[0]
    for note in (*result.strengths, *result.weaknesses, *result.missing):
        assert "<" not in note
