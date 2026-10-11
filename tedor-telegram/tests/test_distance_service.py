"""The straight-line distance service.

Everything here is about one honesty rule: a distance is either measured and
labelled as straight-line, or reported as unknown. Never approximated, never
silently rounded into a claim about travel.
"""

from __future__ import annotations

import math

import pytest

from app.services.location_service import (
    DistanceCalculator,
    LocationAffinity,
    NearestArea,
    band_for_distance,
    calculate_distance_km,
    haversine_km,
    nearest_area,
)

# Addis Ababa landmarks with real coordinates.
AYAT_TSEBEL = (9.0302, 38.7540)
KAZANCHIS = (9.0242, 38.7580)      # ~0.8 km
BOLE = (8.9987, 38.7890)           # ~5 km
CMC = (9.0208, 38.8380)            # ~9 km
KALITY = (8.9129, 38.8007)         # ~14 km


class _Place:
    """A minimal stand-in for a ``Location`` row."""

    def __init__(self, name, coords=None, identifier=None):
        self.id = identifier
        self.name = name
        self.normalized_name = name.lower().replace(" ", "_")
        self.city = "Addis Ababa"
        self.latitude = None if coords is None else coords[0]
        self.longitude = None if coords is None else coords[1]


# ---------------------------------------------------------------------------
# known pairs
# ---------------------------------------------------------------------------


def test_a_point_is_zero_kilometres_from_itself():
    assert calculate_distance_km(*AYAT_TSEBEL, *AYAT_TSEBEL) == 0.0


def test_a_known_addis_pair_is_within_the_expected_range():
    """Kazanchis to Ayat Tsebel is about 0.8 km."""
    distance = calculate_distance_km(*KAZANCHIS, *AYAT_TSEBEL)
    assert 0.6 < distance < 1.0


@pytest.mark.parametrize(
    "start,end,low,high",
    [
        (AYAT_TSEBEL, BOLE, 4.5, 5.5),
        (AYAT_TSEBEL, CMC, 8.5, 9.5),
        (AYAT_TSEBEL, KALITY, 13.5, 14.5),
    ],
)
def test_addis_neighbourhood_distances(start, end, low, high):
    distance = calculate_distance_km(*start, *end)
    assert low < distance < high


def test_distance_is_symmetric():
    """A to B is B to A; a directed result would rank one tutor unfairly."""
    assert calculate_distance_km(*AYAT_TSEBEL, *BOLE) == pytest.approx(
        calculate_distance_km(*BOLE, *AYAT_TSEBEL)
    )


def test_a_known_long_haul_pair_matches_the_published_great_circle():
    """Addis Ababa to Nairobi is ~1167 km by great circle."""
    distance = calculate_distance_km(9.0302, 38.7469, -1.2921, 36.8219)
    assert 1150 < distance < 1185


def test_antipodal_points_are_half_the_circumference():
    """The maximum great-circle distance is half the globe's circumference."""
    distance = calculate_distance_km(10.0, 20.0, -10.0, -160.0)
    assert distance == pytest.approx(20015.1, rel=1e-3)


def test_the_old_name_still_works_and_agrees():
    assert haversine_km(*AYAT_TSEBEL, *BOLE) == calculate_distance_km(
        *AYAT_TSEBEL, *BOLE
    )


# ---------------------------------------------------------------------------
# invalid and missing input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        (None, None, None, None),
        (None, 38.7, 9.0, 38.7),
        (9.0, None, 9.0, 38.7),
        (9.0, 38.7, None, None),
        ("not-a-number", 38.7, 9.0, 38.7),
        (9.0, 38.7, "abc", "def"),
        (91.0, 38.7, 9.0, 38.7),
        (-91.0, 38.7, 9.0, 38.7),
        (9.0, 181.0, 9.0, 38.7),
        (9.0, -181.0, 9.0, 38.7),
        (float("nan"), 38.7, 9.0, 38.7),
        (float("inf"), 38.7, 9.0, 38.7),
        # The origin is "no point", not a point in the Gulf of Guinea.
        (0.0, 0.0, 9.0, 38.7),
    ],
)
def test_unusable_coordinates_report_unknown_rather_than_a_number(arguments):
    result = calculate_distance_km(*arguments)
    assert result is None, "an unusable coordinate must never become a distance"


