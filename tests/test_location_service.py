"""Tutoring-location normalisation and proximity scoring.

Two failure modes drive this suite: a tutor and an admin typing the same place
in different formats (which must not read as a mismatch), and location being
mistaken for a filter (which must never happen — no tutor is excluded).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings as hypothesis_settings
from hypothesis import strategies as st

from app.services import location_service as L

PROFILE = hypothesis_settings(max_examples=200, deadline=None)

#: Every spelling that must collapse onto one canonical key.
AYAT_SPELLINGS = (
    "Ayat Tsebel",
    "ayat tsebel",
    "AYAT TSEBEL",
    "Ayat_Tsebel",
    "ayat_tsebel",
    "Ayat-Tsebel",
    "#Ayat_Tsebel",
    "  Ayat   Tsebel  ",
    "Ayat Tsebel, Addis Ababa",
    "Ayat Tsebel Addis Ababa",
    "AYAT TSEBEL ETHIOPIA",
    "Ayat Tsebel, Addis Ababa, Ethiopia",
    "አይርት ጾብል",
    "Ayat",
    "Ayattsebel",
)

UNKNOWN_PLACEHOLDERS = ("", "   ", None, "-")


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", AYAT_SPELLINGS)
def test_every_spelling_of_ayat_tsebel_normalises_identically(spelling: str) -> None:
    assert L.normalize_location(spelling) == "ayat tsebel"
    assert L.canonical_name(spelling) == "Ayat Tsebel"
    assert L.location_zone(spelling) == L.LocationZone.NORTH


def test_the_three_formats_from_the_spec_are_equal() -> None:
    """Spec section 13: Ayat Tsebel / Ayat_Tsebel / Ayat Tsebel, Addis Ababa."""
    keys = {
        L.normalize_location("Ayat Tsebel"),
        L.normalize_location("Ayat_Tsebel"),
        L.normalize_location("Ayat Tsebel, Addis Ababa"),
    }
    assert keys == {"ayat tsebel"}


def test_alternative_spellings_share_one_key() -> None:
    """Kazanchis and Gefersa are the same district under two names."""
    assert L.normalize_location("Kazanchis") == L.normalize_location("gefersa")
    assert L.canonical_name("gefersa") == "Kazanchis"


def test_abbreviations_are_resolved() -> None:
    assert L.normalize_location("c.m.c") == "cmc"
    assert L.normalize_location("CMC") == "cmc"


def test_nothing_supplied_normalises_to_none() -> None:
    for value in UNKNOWN_PLACEHOLDERS:
        assert L.normalize_location(value) is None
        assert L.canonical_name(value) is None
        assert L.affinity(value, ["Bole"]) == L.LocationAffinity.NOT_APPLICABLE


def test_a_bare_city_suffix_means_the_whole_city() -> None:
    """``Addis Ababa`` alone is the city, not an unparseable address."""
    assert L.normalize_location("Addis Ababa") == L.CITY_WIDE
    assert L.normalize_location("addis ababa city") == L.CITY_WIDE
    assert L.location_zone("Addis Ababa") == L.LocationZone.CITY_WIDE


def test_an_unregistered_place_still_normalises_consistently() -> None:
    """Unknown places must not collapse to the same key as each other."""
    first = L.normalize_location("Kebele 07")
    second = L.normalize_location("Kebele 07")
    assert first == second == "kebele 07"
    assert first != L.normalize_location("Kebele 12")


def test_normalisation_is_idempotent() -> None:
    for spelling in AYAT_SPELLINGS:
        once = L.normalize_location(spelling)
        assert L.normalize_location(once) == once


@given(st.text(max_size=40))
@PROFILE
def test_normalisation_never_raises(garbage: str) -> None:
    """A tutor may type anything; parsing must not blow up."""
    key = L.normalize_location(garbage)
    if key is not None:
        assert isinstance(key, str)
        assert key == key.strip()
        assert key == key.casefold()


@given(st.text(max_size=40))
@PROFILE
def test_equal_input_always_yields_equal_key(garbage: str) -> None:
    assert L.normalize_location(garbage) == L.normalize_location(garbage)


# ---------------------------------------------------------------------------
# the proximity ladder (spec section 13)
# ---------------------------------------------------------------------------


def test_the_full_ladder_from_exact_to_outside_addis() -> None:
    """Same area -> nearby -> other Addis -> outside Addis, monotonically."""
    ladder = [
        (["Ayat Tsebel"], L.LocationAffinity.EXACT),
        (["Nifas Bekele"], L.LocationAffinity.NEARBY),
        (["Kazanchis"], L.LocationAffinity.NEARBY),
        (["Bole"], L.LocationAffinity.SAME_CITY),
        (["CMC"], L.LocationAffinity.SAME_CITY),
        (["Adama"], L.LocationAffinity.OTHER_CITY),
        (["Bahir Dar"], L.LocationAffinity.OTHER_CITY),
    ]
    scores = []
    for tutor_locations, expected in ladder:
        band = L.affinity("Ayat Tsebel", tutor_locations)
        assert band == expected, (tutor_locations, band)
        scores.append(L.AFFINITY_SCORE[band])
    assert scores == sorted(scores, reverse=True), scores


def test_adjacent_sub_cities_are_nearby_not_same_city() -> None:
    """Ayat Tsebel's true neighbours are closer than the far side of town."""
    assert L.affinity("Ayat Tsebel", ["Nifas Bekele"]) == L.LocationAffinity.NEARBY
    assert L.affinity("Ayat Tsebel", ["Bole"]) == L.LocationAffinity.SAME_CITY
    assert (
        L.AFFINITY_SCORE[L.LocationAffinity.NEARBY]
        > L.AFFINITY_SCORE[L.LocationAffinity.SAME_CITY]
    )


