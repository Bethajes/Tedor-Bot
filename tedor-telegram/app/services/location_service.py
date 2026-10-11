"""Tutoring-location normalisation and proximity scoring.

A job's location and a tutor's locations are typed by different people in
different formats: ``Ayat Tsebel``, ``Ayat_Tsebel``, ``ayat tsebel``,
``Ayat Tsebel, Addis Ababa``. :func:`normalize_location` folds all of those onto
one canonical key so they compare equal, and :func:`affinity` turns a job
location plus a tutor's locations into a single proximity band.

Two rules shape this module:

* **Location is a preference, never a filter.** No function here excludes a
  tutor. A tutor in a different part of Addis still scores, and a tutor who
  never recorded a location is reported as *unknown* rather than as a
  mismatch — the 1,000+ imported tutors have no location rows at all.
* **Online jobs have no location.** :func:`affinity` returns
  :attr:`LocationAffinity.NOT_APPLICABLE` so the caller can drop the weight
  instead of scoring an unavoidable zero.

:func:`proximity` adds the coordinate-aware path on top of that ladder. It is
deliberately conservative:

* a distance between two neighbourhood centroids is an *approximate proximity
  signal*, never proof that a tutor can travel to a particular address, and
  nothing in the return value claims otherwise;
* when either side has no coordinates the result falls back to the zone ladder
  and is flagged ``uncertain``, so a caller can say "we don't know" instead of
  inventing a distance;
* it accepts anything shaped like a location, so this module stays free of ORM
  imports and remains testable with plain objects.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

__all__ = [
    "ADJACENT_LOCATIONS",
    "AFFINITY_SCORE",
    "CITY_WIDE",
    "COORDINATE_BANDS",
    "EARTH_RADIUS_KM",
    "LOCATION_NOISE_TOKENS",
    "LOCATIONS",
    "ONLINE_LOCATION_TOKENS",
    "DistanceCalculator",
    "LocationAffinity",
    "LocationLike",
    "LocationSpec",
    "LocationZone",
    "NearestArea",
    "Proximity",
    "affinity",
    "affinity_score",
    "band_for_distance",
    "calculate_distance_km",
    "canonical_name",
    "display_name",
    "haversine_km",
    "is_known_location",
    "location_zone",
    "looks_online",
    "nearest_area",
    "normalize_location",
    "proximity",
]


class LocationZone(StrEnum):
    """Coarse geographic grouping used to derive "nearby"."""

    #: North-west corridor: Ayat Tsebel through to Kolfe Keranio.
    NORTH = "NORTH"
    #: East: the Bole / Gerji / Alem Gena side of the city.
    EAST = "EAST"
    #: South: Shashemene and beyond.
    SOUTH = "SOUTH"
    #: Central: the CMC / Piazza / Merkato / Kirkos core.
    CENTRAL = "CENTRAL"
    #: The whole of Addis Ababa — a tutor who covers anywhere in the city.
    CITY_WIDE = "CITY_WIDE"
    #: A known Ethiopian city outside Addis Ababa.
    OUTSIDE = "OUTSIDE"
    #: Not a place we recognise; the raw phrase is used as the key.
    UNKNOWN = "UNKNOWN"


class LocationAffinity(StrEnum):
    """How close a tutor's tutoring areas are to a job's area.

    Ordered best to worst. :attr:`UNKNOWN` and :attr:`NOT_APPLICABLE` are
    distinct: unknown means no evidence either way, not-applicable means the
    question does not arise (an online job).
    """

    #: Same sub-city.
    EXACT = "EXACT"

    #: Both sides have coordinates and are within ~2 km. Only ever produced by
    #: :func:`proximity`, never by the zone ladder.
    ADJACENT = "ADJACENT"
    #: The tutor covers the whole city.
    CITY_WIDE = "CITY_WIDE"
    #: An adjacent sub-city, or another one in the same zone.
    NEARBY = "NEARBY"
    #: A different part of Addis Ababa.
    SAME_CITY = "SAME_CITY"
    #: One or both sides unrecorded — no evidence, so no penalty.
    UNKNOWN = "UNKNOWN"
    #: A known Ethiopian city outside Addis Ababa.
    OTHER_CITY = "OTHER_CITY"
    #: The job is online, or neither side named a place.
    NOT_APPLICABLE = "NOT_APPLICABLE"


# ---------------------------------------------------------------------------
# The canonical registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocationSpec:
    """One canonical place and everything that maps onto it."""

    key: str
    name: str
    zone: LocationZone
    #: Extra spellings, transliterations and abbreviations.
    aliases: tuple[str, ...] = ()


def _spec(
    key: str, name: str, zone: LocationZone, *aliases: str
) -> tuple[str, LocationSpec]:
    return key, LocationSpec(key=key, name=name, zone=zone, aliases=aliases)


#: Key -> specification. Sub-city zones follow the city's geography: the
#: north-west corridor (Ayat Tsebel -> Kazanchis -> Kolfe Keranio) and the
#: east/south arc (Bole -> Gerji -> Alem Gena -> Shashemene) are contiguous
#: neighbourhoods, while CMC/Piazza sit in the middle.
LOCATIONS: Final[dict[str, LocationSpec]] = dict(
    (
        # -- north-west corridor ----------------------------------------
        _spec(
            "ayat tsebel",
            "Ayat Tsebel",
            LocationZone.NORTH,
            "ayat",
            "tsebel",
            "ayattsebel",
            "ayat sebel",
            "aykiru bereta",
            "አይርት ጾብል",
        ),
        _spec("nifas bekele", "Nifas Bekele", LocationZone.NORTH, "nifas"),
        _spec(
            "gefersa",
            "Kazanchis",
            LocationZone.NORTH,
            "kazanchis",
            "kazanche",
            "ጌፋርሳ",
        ),
        _spec("kolfe keranio", "Kolfe Keranio", LocationZone.NORTH, "kolfe", "keranio"),
        _spec("lideta", "Lideta", LocationZone.NORTH),
        _spec("arada", "Arada", LocationZone.NORTH, "አራዳ"),
        # -- east -------------------------------------------------------
        _spec("bole", "Bole", LocationZone.EAST, "ቦሌ", "bole medhanialem"),
        _spec("gerji", "Gerji", LocationZone.EAST, "ግርጅ"),
        _spec("alem gena", "Alem Gena", LocationZone.EAST, "alemgena"),
        _spec("kality", "Kality", LocationZone.EAST, "cality"),
        _spec("kalanshite", "Kalanshite", LocationZone.EAST),
        _spec("aybet", "Aybet", LocationZone.EAST),
        # -- south ------------------------------------------------------
        _spec("shashemene", "Shashemene", LocationZone.SOUTH, "ሻሸመኔ", "shashemena"),
        _spec("meron", "Meron", LocationZone.SOUTH),
        _spec("vuren", "Vuren", LocationZone.SOUTH),
        _spec("adaba", "Adaba", LocationZone.SOUTH),
        # -- central ----------------------------------------------------
        _spec("cmc", "CMC", LocationZone.CENTRAL, "c.m.c", "ሲ.ኤም.ሲ"),
        _spec("piazza", "Piazza", LocationZone.CENTRAL, "ፒያዛ"),
        _spec("merkato", "Merkato", LocationZone.CENTRAL, "መርካቶ", "new merkato"),
        _spec("ras hailu", "Ras Hailu", LocationZone.CENTRAL),
        _spec("kirkos", "Kirkos", LocationZone.CENTRAL, "kirkos mekanisa"),
        # -- the city as a whole ----------------------------------------
        _spec(
            "addis ababa",
            "Addis Ababa",
            LocationZone.CITY_WIDE,
            "addisababa",
            "አዲስ አበባ",
        ),
        # -- known cities outside Addis ---------------------------------
        _spec("adama", "Adama", LocationZone.OUTSIDE, "nazret"),
        _spec("adigrat", "Adigrat", LocationZone.OUTSIDE),
        _spec("bahir dar", "Bahir Dar", LocationZone.OUTSIDE, "bahirdar"),
        _spec("hawassa", "Hawassa", LocationZone.OUTSIDE),
        _spec("jimma", "Jimma", LocationZone.OUTSIDE),
        _spec("mekelle", "Mekelle", LocationZone.OUTSIDE, "mekelle"),
        _spec("gondar", "Gondar", LocationZone.OUTSIDE),
        _spec("dire dawa", "Dire Dawa", LocationZone.OUTSIDE, "dire dawa"),
    )
)

#: The key of the entry covering the whole city.
CITY_WIDE: Final[str] = "addis ababa"

#: Explicitly adjacent pairs — sub-cities that are genuinely close by rather
#: than merely in the same zone. Unordered: each pair appears once.
ADJACENT_LOCATIONS: Final[frozenset[frozenset[str]]] = frozenset(
    {
        frozenset({"ayat tsebel", "nifas bekele"}),
        frozenset({"nifas bekele", "gefersa"}),
        frozenset({"nifas bekele", "kolfe keranio"}),
        frozenset({"gefersa", "kolfe keranio"}),
        frozenset({"gefersa", "lideta"}),
        frozenset({"kolfe keranio", "lideta"}),
        frozenset({"kolfe keranio", "arada"}),
        frozenset({"lideta", "arada"}),
        frozenset({"bole", "gerji"}),
        frozenset({"bole", "kality"}),
        frozenset({"bole", "kalanshite"}),
        frozenset({"gerji", "alem gena"}),
        frozenset({"gerji", "shashemene"}),
        frozenset({"alem gena", "kality"}),
        frozenset({"alem gena", "shashemene"}),
        frozenset({"shashemene", "meron"}),
        frozenset({"shashemene", "adaba"}),
        frozenset({"meron", "vuren"}),
        frozenset({"cmc", "piazza"}),
        frozenset({"cmc", "merkato"}),
        frozenset({"cmc", "ras hailu"}),
        frozenset({"cmc", "kirkos"}),
        frozenset({"piazza", "merkato"}),
        frozenset({"merkato", "ras hailu"}),
        frozenset({"ras hailu", "kirkos"}),
        frozenset({"kirkos", "arada"}),
        frozenset({"kirkos", "meron"}),
    }
)

#: Score contribution per band, on the same 0-1 scale as every other criterion.
#:
#: ``UNKNOWN`` sits at the midpoint on purpose. A tutor who never filled in
#: their areas has given no evidence, which is not the same as evidence of a
#: bad fit — the matching report lists it under missing information instead.
AFFINITY_SCORE: Final[dict[LocationAffinity, float]] = {
    LocationAffinity.EXACT: 1.00,
    LocationAffinity.ADJACENT: 0.92,
    LocationAffinity.CITY_WIDE: 0.85,
    LocationAffinity.NEARBY: 0.70,
    LocationAffinity.SAME_CITY: 0.45,
    LocationAffinity.UNKNOWN: 0.50,
    LocationAffinity.OTHER_CITY: 0.20,
    LocationAffinity.NOT_APPLICABLE: 0.50,
}

#: Tokens that carry no locational meaning and are stripped before matching.
#: This is what makes ``"Ayat Tsebel, Addis Ababa"`` equal ``"Ayat Tsebel"``.
#: Tokens that carry no locational meaning and are stripped before matching.
#: This is what makes ``"Ayat Tsebel, Addis Ababa"`` equal ``"Ayat Tsebel"``.
#:
#: ``woreda``/``kebele`` are deliberately absent: they are the names of real
#: Addis sub-areas, so stripping them would turn "Kebele 07" into "07".
LOCATION_NOISE_TOKENS: Final[frozenset[str]] = frozenset(
    {"addis", "ababa", "ethiopia", "city", "subcity"}
)

#: Markers an admin may type in the location field to mean "no fixed place".
ONLINE_LOCATION_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "online",
        "remote",
        "virtual",
        "zoom",
        "google meet",
        "skype",
        "anywhere",
        "addis ababa online",
        "online only",
    }
)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")
_UNDERSCORE_RE = re.compile(r"_")


def _clean(value: object) -> str:
    """Lower-case, de-accent, collapse punctuation to single spaces.

    ``_`` is a word character in ``\\w``, so it is split explicitly: the
    hashtag form ``Ayat_Tsebel`` has to compare equal to ``Ayat Tsebel``.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = text.lstrip("#").strip()
    text = _UNDERSCORE_RE.sub(" ", text)
    text = _NON_WORD_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_location(value: object) -> str | None:
    """Fold a typed location onto its canonical key.

    ``Ayat Tsebel``, ``ayat_tsebel`` and ``Ayat Tsebel, Addis Ababa`` all
    return ``"ayat tsebel"``. An unrecognised place returns its own cleaned
    phrase so it still compares consistently across two spellings; ``None``
    means nothing was supplied.
    """
    text = _clean(value)
    if not text:
        return None

    tokens = [
        token for token in text.split(" ") if token not in LOCATION_NOISE_TOKENS
    ]
    if not tokens:
        # The input was nothing but a city suffix, e.g. "Addis Ababa".
        return CITY_WIDE
    text = " ".join(tokens)

    known = _ALIAS_INDEX.get(text)
    if known is not None:
        return known
    # Fall back to a single-token match so "Bole" resolves from "Bole, Addis".
    if len(tokens) == 1:
        return _ALIAS_INDEX.get(tokens[0], text)
    return text