def test_unknown_is_not_zero():
    """A caller that treats unknown as 0 would claim the tutor is next door."""
    assert calculate_distance_km(None, None, *BOLE) != 0.0


def test_numeric_strings_are_accepted():
    """Imported data arrives as text; that is not a reason to fail."""
    assert calculate_distance_km("9.0302", "38.7540", "8.9987", "38.7890") == (
        pytest.approx(calculate_distance_km(*AYAT_TSEBEL, *BOLE))
    )


# ---------------------------------------------------------------------------
# banding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "distance,expected_name",
    [
        # Zero is ADJACENT, not EXACT: EXACT means "the same place", which is
        # proven by identity, never by a distance of zero. Two distinct rows can
        # legitimately share a centroid.
        (0.0, "ADJACENT"),
        (1.9, "ADJACENT"),
        (2.1, "NEARBY"),
        (4.9, "NEARBY"),
        (5.1, "SAME_CITY"),
        (11.9, "SAME_CITY"),
        (12.1, "OTHER_CITY"),
        (400.0, "OTHER_CITY"),
    ],
)
def test_a_measured_distance_falls_into_a_documented_band(distance, expected_name):
    assert band_for_distance(distance).name == expected_name


def test_an_unmeasurable_distance_is_never_a_band():
    assert band_for_distance(None).name == "UNKNOWN"


# ---------------------------------------------------------------------------
# nearest selected area
# ---------------------------------------------------------------------------


def test_the_nearest_of_several_areas_is_chosen_by_actual_distance():
    areas = [_Place("Kality", KALITY), _Place("Bole", BOLE), _Place("Kazanchis", KAZANCHIS)]
    nearest = nearest_area(_Place("Ayat Tsebel", AYAT_TSEBEL), areas)
    assert nearest.name == "Kazanchis"
    assert nearest.distance_km == pytest.approx(0.8, abs=0.2)
    assert nearest.considered == 3
    assert not nearest.uncertain


def test_the_returned_name_and_distance_describe_the_same_area():
    """The bug this guards: an earlier area in a tie band winning the label
    while a different area's distance was reported."""
    areas = [
        _Place("Kality", KALITY),       # 14 km
        _Place("Bole", BOLE),           # 5 km
        _Place("Kazanchis", KAZANCHIS),  # 0.8 km
    ]
    nearest = nearest_area(_Place("Ayat Tsebel", AYAT_TSEBEL), areas)
    expected = calculate_distance_km(*AYAT_TSEBEL, *KAZANCHIS)
    assert nearest.distance_km == pytest.approx(round(expected, 2), abs=0.01)
    assert nearest.name == "Kazanchis"


def test_a_measured_area_beats_a_text_only_one_even_when_the_text_one_is_exact():
    """Coordinates are stronger evidence than a shared word, so they win."""
    areas = [_Place("Somewhere else"), _Place("Bole", BOLE)]
    nearest = nearest_area(_Place("Ayat Tsebel", AYAT_TSEBEL), areas)
    assert nearest.name == "Bole"
    assert nearest.has_distance


def test_the_same_place_is_zero_kilometres_not_an_unknown_distance():
    """The most certain match there is must not report "distance unknown".

    The band alone says EXACT and carries no figure, so reading the distance off
    it left the single strongest signal looking like missing data.
    """
    bole = _Place("Bole", BOLE, identifier=7)
    nearest = nearest_area(bole, [bole])
    assert nearest.distance_km == 0.0
    assert nearest.band is LocationAffinity.EXACT
    assert not nearest.uncertain


def test_the_same_named_place_under_two_rows_is_still_measured():
    """Two rows for one name: the match is exact but the coordinates are not
    the same, so a real figure is reported rather than a false zero."""
    left = _Place("Bole", BOLE, identifier=7)
    right = _Place("Bole", (8.99870, 38.78900), identifier=8)
    right.latitude, right.longitude = 8.99870, 38.78900
    nearest = nearest_area(left, [right])
    assert nearest.distance_km is not None
    assert nearest.band is LocationAffinity.EXACT