def test_same_zone_is_nearby_even_when_not_explicitly_adjacent() -> None:
    for job, tutor in (
        ("Gerji", "Alem Gena"),
        ("Shashemene", "Meron"),
        ("Piazza", "Ras Hailu"),
    ):
        assert L.affinity(job, [tutor]) == L.LocationAffinity.NEARBY, (job, tutor)


def test_a_city_wide_tutor_beats_a_distant_sub_city() -> None:
    assert L.affinity("Ayat Tsebel", ["Addis Ababa"]) == L.LocationAffinity.CITY_WIDE
    assert L.AFFINITY_SCORE[L.LocationAffinity.CITY_WIDE] > L.AFFINITY_SCORE[
        L.LocationAffinity.SAME_CITY
    ]


def test_matching_city_outside_addis_is_an_exact_match() -> None:
    assert L.affinity("Adama", ["Adama"]) == L.LocationAffinity.EXACT
    assert L.affinity("Bahir Dar", ["Adama"]) == L.LocationAffinity.OTHER_CITY


def test_another_city_is_never_automatically_excluded() -> None:
    """The lowest band still returns a positive score — location is a score."""
    assert L.AFFINITY_SCORE[L.LocationAffinity.OTHER_CITY] > 0
    assert L.affinity_score("Ayat Tsebel", ["Bahir Dar"]) > 0


# ---------------------------------------------------------------------------
# missing information
# ---------------------------------------------------------------------------


def test_a_tutor_with_no_locations_is_unknown_not_wrong() -> None:
    assert L.affinity("Ayat Tsebel", []) == L.LocationAffinity.UNKNOWN
    assert L.affinity("Ayat Tsebel", None) == L.LocationAffinity.UNKNOWN
    # Neutral: not better than a nearby tutor, not as bad as another city.
    assert L.AFFINITY_SCORE[L.LocationAffinity.UNKNOWN] > L.AFFINITY_SCORE[
        L.LocationAffinity.SAME_CITY
    ]
    assert L.AFFINITY_SCORE[L.LocationAffinity.UNKNOWN] > L.AFFINITY_SCORE[
        L.LocationAffinity.OTHER_CITY
    ]


def test_an_unrecognised_tutor_place_is_unknown() -> None:
    """We must not claim an unknown place is "in Addis"."""
    assert L.affinity("Ayat Tsebel", ["Remote Village"]) == L.LocationAffinity.UNKNOWN


# ---------------------------------------------------------------------------
# multiple locations
# ---------------------------------------------------------------------------


def test_the_best_of_several_locations_wins() -> None:
    assert (
        L.affinity("Ayat Tsebel", ["Bole", "Ayat_Tsebel", "Gerji"])
        == L.LocationAffinity.EXACT
    )


def test_several_locations_none_good_still_scores_the_best() -> None:
    assert L.affinity("Ayat Tsebel", ["Bole", "Adama"]) == L.LocationAffinity.SAME_CITY


def test_a_single_string_is_accepted_as_the_location() -> None:
    assert L.affinity("Ayat Tsebel", "Ayat Tsebel") == L.LocationAffinity.EXACT


def test_a_generator_of_locations_is_accepted() -> None:
    generator = (item for item in ["Bole", "Kazanchis"])
    assert L.affinity("Ayat Tsebel", generator) == L.LocationAffinity.NEARBY