def _build_alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for spec in LOCATIONS.values():
        candidates = (spec.key, spec.name, *spec.aliases)
        for candidate in candidates:
            cleaned = _clean(candidate)
            if cleaned:
                index.setdefault(cleaned, spec.key)
    return index


#: Cleaned spelling -> canonical key.
_ALIAS_INDEX: Final[dict[str, str]] = _build_alias_index()


def canonical_name(value: object) -> str | None:
    """The display name for a location, e.g. ``"gefersa"`` -> ``"Kazanchis"``."""
    key = normalize_location(value)
    if key is None:
        return None
    spec = LOCATIONS.get(key)
    return spec.name if spec else key.title()


#: Friendlier alias for callers that want the display name.
display_name = canonical_name


def location_zone(value: object) -> LocationZone:
    """The coarse zone for a location; :attr:`LocationZone.UNKNOWN` if new."""
    key = normalize_location(value)
    if key is None:
        return LocationZone.UNKNOWN
    spec = LOCATIONS.get(key)
    return spec.zone if spec else LocationZone.UNKNOWN


def is_known_location(value: object) -> bool:
    """Whether the location is in the curated registry."""
    key = normalize_location(value)
    return key in LOCATIONS if key else False


def looks_online(value: object) -> bool:
    """Whether a job's location field actually means "online"."""
    text = _clean(value)
    if not text:
        return False
    if text in _ONLINE_INDEX:
        return True
    tokens = [token for token in text.split(" ") if token not in LOCATION_NOISE_TOKENS]
    return bool(tokens) and " ".join(tokens) in _ONLINE_INDEX


