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
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "ADJACENT_LOCATIONS",
    "AFFINITY_SCORE",
    "CITY_WIDE",
    "LOCATION_NOISE_TOKENS",
    "LOCATIONS",
    "ONLINE_LOCATION_TOKENS",
    "LocationAffinity",
    "LocationSpec",
    "LocationZone",
    "affinity",
    "affinity_score",
    "canonical_name",
    "display_name",
    "is_known_location",
    "location_zone",
    "looks_online",
    "normalize_location",
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
    LocationAffinity.CITY_WIDE,
    LocationAffinity.NEARBY,
    LocationAffinity.SAME_CITY,
    LocationAffinity.OTHER_CITY,
)


def _as_sequence(value: object) -> list[str]:
    """Accept ``None``, a string, or any iterable of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(item) for item in value if str(item).strip()]
    except TypeError:
        return [str(value)]


def affinity_score(
    job_location: object,
    tutor_locations: object = None,
    *,
    online: bool = False,
) -> float:
    """The 0-1 score for :func:`affinity`, for the matching engine."""
    return AFFINITY_SCORE[affinity(job_location, tutor_locations, online=online)]