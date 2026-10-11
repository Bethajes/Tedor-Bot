"""Grade and subject resolution for the matching engine.

A job's level and a tutor's grades are written in different vocabularies. The
job-post generator collects ``"4th Grader"``; the new onboarding flow collects
``"Grade 4"``; the 1,000+ legacy tutors hold coarse bands such as
``"Grade 4-6"``. All three have to compare equal before anything can be
scored, which is what :func:`grade_band` and :func:`canonical_subject` do.

Grades resolve to a numeric range rather than a single number, because
``"Grade 4-6"`` genuinely covers three grades and an exact-match test on the
raw string would wrongly call it a mismatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from app.templates.tutor_job_post import (
    MAJOR_SUBJECT_ALIASES,
    MAJOR_SUBJECT_TAGS,
    SUBJECT_HASHTAGS,
)

__all__ = [
    "ELEMENTARY_MAX",
    "GRADE_SUBJECTS",
    "GradeBand",
    "UNIVERSAL_GRADE_KEYWORDS",
    "canonical_subject",
    "canonical_subjects",
    "grade_band",
    "grade_overlap",
    "is_universal_grade",
    "is_university_grade",
]


@dataclass(frozen=True)
class GradeBand:
    """One resolved grade level, as an inclusive numeric range.

    ``low``/``high`` are ``None`` for levels with no grade number
    (``University``, ``Adult/Professional``).
    """

    key: str
    label: str
    low: int | None
    high: int | None

    @property
    def is_numeric(self) -> bool:
        return self.low is not None and self.high is not None

    @property
    def is_university(self) -> bool:
        return self.is_universal_stage

    @property
    def is_universal_stage(self) -> bool:
        """University, Adult/Professional — not tied to a grade number."""
        return not self.is_numeric


#: Ethiopia runs Grades 1-4 at primary level, so ``Elementary`` spans them.
ELEMENTARY_MAX: Final[int] = 4

#: Phrases meaning "any level at all", from either vocabulary.
UNIVERSAL_GRADE_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"all levels", "all grades", "all grade", "any grade", "any level"}
)

#: Non-numeric stages, keyed by the phrases that introduce them.
_UNIVERSITY_KEYWORDS: Final[tuple[str, ...]] = (
    "university",
    "undergraduate",
    "college",
    "postgraduate",
    "post graduate",
    "higher education",
)
_ADULT_KEYWORDS: Final[tuple[str, ...]] = (
    "adult",
    "professional",
)

_ORDINAL_GRADE_RE = re.compile(r"(\d+)\s*(?:st|nd|rd|th)?\s*grader", re.IGNORECASE)
_PLAIN_GRADE_RE = re.compile(r"grade\s*(\d+)", re.IGNORECASE)
_GRADE_RANGE_RE = re.compile(
    r"grade\s*(\d+)\s*(?:-|–|to)\s*(\d+)", re.IGNORECASE
)
_BARE_RANGE_RE = re.compile(r"^\s*(\d+)\s*(?:-|–|to)\s*(\d+)\s*$")
_BARE_NUMBER_RE = re.compile(r"^\s*(\d+)\s*$")
_WHITESPACE_RE = re.compile(r"\s+")


def _clean(value: object) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "").strip()).strip()


def _normalise_phrase(value: object) -> str:
    """Lower-case, unify dashes and collapse whitespace."""
    text = _clean(value).casefold().replace("–", "-").replace("_", " ")
    return _WHITESPACE_RE.sub(" ", text).strip()


def is_universal_grade(value: object) -> bool:
    """``All Levels`` / ``All Grades`` — the tutor can teach any grade."""
    return _normalise_phrase(value) in UNIVERSAL_GRADE_KEYWORDS


def is_university_grade(value: object) -> bool:
    text = _normalise_phrase(value)
    return any(keyword in text for keyword in _UNIVERSITY_KEYWORDS)


def grade_band(value: object) -> GradeBand | None:
    """Resolve one written level to a :class:`GradeBand`.

    Accepts every form in use: ``"4th Grader"``, ``"Grade 4"``, ``"Grade 4-6"``,
    ``"Grade 1-3"``, ``"4"``, ``"Elementary"``, ``"KG"``, ``"University"``,
    ``"All Levels"``. Returns ``None`` for nothing usable, which the scorers
    report as missing information rather than as a mismatch.
    """
    text = _normalise_phrase(value)
    if not text:
        return None

    if text in UNIVERSAL_GRADE_KEYWORDS:
        return GradeBand(key="all", label=str(value).strip(), low=0, high=12)

    if text in ("kg", "kg1", "kg 1", "kindergarten", "pre - k"):
        return GradeBand(key="kg", label="KG", low=0, high=0)

    if "elementary" in text or "primary" in text:
        return GradeBand(key="elementary", label="Elementary", low=1, high=ELEMENTARY_MAX)

    if any(keyword in text for keyword in _UNIVERSITY_KEYWORDS):
        return GradeBand(key="university", label=str(value).strip(), low=None, high=None)

    if any(keyword in text for keyword in _ADULT_KEYWORDS):
        return GradeBand(key="adult", label=str(value).strip(), low=None, high=None)

    match = _GRADE_RANGE_RE.search(text)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        return _numeric_band(low, high, str(value).strip())

    match = _ORDINAL_GRADE_RE.search(text)
    if match:
        grade = int(match.group(1))
        return _numeric_band(grade, grade, str(value).strip())

    match = _PLAIN_GRADE_RE.search(text)
    if match:
        grade = int(match.group(1))
        return _numeric_band(grade, grade, str(value).strip())

    match = _BARE_RANGE_RE.match(text)
    if match:
        return _numeric_band(int(match.group(1)), int(match.group(2)), str(value).strip())

    match = _BARE_NUMBER_RE.match(text)
    if match:
        grade = int(match.group(1))
        return _numeric_band(grade, grade, str(value).strip())

    return None


def _numeric_band(low: int, high: int, label: str) -> GradeBand:
    """Build a numeric band, tolerating a reversed range like ``"6-4"``."""
    if low > high:
        low, high = high, low
    return GradeBand(key=f"{low}-{high}", label=label, low=low, high=high)


def grade_overlap(job: object, tutor: object) -> float:
    """How well one written grade covers another, 0.0 - 1.0.

    Bands rather than strings, so ``"Grade 4"`` and ``"Grade 4-6"`` both count as
    a match for a ``"4th Grader"`` job. ``None`` in, ``None`` out: an
    unresolvable level is missing information, not a score of zero.
    """
    job_band = job if isinstance(job, GradeBand) else grade_band(job)
    tutor_band = tutor if isinstance(tutor, GradeBand) else grade_band(tutor)
    if job_band is None or tutor_band is None:
        return 0.0
    return _band_overlap(job_band, tutor_band)


def _band_overlap(job: GradeBand, tutor: GradeBand) -> float:
    # "All grades" covers any numeric level.
    if tutor.key == "all" and job_band_is_numeric(job):
        return 1.0
    if job.key == "all" and job_band_is_numeric(tutor):
        # The job is unconstrained; a tutor who named a level satisfies it.
        return 0.9

    if not job.is_numeric or not tutor.is_numeric:
        # University against university; university against a grade number.
        if job.is_universal_stage and tutor.is_universal_stage:
            return 1.0
        return 0.2

    assert job.low is not None and job.high is not None
    assert tutor.low is not None and tutor.high is not None

    overlap = min(job.high, tutor.high) - max(job.low, tutor.low) + 1
    if overlap <= 0:
        return _distance_score(job, tutor)

    job_span = job.high - job.low + 1
    # Full coverage of the job's grade is the ideal; a wider tutor band that
    # includes it scores 1.0 rather than being penalised for covering more.
    return 1.0 if overlap >= job_span else 0.75


def job_band_is_numeric(band: GradeBand) -> bool:
    return band.is_numeric


def _distance_score(job: GradeBand, tutor: GradeBand) -> float:
    """Adjacent grades are related; distant ones are not."""
    assert job.low is not None and job.high is not None
    assert tutor.low is not None and tutor.high is not None
    if tutor.high < job.low:
        gap = job.low - tutor.high
    else:
        gap = tutor.low - job.high
    if gap <= 1:
        return 0.5
    if gap == 2:
        return 0.35
    if gap <= 4:
        return 0.2
    return 0.05


# ---------------------------------------------------------------------------
# subjects
# ---------------------------------------------------------------------------

#: Subjects whose teaching implies the corresponding grade band's core need.
#: Used only to decide whether an academic signal is *relevant* to a job.
GRADE_SUBJECTS: Final[frozenset[str]] = frozenset(
    {"MATH", "ENGLISH", "SCIENCE", "AMHARIC"}
)


def canonical_subject(value: object) -> str | None:
    """Map a written subject onto its canonical tag, e.g. ``Maths`` -> ``MATH``.

    ``Major Subjects`` expands to its three component tags because that is what
    the job-post template already means by it. Returns ``None`` for nothing
    usable.
    """
    text = _normalise_phrase(value)
    if not text:
        return None
    if any(alias in text for alias in MAJOR_SUBJECT_ALIASES):
        return "MAJOR"
    tag = SUBJECT_HASHTAGS.get(text)
    if tag:
        return tag
    # An already-tagged value, e.g. a stored "#MATH".
    cleaned = text.lstrip("#").replace(" ", "_").upper()
    if cleaned in SUBJECT_TAG_VALUES:
        return cleaned
    return cleaned


#: Every tag the template knows, so a stored tag resolves to itself.
SUBJECT_TAG_VALUES: Final[frozenset[str]] = frozenset(SUBJECT_HASHTAGS.values()) | set(
    MAJOR_SUBJECT_TAGS
)


def canonical_subjects(value: object) -> tuple[str, ...]:
    """Expand a subject list, splitting on separators.

    Accepts either a written string (the job post's ``subjects`` column) or an
    iterable of names (the tutor's :attr:`TutorSubject` rows). ``"Major
    Subjects"`` becomes ``("ENGLISH", "MATH", "SCIENCE")``; an unrecognised
    subject keeps its own name as a tag, exactly as the public hashtag renderer
    does — no subject is ever dropped.
    """
    if isinstance(value, str):
        parts = [
            part
            for part in re.split(r"[,/\n+&]| and ", _clean(value), flags=re.I)
            if part
        ]
    elif value is None:
        parts = []
    else:
        try:
            parts = [_clean(item) for item in value if _clean(item)]
        except TypeError:
            parts = [_clean(value)]

    tags: list[str] = []
    for part in parts:
        tag = canonical_subject(part)
        if tag == "MAJOR":
            tags.extend(MAJOR_SUBJECT_TAGS)
        elif tag and tag not in tags:
            tags.append(tag)
    return tuple(tags)


def subject_overlap(job_subjects: object, tutor_subjects: object) -> float:
    """Fraction of the job's subjects the tutor covers.

    A job listing one subject the tutor teaches is a full match. ``None``
    propagates from an unresolvable job subject.
    """
    wanted = canonical_subjects(job_subjects)
    if not wanted:
        return 0.0
    offered = {
        tag for tag in canonical_subjects(tutor_subjects) if tag
    }
    if not offered:
        return 0.0
    # "All Levels"-style breadth does not apply to subjects; a tutor who lists
    # a bare "Other" has told us nothing useful.
    matched = sum(1 for tag in wanted if tag in offered)
    if matched:
        return matched / len(wanted)
    # A subject we do not recognise on the job side may still be matched by a
    # tutor's own tag, so fall back to a case-insensitive substring test.
    lowered = {tag.casefold() for tag in offered}
    for tag in wanted:
        if any(tag.casefold() in name or name in tag.casefold() for name in lowered):
            return 0.75
    return 0.0