_ONLINE_INDEX: Final[frozenset[str]] = frozenset(
    _clean(token) for token in ONLINE_LOCATION_TOKENS
)


# ---------------------------------------------------------------------------
# Affinity
# ---------------------------------------------------------------------------


def _pair(left: str, right: str) -> bool:
    return frozenset({left, right}) in ADJACENT_LOCATIONS


def _affinity_for_keys(job_key: str, tutor_key: str) -> LocationAffinity:
    if job_key == tutor_key:
        # The same place on both sides is as exact as it gets, including
        # "Addis Ababa" — that is a match, not merely city-wide coverage.
        return LocationAffinity.EXACT

    job = LOCATIONS.get(job_key)
    tutor = LOCATIONS.get(tutor_key)
    if job is None or tutor is None:
        # At least one side is a place the registry does not know. "Same city"
        # would claim the tutor is in Addis when we simply do not know where
        # they are, so an unrecognised place is treated as no evidence.
        known_zone = (job or tutor).zone if (job or tutor) else None
        if known_zone == LocationZone.CITY_WIDE:
            return LocationAffinity.CITY_WIDE
        return LocationAffinity.UNKNOWN

    if job.zone == LocationZone.CITY_WIDE or tutor.zone == LocationZone.CITY_WIDE:
        return LocationAffinity.CITY_WIDE
    if job.zone == LocationZone.OUTSIDE or tutor.zone == LocationZone.OUTSIDE:
        # One known city outside Addis and the other inside it.
        return LocationAffinity.OTHER_CITY
    if _pair(job_key, tutor_key) or job.zone == tutor.zone:
        return LocationAffinity.NEARBY
    return LocationAffinity.SAME_CITY