# ---------------------------------------------------------------------------
# online jobs (spec section 12)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("marker", ["online", "Online", "remote", "Google Meet", "zoom"])
def test_online_markers_in_the_location_field(marker: str) -> None:
    assert L.looks_online(marker) is True
    assert L.affinity(marker, ["Bole"]) == L.LocationAffinity.NOT_APPLICABLE


def test_a_real_place_does_not_look_online() -> None:
    assert L.looks_online("Ayat Tsebel") is False
    assert L.looks_online("") is False


def test_an_explicit_online_flag_overrides_a_real_location() -> None:
    band = L.affinity("Ayat Tsebel", ["Ayat Tsebel"], online=True)
    assert band == L.LocationAffinity.NOT_APPLICABLE


def test_online_jobs_carry_no_location_signal() -> None:
    """A tutor anywhere scores identically for an online job."""
    scores = {
        L.affinity_score("Ayat Tsebel", [place], online=True)
        for place in ("Ayat Tsebel", "Bole", "Adama", "Bahir Dar", "")
    }
    assert len(scores) == 1, "online jobs must not vary by location"


# ---------------------------------------------------------------------------
# invariants
# ---------------------------------------------------------------------------


@given(st.sampled_from(sorted(L.LOCATIONS)))
@PROFILE
def test_every_registered_location_normalises_to_its_own_key(key: str) -> None:
    assert L.normalize_location(L.LOCATIONS[key].name) == key


@given(st.sampled_from(sorted(L.LOCATIONS)))
@PROFILE
def test_every_alias_resolves_to_its_own_location(key: str) -> None:
    for alias in L.LOCATIONS[key].aliases:
        assert L.normalize_location(alias) == key, alias


@given(st.sampled_from(sorted(L.LOCATIONS)))
@PROFILE
def test_every_location_matches_itself_exactly(key: str) -> None:
    band = L.affinity(L.LOCATIONS[key].name, [key])
    assert band == L.LocationAffinity.EXACT, (key, band)


@given(
    st.sampled_from(sorted(L.LOCATIONS)),
    st.sampled_from(sorted(L.LOCATIONS)),
)
@PROFILE
def test_a_location_always_scores_between_zero_and_one(left: str, right: str) -> None:
    score = L.affinity_score(L.LOCATIONS[left].name, [right])
    assert 0.0 <= score <= 1.0


@given(
    st.sampled_from(sorted(L.LOCATIONS)),
    st.sampled_from(sorted(L.LOCATIONS)),
)
@PROFILE
def test_affinity_is_symmetric(left: str, right: str) -> None:
    """Distance does not depend on which side is the job."""
    assert L.affinity(left, [right]) == L.affinity(right, [left])


def test_no_two_locations_share_a_key() -> None:
    """Distinct keys, so a tutor's areas never collapse into one."""
    normalised = [L.normalize_location(spec.name) for spec in L.LOCATIONS.values()]
    assert len(set(normalised)) == len(normalised)


def test_distance_bands_score_monotonically() -> None:
    """Every real distance, best to worst, has a non-increasing score."""
    scores = [L.AFFINITY_SCORE[band] for band in L._DISTANCE_BANDS]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores), "bands must not tie"


def test_unknown_is_neutral_not_worst() -> None:
    """No evidence must not be penalised below a known mismatch."""
    unknown = L.AFFINITY_SCORE[L.LocationAffinity.UNKNOWN]
    assert L.AFFINITY_SCORE[L.LocationAffinity.OTHER_CITY] < unknown
    assert unknown > 0


def test_a_known_area_is_reported_over_an_unrecognised_one() -> None:
    """Best-of-several prefers evidence, even though UNKNOWN scores higher."""
    band = L.affinity("Ayat Tsebel", ["Bole", "Remote Village"])
    assert band == L.LocationAffinity.SAME_CITY


def test_every_band_has_a_score() -> None:
    for band in L.LocationAffinity:
        assert 0.0 <= L.AFFINITY_SCORE[band] <= 1.0, band


def test_adjacency_pairs_reference_real_locations() -> None:
    for pair in L.ADJACENT_LOCATIONS:
        for key in pair:
            assert key in L.LOCATIONS, key
        # An adjacent pair must not contain a location with itself.
        assert len(pair) == 2


def test_adjacency_never_crosses_a_geographic_boundary() -> None:
    """Adjacent sub-cities are always inside Addis.

    Cross-zone pairs are fine and are the point of the explicit list (Arada
    sits on the north/central divide), but nothing outside the city and nothing
    city-wide may be declared a neighbour of a specific sub-city.
    """
    excluded = {L.LocationZone.OUTSIDE, L.LocationZone.CITY_WIDE}
    for pair in L.ADJACENT_LOCATIONS:
        for key in pair:
            assert L.LOCATIONS[key].zone not in excluded, pair