def test_no_areas_reports_unknown_with_no_invented_number():
    nearest = nearest_area(_Place("Ayat Tsebel", AYAT_TSEBEL), [])
    assert nearest.distance_km is None
    assert nearest.uncertain
    assert nearest.considered == 0
    assert "not recorded" in nearest.reason.lower()


def test_areas_without_coordinates_report_an_uncertain_distance():
    nearest = nearest_area(
        _Place("Ayat Tsebel", AYAT_TSEBEL), [_Place("Unplaced", None)]
    )
    assert nearest.distance_km is None
    assert nearest.uncertain
    assert "uncertain" in nearest.reason.lower() or "unrecognised" in nearest.reason.lower()


def test_an_online_job_reports_location_as_inapplicable():
    nearest = nearest_area(
        _Place("Ayat Tsebel", AYAT_TSEBEL), [_Place("Bole", BOLE)], online=True
    )
    assert nearest.band.name == "NOT_APPLICABLE"
    assert nearest.distance_km is None


def test_the_serialised_detail_labels_the_distance_kind():
    """A renderer must not have to guess whether these are road kilometres."""
    nearest = nearest_area(_Place("Ayat Tsebel", AYAT_TSEBEL), [_Place("Bole", BOLE)])
    assert nearest.to_dict()["distanceKind"] == "straight_line"


# ---------------------------------------------------------------------------
# caching within one run
# ---------------------------------------------------------------------------


def test_the_calculator_computes_a_repeated_pair_once():
    calculator = DistanceCalculator()
    first = calculator.distance_km(*AYAT_TSEBEL, *BOLE)
    second = calculator.distance_km(*AYAT_TSEBEL, *BOLE)
    assert first == second
    assert calculator.misses == 1
    assert calculator.hits == 1


def test_many_tutors_in_one_neighbourhood_reuse_one_calculation():
    calculator = DistanceCalculator()
    areas = [_Place("Bole", BOLE) for _ in range(50)]
    for _ in range(50):
        calculator.nearest(_Place("Ayat Tsebel", AYAT_TSEBEL), areas)
    # One real computation for the single distinct coordinate pair; the other
    # 49 look-ups are served from the cache.
    assert calculator.misses == 1
    assert calculator.hits >= 49


def test_cached_and_uncached_results_agree():
    uncached = DistanceCalculator().distance_km(*AYAT_TSEBEL, *CMC)
    calculator = DistanceCalculator()
    calculator.distance_km(*AYAT_TSEBEL, *CMC)
    assert calculator.distance_km(*AYAT_TSEBEL, *CMC) == uncached


def test_an_unknown_pair_is_not_cached():
    """Caching "unknown" would survive the fix that made it known."""
    calculator = DistanceCalculator()
    assert calculator.distance_km(None, None, *BOLE) is None
    assert calculator.distance_km(None, None, *BOLE) is None
    assert calculator.hits == 0


def test_each_calculator_starts_empty():
    """No process-wide cache, so one run cannot leak into the next."""
    first = DistanceCalculator()
    first.distance_km(*AYAT_TSEBEL, *BOLE)
    second = DistanceCalculator()
    second.distance_km(*AYAT_TSEBEL, *BOLE)
    assert second.hits == 0
    assert second.misses == 1


def test_nearest_uses_the_supplied_calculator():
    calculator = DistanceCalculator()
    areas = [_Place("Bole", BOLE)]
    nearest = calculator.nearest(_Place("Ayat Tsebel", AYAT_TSEBEL), areas)
    assert isinstance(nearest, NearestArea)
    assert calculator.misses == 1


def test_coordinate_precision_is_far_below_the_neighbourhood_scale():
    """The cache rounds to ~1 m; it must not change any displayed value."""
    exact = calculate_distance_km(9.03020, 38.75400, 8.99870, 38.78900)
    calculator = DistanceCalculator(precision=4)
    assert calculator.distance_km(9.03020, 38.75400, 8.99870, 38.78900) == (
        pytest.approx(exact)
    )
    assert math.isclose(exact, 5.200289, rel_tol=1e-6)