def affinity(
    job_location: object,
    tutor_locations: object = None,
    *,
    online: bool = False,
) -> LocationAffinity:
    """Best proximity band between a job's area and a tutor's areas.

    ``tutor_locations`` may be a single string or any iterable of strings;
    several areas are the normal case, so the best (lowest ordinal) band wins.
    ``online=True`` short-circuits to :attr:`LocationAffinity.NOT_APPLICABLE`.
    """
    if online or looks_online(job_location):
        return LocationAffinity.NOT_APPLICABLE

    job_key = normalize_location(job_location)
    if job_key is None:
        return LocationAffinity.NOT_APPLICABLE

    candidates = _as_sequence(tutor_locations)
    keys = [normalize_location(item) for item in candidates]
    known = [key for key in keys if key]
    if not known:
        return LocationAffinity.UNKNOWN

    bands = [_affinity_for_keys(job_key, key) for key in known]
    return min(bands, key=lambda band: _AFFINITY_ORDER.index(band))


#: Selection priority when a tutor lists several areas: :func:`affinity` returns
#: the band with the lowest ordinal.
#:
#: This is ordered by *evidence quality*, which is deliberately not the same as
#: score order. ``UNKNOWN`` scores 0.50 — above ``SAME_CITY`` at 0.45, because
#: no information beats mild evidence against — yet it is selected last, so a
#: tutor who genuinely covers Bole is never reported as merely "unknown" just
#: because one of their other areas was not in the registry.
_AFFINITY_ORDER: Final[tuple[LocationAffinity, ...]] = (
    LocationAffinity.EXACT,
    LocationAffinity.ADJACENT,
    LocationAffinity.CITY_WIDE,
    LocationAffinity.NEARBY,
    LocationAffinity.SAME_CITY,
    LocationAffinity.OTHER_CITY,
    LocationAffinity.UNKNOWN,
    LocationAffinity.NOT_APPLICABLE,
)

#: Bands that describe a real, measured distance. Ordered best to worst, so a
#: caller can reason about location as a monotonic signal.
_DISTANCE_BANDS: Final[tuple[LocationAffinity, ...]] = (
    LocationAffinity.EXACT,
    LocationAffinity.ADJACENT,
    LocationAffinity.CITY_WIDE,
    LocationAffinity.NEARBY,
    LocationAffinity.SAME_CITY,
    LocationAffinity.OTHER_CITY,
)


def _as_sequence(value: object) -> list[Any]:
    """Accept ``None``, a string, a location row, or any iterable of those.

    Items are passed through **unchanged**. An earlier version coerced each to
    ``str``, which was right for the text-only ladder but silently turned a
    :class:`Location` into its repr — so the coordinate path received
    ``"<app.models.Location object at 0x…>"`` and every location looked
    unknown. Converting here is what made the measured-distance path
    unreachable.
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    try:
        return [item for item in value if item is not None and str(item).strip()]
    except TypeError:
        return [value]


def affinity_score(
    job_location: object,
    tutor_locations: object = None,
    *,
    online: bool = False,
) -> float:
    """The 0-1 score for :func:`affinity`, for the matching engine."""
    return AFFINITY_SCORE[affinity(job_location, tutor_locations, online=online)]

# ---------------------------------------------------------------------------
# Coordinate-aware proximity
# ---------------------------------------------------------------------------
#
# The zone ladder above answers "which part of town is this?". When both sides
# of a match carry coordinates we can answer the sharper question — how far
# apart are these two points — which is better evidence and worth using.


#: Earth radius in kilometres (IUGG mean radius). Good to about 0.5%, which is
#: far tighter than the neighbourhood-level precision this is used at.
EARTH_RADIUS_KM: Final[float] = 6371.0088

#: Distance thresholds, tightest first. A pair inside 2 km is treated as the
#: same neighbourhood; past 12 km the two places are in different parts of the
#: city and the band stops claiming closeness at all.
COORDINATE_BANDS: Final[tuple[tuple[float, LocationAffinity], ...]] = (
    (2.0, LocationAffinity.ADJACENT),
    (5.0, LocationAffinity.NEARBY),
    (12.0, LocationAffinity.SAME_CITY),
)

#: Earth's full turn at the equator, used to sanity-check coordinates.
_MAX_LATITUDE: Final[float] = 90.0
_MAX_LONGITUDE: Final[float] = 180.0


@runtime_checkable
class LocationLike(Protocol):
    """Anything that can stand in for a location row.

    Matches ``app.models.Location`` and anything a test builds by hand, which
    keeps this module free of ORM imports.
    """

    id: int | None
    name: str
    normalized_name: str
    city: str
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class Proximity:
    """How close a tutor's areas are to a job's area, and how sure we are.

    ``distance_km`` is ``None`` whenever it could not be computed, which is the
    common case for the 1,000+ imported tutors and for every manually entered
    location. ``uncertain`` says the same thing to a caller that wants to
    explain it to a human.
    """

    band: LocationAffinity
    score: float
    #: Approximate great-circle distance between the two locations' reference
    #: points, in kilometres. An indication of closeness — not a journey time,
    #: and not a claim that a tutor can reach a specific address.
    distance_km: float | None = None
    #: ``True`` when the band came from the zone ladder because at least one
    #: side has no coordinates. The band is a weaker claim, and any
    #: recommendation built on it should say so.
    uncertain: bool = False
    #: Which of the tutor's areas produced the result, for the explanation.
    matched_name: str | None = None
    #: A short, honest sentence for an admin recommendation card.
    reason: str = ""

    @property
    def is_exact(self) -> bool:
        return self.band is LocationAffinity.EXACT

    @property
    def has_distance(self) -> bool:
        return self.distance_km is not None


def _coordinates_of(value: object) -> tuple[float, float] | None:
    """A valid ``(latitude, longitude)`` pair read off a location, or ``None``.

    Accepts anything shaped like a location row, a bare ``"lat, lon"`` string,
    or an already-extracted ``(latitude, longitude)`` pair — a job's location
    may still be raw text, and the calculator hands tuples back in.
    """
    if isinstance(value, str):
        text_value = value.strip()
        if "," not in text_value:
            return None
        latitude_text, _, longitude_text = text_value.partition(",")
        return _validated_point(latitude_text.strip(), longitude_text.strip())
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        if len(value) != 2:
            return None
        return _validated_point(value[0], value[1])
    return _validated_point(
        getattr(value, "latitude", None), getattr(value, "longitude", None)
    )


def haversine_km(
    latitude_a: float | None,
    longitude_a: float | None,
    latitude_b: float | None,
    longitude_b: float | None,
) -> float | None:
    """Great-circle distance in kilometres, or ``None`` if either point is unknown.

    ``None`` means "we do not know how far apart these are", which callers must
    report as uncertainty rather than substituting a guess. Retained as the
    historical name; :func:`calculate_distance_km` is the public entry point.
    """
    if None in (latitude_a, longitude_a, latitude_b, longitude_b):
        return None
    return calculate_distance_km(latitude_a, longitude_a, latitude_b, longitude_b)


def band_for_distance(distance_km: float | None) -> LocationAffinity:
    """The band a measured distance falls into.

    Anything beyond the last threshold is :attr:`LocationAffinity.OTHER_CITY`;
    ``None`` is :attr:`LocationAffinity.UNKNOWN`, never a real band.
    """
    if distance_km is None:
        return LocationAffinity.UNKNOWN
    for limit, band in COORDINATE_BANDS:
        if distance_km <= limit:
            return band
    return LocationAffinity.OTHER_CITY


# ---------------------------------------------------------------------------
# Distance service
# ---------------------------------------------------------------------------
#
# This is the straight-line layer. It answers "how far apart are these two
# points", and deliberately answers nothing else: it is not a road network, it
# does not know about traffic, one-way systems, or whether a bridge is open.
# Every value it produces is great-circle distance in kilometres between two
# reference points — an *approximate proximity signal* and nothing more.
# Nothing produced here may be described to a user as driving distance or as a
# travel time.
#
# The seam for real routing is deliberately left open: :class:`DistanceCalculator`
# is the one place every distance is computed, so replacing the great-circle
# implementation with a routing provider later means reimplementing one method
# rather than hunting every call site.


def calculate_distance_km(
    lat1: float | str | None,
    lon1: float | str | None,
    lat2: float | str | None,
    lon2: float | str | None,
) -> float | None:
    """Straight-line distance in kilometres between two points.

    This is the stable public entry point named in the specification. Every
    argument is validated and ``None`` is returned — rather than raising, or
    substituting a guess — when a coordinate is missing, unparseable, or out of
    range.

    ``None`` means *unknown*, and callers must report it as uncertainty. It is
    never ``0``: a zero would claim the tutor teaches in the job's street.
    """
    point_a = _validated_point(lat1, lon1)
    point_b = _validated_point(lat2, lon2)
    if point_a is None or point_b is None:
        return None
    return _great_circle_km(*point_a, *point_b)


def _validated_point(
    latitude: float | str | None, longitude: float | str | None
) -> tuple[float, float] | None:
    """One ``(latitude, longitude)`` pair if both are valid, else ``None``.

    The origin is rejected deliberately. A geocoder that has no point for a
    place may answer ``0, 0`` rather than with nothing, and scoring that as a
    real position would place a neighbourhood off the coast of Africa.
    """
    try:
        lat = float(latitude)  # type: ignore[arg-type]
        lon = float(longitude)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(lat) or math.isnan(lon):
        return None
    if not -_MAX_LATITUDE <= lat <= _MAX_LATITUDE:
        return None
    if not -_MAX_LONGITUDE <= lon <= _MAX_LONGITUDE:
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return lat, lon


def _great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine great-circle distance between two already-valid points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(haversine)))


def _identity_of(value: object) -> tuple[int | None, str | None]:
    """``(id, normalized_name)`` for a location row, or ``(None, key)`` for text.

    A shared id is the strongest possible evidence that two sides mean the
    same place; a shared normalized name is the next best, and is what a text
    comparison has always used.
    """
    if value is None or isinstance(value, str):
        return None, normalize_location(value)
    identifier = getattr(value, "id", None)
    key = getattr(value, "normalized_name", None)
    return (
        identifier if isinstance(identifier, int) else None,
        normalize_location(key) if key else None,
    )


def _label_of(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return canonical_name(value)
    name = getattr(value, "name", None)
    return str(name) if name else None


def proximity(
    job_location: object,
    tutor_locations: object = None,
    *,
    online: bool = False,
    calculator: "DistanceCalculator | None" = None,
) -> Proximity:
    """Proximity of a tutor's areas to a job's area, with its confidence.

    Resolution order for each tutor area:

    1. **Same row, or same canonical name** → :attr:`LocationAffinity.EXACT`.
       This also covers a spelling an admin confirmed as an alias, because the
       alias resolves to the same row before this function is called.
    2. **Both sides have coordinates** → measured distance, banded by
       :data:`COORDINATE_BANDS`, and reported with the distance.
    3. **Anything else** → the zone ladder, flagged ``uncertain``.

    ``online=True`` short-circuits to :attr:`LocationAffinity.NOT_APPLICABLE`,
    which the caller turns into a zero weight rather than a zero score.

    ``calculator`` is supplied by callers that score many tutors against one
    job, so the same job-to-area distance is computed once per run instead of
    once per candidate.
    """
    if online or looks_online(job_location):
        return Proximity(
            band=LocationAffinity.NOT_APPLICABLE,
            score=AFFINITY_SCORE[LocationAffinity.NOT_APPLICABLE],
            reason="Online job — location does not apply.",
        )

    job_id, job_key = _identity_of(job_location)
    if job_key is None and job_id is None:
        return Proximity(
            band=LocationAffinity.NOT_APPLICABLE,
            score=AFFINITY_SCORE[LocationAffinity.NOT_APPLICABLE],
            reason="The job's location could not be read.",
        )

    candidates = _as_sequence(tutor_locations)
    if not candidates:
        return Proximity(
            band=LocationAffinity.UNKNOWN,
            score=AFFINITY_SCORE[LocationAffinity.UNKNOWN],
            uncertain=True,
            reason="The tutor has not recorded any tutoring areas.",
        )

    job_coords = _coordinates_of(job_location)
    measure = calculator if calculator is not None else DistanceCalculator()
    best: Proximity | None = None

    for candidate in candidates:
        area_id, area_key = _identity_of(candidate)
        label = _label_of(candidate)

        if job_id is not None and area_id is not None and job_id == area_id:
            result = Proximity(
                band=LocationAffinity.EXACT,
                score=AFFINITY_SCORE[LocationAffinity.EXACT],
                matched_name=label,
                reason=f"Same location: {label}." if label else "Same location.",
            )
        elif job_key is not None and area_key is not None and job_key == area_key:
            result = Proximity(
                band=LocationAffinity.EXACT,
                score=AFFINITY_SCORE[LocationAffinity.EXACT],
                matched_name=label,
                reason=(
                    f"Same area: {label}." if label else "Same area."
                ),
            )
        else:
            distance = None
            if job_coords is not None:
                area_coords = _coordinates_of(candidate)
                if area_coords is not None:
                    distance = measure.between(job_coords, area_coords)
            if distance is not None:
                band = band_for_distance(distance)
                result = Proximity(
                    band=band,
                    score=AFFINITY_SCORE[band],
                    distance_km=round(distance, 1),
                    matched_name=label,
                    reason=_distance_reason(band, distance, label),
                )
            else:
                band = affinity(job_location, [candidate])
                result = Proximity(
                    band=band,
                    score=AFFINITY_SCORE[band],
                    matched_name=label,
                    uncertain=True,
                    reason=_zone_reason(band, label),
                )

        if best is None or _proximity_rank(result) < _proximity_rank(best):
            best = result

    # ``best`` cannot be ``None``: the empty-list case returned above.
    return best  # type: ignore[return-value]


def _proximity_rank(proximity: Proximity) -> tuple[int, float]:
    """Sort key: stronger evidence first, then genuinely closer.

    Ordering by band alone left a real defect: two tutor areas could both land
    in the same band and the *first one encountered* won, so the reported
    ``matched_name`` and ``distance_km`` could describe the farther of the two.
    Breaking ties on the measured distance means the number and the name an
    admin reads always describe the same, nearest, area. A band derived without
    coordinates sorts after any measured one (``inf``), so an uncertain
    estimate never displaces a measured fact.
    """
    return (
        _AFFINITY_ORDER.index(proximity.band),
        proximity.distance_km if proximity.distance_km is not None else math.inf,
    )


def _distance_reason(
    band: LocationAffinity, distance_km: float, label: str | None
) -> str:
    """A sentence that says "about", never "within walking distance"."""
    where = f"{label} is " if label else "The tutor's area is "
    if band is LocationAffinity.EXACT:
        return f"Same location: {label}." if label else "Same location."
    if band is LocationAffinity.OTHER_CITY:
        return f"{where}about {distance_km:.0f} km from the job's area."
    return (
        f"{where}about {distance_km:.1f} km from the job's area "
        "(approximate proximity, not a travel guarantee)."
    )


def _zone_reason(band: LocationAffinity, label: str | None) -> str:
    """A sentence for a band derived without coordinates."""
    where = label or "that area"
    if band is LocationAffinity.EXACT:
        return f"Same area: {where}."
    if band is LocationAffinity.CITY_WIDE:
        return "Available across Addis Ababa."
    if band is LocationAffinity.NEARBY:
        return f"Near the job's area ({where}) — proximity uncertain."
    if band is LocationAffinity.SAME_CITY:
        return f"Different part of Addis Ababa ({where}) — proximity uncertain."
    if band is LocationAffinity.OTHER_CITY:
        return f"Based in {where}, away from the job's area."
    return f"Area not recognised ({where}) — proximity uncertain."


# ---------------------------------------------------------------------------
# Nearest tutoring area
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NearestArea:
    """Which of a tutor's selected areas is closest to a job, and how far.

    ``distance_km`` is straight-line (great-circle) distance and is ``None``
    whenever it could not be measured — which is the normal case for a
    manually entered area. A caller must render ``None`` as "distance unknown",
    never as zero and never as "close".
    """

    #: The winning area itself, so a caller can read its id and link to it.
    location: object
    #: Its display name, or ``None`` when the area carried no label.
    name: str | None
    #: Straight-line distance in kilometres, or ``None`` when unmeasurable.
    distance_km: float | None
    band: LocationAffinity
    uncertain: bool
    reason: str
    #: How many areas were considered, so a caller can say "closest of 4".
    considered: int = 0

    @property
    def has_distance(self) -> bool:
        return self.distance_km is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "locationId": getattr(self.location, "id", None),
            "name": self.name,
            "distanceKm": self.distance_km,
            "distanceKind": "straight_line",
            "band": str(self.band),
            "uncertain": self.uncertain,
            "reason": self.reason,
            "considered": self.considered,
        }


class DistanceCalculator:
    """Memoised straight-line distances for one matching run.

    A matching run compares every eligible tutor's areas against a single job
    location. With many tutors in the same neighbourhood that is the same pair
    of coordinates recomputed hundreds of times, so results are memoised on the
    rounded coordinate pair. Rounding to :attr:`precision` decimals (a metre at
    four) is safe: two inputs that round identically are genuinely the same
    place for a neighbourhood-level decision, and the cache key stays short.

    The instance is scoped to a run and discarded with it. Nothing here is a
    process-wide singleton, so a long-lived server cannot grow an unbounded
    cache or serve a distance computed from stale data.
    """

    def __init__(self, *, precision: int = 4) -> None:
        self._cache: dict[tuple[float, float, float, float], float | None] = {}
        self._precision = precision
        #: Counters exposed so a caller (and the tests) can show the cache is
        #: actually doing something.
        self.hits = 0
        self.misses = 0

    def distance_km(
        self,
        lat1: float | str | None,
        lon1: float | str | None,
        lat2: float | str | None,
        lon2: float | str | None,
    ) -> float | None:
        """Straight-line distance in km, or ``None`` when unmeasurable."""
        point_a = _validated_point(lat1, lon1)
        point_b = _validated_point(lat2, lon2)
        if point_a is None or point_b is None:
            # An unknown pair is not cached: "unknown" is usually a data
            # problem that a later edit fixes, and caching it would survive
            # the fix.
            self.misses += 1
            return None
        key = (
            round(point_a[0], self._precision),
            round(point_a[1], self._precision),
            round(point_b[0], self._precision),
            round(point_b[1], self._precision),
        )
        if key in self._cache:
            self.hits += 1
            return self._cache[key]
        self.misses += 1
        distance = _great_circle_km(*key)
        self._cache[key] = distance
        return distance

    def between(self, point_a: object, point_b: object) -> float | None:
        """Distance between two location-shaped values, or ``None``."""
        coordinates_a = _coordinates_of(point_a)
        coordinates_b = _coordinates_of(point_b)
        if coordinates_a is None or coordinates_b is None:
            return None
        return self.distance_km(*coordinates_a, *coordinates_b)

    def nearest(
        self,
        job_location: object,
        tutor_locations: object = None,
        *,
        online: bool = False,
    ) -> NearestArea:
        """The closest of a tutor's selected areas to ``job_location``.

        Selection is by *measured distance first*: among areas that can be
        measured, the shortest one wins, and its name and distance are returned
        together so the two can never describe different areas. Only when no
        area can be measured does this fall back to the zone ladder, and the
        result is then flagged ``uncertain``.
        """
        candidates = _as_sequence(tutor_locations)
        considered = len(candidates)
        # An online job has no place. Checked before any measurement, because
        # measuring anyway would produce a confident-looking figure for
        # something that does not apply.
        online = online or looks_online(job_location)
        if not candidates:
            return NearestArea(
                location=None,
                name=None,
                distance_km=None,
                band=LocationAffinity.UNKNOWN,
                uncertain=True,
                reason="The tutor has not recorded any tutoring areas.",
                considered=0,
            )

        job_coords = None if online else _coordinates_of(job_location)
        best_measured: tuple[float, object, str | None, LocationAffinity] | None = None
        best_fallback: Proximity | None = None

        for candidate in candidates:
            result = proximity(
                job_location, [candidate], online=online, calculator=self
            )
            # Measured directly rather than read off ``result``: an exact match
            # is the *same* place, so when both sides have coordinates the
            # straight-line distance is genuinely 0 km. Taking it from the band
            # would report "distance unknown" for the single most certain match
            # there is.
            distance = self.between(job_coords, candidate)
            if distance is not None:
                band = (
                    LocationAffinity.EXACT
                    if result.is_exact
                    else band_for_distance(distance)
                )
                if best_measured is None or distance < best_measured[0]:
                    best_measured = (
                        distance, candidate, result.matched_name, band
                    )
                continue
            if best_fallback is None or _proximity_rank(result) < _proximity_rank(
                best_fallback
            ):
                best_fallback = result

        if best_measured is not None:
            distance, area, label, band = best_measured
            return NearestArea(
                location=area,
                name=label,
                distance_km=round(distance, 2),
                band=band,
                uncertain=False,
                reason=_distance_reason(band, distance, label),
                considered=considered,
            )

        chosen = best_fallback or proximity(job_location, [], online=online)
        return NearestArea(
            location=None,
            name=chosen.matched_name,
            distance_km=None,
            band=chosen.band,
            uncertain=True,
            reason=chosen.reason,
            considered=considered,
        )


def nearest_area(
    job_location: object,
    tutor_locations: object = None,
    *,
    online: bool = False,
) -> NearestArea:
    """The closest of a tutor's selected areas, with a throwaway calculator.

    Convenience wrapper for one-off callers. Anything scoring many tutors
    should build a single :class:`DistanceCalculator` and reuse it.
    """
    return DistanceCalculator().nearest(
        job_location, tutor_locations, online=online
    )
