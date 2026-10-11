"""Explainable tutor-job matching.

:meth:`MatchingService.match` scores every VERIFIED tutor against a published
job and returns the ranked candidates with the reasons behind each score. Seven
independent criteria are scored on a 0-1 scale and combined with a job-aware set
of weights.

Design rules that the tests enforce, because each one contradicts the obvious
implementation:

* **Location is a score, never a filter.** No criterion can exclude a tutor. A
  tutor in another city scores low and is still returned, because the admin may
  know a reason we do not.
* **Missing information is not a mismatch.** A tutor who never recorded their
  areas scores the neutral :data:`NEUTRAL` band and reports the gap, so a thin
  record is visibly thin rather than silently poor.
* **The entrance examination is one signal, not the verdict.** Scores are
  normalised against their own maximum before any comparison, and the academic
  criterion is capped so it can never outweigh teaching fit.
* **Gender contributes at most 5%.** It is the smallest weight, and a mismatch
  reduces the score without excluding the tutor.
* **The 50-member rule is invisible here.** ``member_referral_status`` is never
  read by this module — ``test_member_referral_is_never_read`` asserts it.
* **Nothing is assigned automatically.** Ranking produces advice for an admin;
  the admin chooses.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Final, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.enums import (
    DEFAULT_MATCH_WEIGHTS,
    ENGLISH_FOCUSED_MATCH_WEIGHTS,
    MATCH_CRITERIA,
    STEM_ADVANCED_MATCH_WEIGHTS,
    normalise_weights,
)
from app.models import JobPost, Tutor, TutorExperience
from app.services import grade_service, location_service
from app.services.location_service import LocationAffinity

logger = logging.getLogger(__name__)

#: Score for a criterion with no usable evidence either way. Chosen as the
#: midpoint so a thin record neither outranks nor is outranked by a real fit.
NEUTRAL: Final[float] = 0.5

#: Floor for the academic criterion. Entrance exam, CGPA and documents together
#: can lift a tutor by at most this much, so academic strength can never
#: substitute for an inability to teach the requested grade or subject.
ACADEMIC_MAX_CONTRIBUTION: Final[float] = 0.90
ACADEMIC_MIN_CONTRIBUTION: Final[float] = 0.30

#: Upper bound on how much of a job's English requirement can lift a score.
ENGLISH_MAX_CONTRIBUTION: Final[float] = 1.0

#: Years at which the experience criterion saturates. Above this, relevance
#: (the subject and grade actually taught) decides, not the total.
EXPERIENCE_SATURATION_YEARS: Final[float] = 5.0

#: A gender/category mismatch floors this criterion rather than zeroing it.
CATEGORY_MISMATCH_FLOOR: Final[float] = 0.25


@dataclass(frozen=True)
class CriterionResult:
    """One criterion's verdict for one tutor/job pair.

    ``strengths``, ``weaknesses`` and ``missing`` are what makes the match
    explainable: an admin sees *why* a tutor scored what they scored, and what
    information simply was not on file.
    """

    criterion: str
    score: float
    weight: float
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def weighted(self) -> float:
        return self.score * self.weight

    @property
    def is_neutral(self) -> bool:
        return abs(self.score - NEUTRAL) < 1e-9


@dataclass
class MatchResult:
    """A ranked candidate with its full explanation."""

    tutor: Tutor
    job: JobPost
    score: float
    rank: int = 0
    criteria: dict[str, CriterionResult] = field(default_factory=dict)

    @property
    def public_tutor_id(self) -> str:
        return self.tutor.public_tutor_id

    @property
    def display_name(self) -> str:
        return self.tutor.display_name

    @property
    def percentage(self) -> int:
        """The headline number, 0-100."""
        return round(self.score * 100)

    @property
    def strengths(self) -> tuple[str, ...]:
        collected: list[str] = []
        for criterion in MATCH_CRITERIA:
            result = self.criteria.get(criterion)
            if result:
                collected.extend(result.strengths)
        return tuple(collected)

    @property
    def weaknesses(self) -> tuple[str, ...]:
        collected: list[str] = []
        for criterion in MATCH_CRITERIA:
            result = self.criteria.get(criterion)
            if result:
                collected.extend(result.weaknesses)
        return tuple(collected)

    @property
    def missing(self) -> tuple[str, ...]:
        collected: list[str] = []
        for criterion in MATCH_CRITERIA:
            result = self.criteria.get(criterion)
            if result:
                collected.extend(result.missing)
        return tuple(collected)

    @property
    def has_documented_experience(self) -> bool:
        return bool(self.tutor.experiences) or bool(self.tutor.teaching_experience_years)

    def criterion(self, name: str) -> CriterionResult | None:
        return self.criteria.get(name)

    def to_dict(self) -> dict[str, Any]:
        """Serialisable form for the admin API and the report renderer."""
        return {
            "tutor_id": self.tutor.public_tutor_id,
            "display_name": self.display_name,
            "score": self.percentage,
            "rank": self.rank,
            "strengths": list(self.strengths),
            "weaknesses": list(self.weaknesses),
            "missing": list(self.missing),
            "criteria": {
                name: {
                    "score": round(result.score, 3),
                    "weight": round(result.weight, 3),
                    "strengths": list(result.strengths),
                    "weaknesses": list(result.weaknesses),
                    "missing": list(result.missing),
                }
                for name, result in self.criteria.items()
            },
        }


# ---------------------------------------------------------------------------
# job profile interpretation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobProfile:
    """A published job, resolved into the terms the scorers need."""

    post: JobPost
    grade: grade_service.GradeBand | None
    subjects: tuple[str, ...]
    online: bool
    english_level_required: str
    wants_male: bool
    wants_female: bool

    @property
    def is_english_job(self) -> bool:
        """English is one of the requested subjects."""
        return "ENGLISH" in self.subjects

    @property
    def is_stem_job(self) -> bool:
        """A mathematical or science subject at an advanced grade."""
        stem = {"MATH", "SCIENCE", "PHYSICS", "CHEMISTRY"}
        return bool(self.subjects) and bool(self.subjects[0] in stem) and self.is_upper_grade

    @property
    def is_upper_grade(self) -> bool:
        band = self.grade
        if band is None or not band.is_numeric:
            return False
        assert band.low is not None
        return band.low >= 9


#: The English bars, from weakest to strongest.
_ENGLISH_LEVELS: Final[frozenset[str]] = frozenset({"NONE", "BASIC", "STANDARD", "HIGH"})


def _english_level_for(post: JobPost) -> str:
    """The English bar this job sets, from what the admin collected.

    ``english_level_required`` is populated at publish time from the job's own
    subjects, so it is honoured when present and derived when the column is
    still empty (an older post, or one backfilled by hand).
    """
    declared = str(post.english_level_required or "").strip().upper()
    if declared in _ENGLISH_LEVELS:
        return declared
    if "ENGLISH" in grade_service.canonical_subjects(post.subjects):
        return "HIGH"
    return "STANDARD"


def job_profile(post: JobPost) -> JobProfile:
    """Resolve a job post into the structured terms the scorers consume."""
    online = str(post.teaching_mode or "").strip().upper() == "ONLINE" or (
        location_service.looks_online(post.location)
    )
    category = str(post.tutor_category or "").casefold()
    # Word-boundary matching: ``"female"`` contains ``"male"``, so a plain
    # substring test would read "Female" as a request for both. The words are
    # lower-cased before comparison for the same reason.
    words = {word.lower() for word in _GENDER_WORD_RE.findall(category)}
    wants_male = "male" in words and "female" not in words
    wants_female = "female" in words and "male" not in words

    return JobProfile(
        post=post,
        grade=grade_service.grade_band(post.student_level),
        subjects=grade_service.canonical_subjects(post.subjects),
        online=online,
        english_level_required=_english_level_for(post),
        wants_male=wants_male,
        wants_female=wants_female,
    )


def weights_for_job(profile: JobProfile) -> dict[str, float]:
    """Pick the weight profile that suits this job, then normalise it.

    Composition rather than a table of special cases, applied in a fixed order
    so that no later step can silently undo an earlier one:

    1. the default profile;
    2. what the job is *about* — an English job leans on English, an advanced
       STEM job leans on subject and academics;
    3. what the English bar is — a job that does not need English drops the
       English weight rather than scoring it;
    4. the teaching mode, **last**, because it is the hard constraint: location
       is worthless online and decisive in person, so it overrides whatever
       step 2 chose.

    Always renormalised to 1.0, so scores are comparable across jobs.
    """
    weights = dict(DEFAULT_MATCH_WEIGHTS)

    if profile.is_english_job:
        weights.update(ENGLISH_FOCUSED_MATCH_WEIGHTS)
    elif profile.is_stem_job:
        weights.update(STEM_ADVANCED_MATCH_WEIGHTS)

    if profile.english_level_required == "NONE":
        weights["english"] = 0.0
    elif profile.english_level_required == "BASIC":
        weights["english"] *= 0.4

    # Applied last: teaching mode beats job subject. Only the location weight is
    # overridden — applying a whole profile here would discard the English/STEM
    # emphasis chosen in step 2.
    weights["location"] = 0.0 if profile.online else IN_PERSON_LOCATION_WEIGHT

    return normalise_weights(weights)


# ---------------------------------------------------------------------------
# criteria
# ---------------------------------------------------------------------------


def score_location(tutor: Tutor, profile: JobProfile, weight: float) -> CriterionResult:
    """Proximity of the tutor's areas to the job's area."""
    locations = tutor.location_list or ([tutor.city] if tutor.city else [])
    if profile.online:
        return CriterionResult(
            criterion="location",
            score=NEUTRAL,
            weight=weight,
            strengths=("Online job — location does not apply",),
        )

    if not locations:
        return CriterionResult(
            criterion="location",
            score=NEUTRAL,
            weight=weight,
            missing=("Tutoring areas not recorded",),
        )

    band = location_service.affinity(profile.post.location, locations)
    score = location_service.AFFINITY_SCORE[band]
    result = CriterionResult(criterion="location", score=score, weight=weight)

    if band == LocationAffinity.EXACT:
        result = _with(result, strengths=(f"Available in {profile.post.location}",))
    elif band == LocationAffinity.CITY_WIDE:
        result = _with(result, strengths=("Available across Addis Ababa",))
    elif band == LocationAffinity.NEARBY:
        result = _with(result, strengths=(f"Near {profile.post.location} — {', '.join(locations)}",))
    elif band == LocationAffinity.SAME_CITY:
        result = _with(
            result,
            weaknesses=(f"Different part of Addis Ababa — {', '.join(locations)}",),
        )
    elif band == LocationAffinity.OTHER_CITY:
        # Deliberately a weakness, not an exclusion.
        result = _with(
            result,
            weaknesses=(f"Based in {', '.join(locations)}, away from the job's area",),
        )
    else:
        result = _with(result, missing=("Tutoring areas not recognised",))
    return result


def _with(result: CriterionResult, **changes: Any) -> CriterionResult:
    """Return a copy of ``result`` with the named tuple field replaced."""
    data = {
        "criterion": result.criterion,
        "score": result.score,
        "weight": result.weight,
        "strengths": result.strengths,
        "weaknesses": result.weaknesses,
        "missing": result.missing,
    }
    data.update(changes)
    return CriterionResult(**data)


def score_grade(tutor: Tutor, profile: JobProfile, weight: float) -> CriterionResult:
    """Whether the tutor can teach the requested level.

    Reads the fine-grained ``TutorGrade`` rows when present and falls back to
    the coarse ``TutorLevel`` bands, so the 1,000+ legacy tutors are still
    scored rather than treated as unknown.
    """
    if profile.grade is None:
        return CriterionResult(
            criterion="grade",
            score=NEUTRAL,
            weight=weight,
            missing=("Job grade level could not be read",),
        )

    tutor_grades = tutor.grade_list or tutor.level_list
    if not tutor_grades:
        return CriterionResult(
            criterion="grade",
            score=NEUTRAL,
            weight=weight,
            missing=("No grade levels recorded",),
        )

    overlaps = [grade_service.grade_overlap(profile.grade, band) for band in tutor_grades]
    best = max(overlaps)
    covering = [
        band for band, overlap in zip(tutor_grades, overlaps) if overlap >= 1.0
    ]

    result = CriterionResult(criterion="grade", score=best, weight=weight)
    if best >= 0.99:
        result = _with(result, strengths=(f"Teaches {profile.post.student_level}",))
        if covering:
            result = _with(
                result,
                strengths=(f"Grade match — {', '.join(str(item) for item in covering[:3])}",),
            )
    elif best >= 0.5:
        result = _with(result, strengths=(f"Teaches nearby levels — {', '.join(tutor_grades[:3])}",))
    else:
        result = _with(
            result,
            weaknesses=(f"Teaches {', '.join(tutor_grades[:3])}, not {profile.post.student_level}",),
        )
    return result


def score_subject(tutor: Tutor, profile: JobProfile, weight: float) -> CriterionResult:
    """Whether the tutor covers the requested subjects."""
    if not profile.subjects:
        return CriterionResult(
            criterion="subject",
            score=NEUTRAL,
            weight=weight,
            missing=("Job subjects could not be read",),
        )

    tutor_subjects = tutor.subject_list
    if not tutor_subjects:
        return CriterionResult(
            criterion="subject",
            score=NEUTRAL,
            weight=weight,
            missing=("No subjects recorded",),
        )

    score = grade_service.subject_overlap(profile.post.subjects, tutor_subjects)
    result = CriterionResult(criterion="subject", score=score, weight=weight)
    if score >= 0.99:
        result = _with(result, strengths=(f"Teaches {', '.join(tutor_subjects[:3])}",))
    elif score > 0:
        result = _with(
            result,
            strengths=(f"Teaches {', '.join(tutor_subjects[:3])}",),
            weaknesses=("Partial subject match",),
        )
    else:
        result = _with(
            result,
            weaknesses=(f"Does not teach {profile.post.subjects}",),
        )
    return result


def score_english(tutor: Tutor, profile: JobProfile, weight: float) -> CriterionResult:
    """English proficiency against what the job needs.

    Uses the self-rated 1-10 number only. The voice recording is surfaced to
    the admin as evidence and is deliberately not scored, and a low rating never
    removes a tutor from consideration — the admin decides.
    """
    if weight <= 0:
        return CriterionResult(criterion="english", score=NEUTRAL, weight=0.0)

    level = tutor.english_proficiency
    if level is None:
        return CriterionResult(
            criterion="english",
            score=NEUTRAL,
            weight=weight,
            missing=("English level not provided",),
            strengths=(
                ("English voice recording available for review",) if tutor.has_english_voice else ()
            ),
        )

    required = _ENGLISH_REQUIREMENTS.get(profile.english_level_required, 5)
    # Linear from "half the required bar scores zero" to "the bar plus one is
    # full marks", clamped — so a tutor well above the bar is not rewarded
    # twice.
    raw = (level - required * 0.5) / max(1.0, required)
    score = min(ENGLISH_MAX_CONTRIBUTION, max(0.0, raw))

    result = CriterionResult(criterion="english", score=score, weight=weight)
    if level >= required:
        result = _with(
            result,
            strengths=(
                f"English {level}/10 — meets the {profile.english_level_required.lower()} requirement",
            ),
        )
    else:
        result = _with(
            result,
            weaknesses=(f"English {level}/10, below the {profile.english_level_required.lower()} requirement",),
        )
    if tutor.has_english_voice:
        result = _with(result, strengths=("🎤 English voice recording on file for admin review",))
    return result


#: Gender words in a job's ``tutor_category``. Matched on word boundaries
#: because ``"Female"`` contains ``"male"`` as a substring.
_GENDER_WORD_RE: Final[re.Pattern[str]] = re.compile(r"\b(male|female)\b", re.IGNORECASE)

#: Location's weight for an in-person job. The heaviest of any criterion, since
#: for in-person teaching it is a practical constraint rather than a preference.
IN_PERSON_LOCATION_WEIGHT: Final[float] = 0.35

#: Self-rated level treated as "meets the bar" for each requirement.
_ENGLISH_REQUIREMENTS: Final[dict[str, int]] = {
    "NONE": 0,
    "BASIC": 3,
    "STANDARD": 5,
    "HIGH": 7,
}


def normalised_entrance_score(tutor: Tutor) -> float | None:
    """The entrance exam as a 0-100 percentage, or ``None`` when unknown.

    Normalised against the score's own maximum so an EHE result and a percentage
    result are comparable. A score with no recorded maximum returns ``None``
    rather than being compared raw — that is the whole reason
    ``entrance_exam_max_score`` is stored.
    """
    score = tutor.entrance_exam_score
    maximum = tutor.entrance_exam_max_score
    if score is None or not maximum or maximum <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * float(score) / float(maximum)))


#: Relative weights of the academic signals, in percent. Kept small on purpose:
#: academic performance is one factor, never the verdict (section 14).
_ACADEMIC_SIGNAL_WEIGHTS: Final[dict[str, float]] = {
    "entrance": 0.40,
    "cgpa": 0.30,
    "degree": 0.20,
    "documents": 0.10,
}


def score_academic(tutor: Tutor, profile: JobProfile, weight: float) -> CriterionResult:
    """Academic signal: entrance exam, CGPA, degree and documents.

    Each present signal contributes its share; a signal with no evidence simply
    does not contribute, it is never invented. The result is clamped so a
    brilliant academic record cannot outweigh an inability to teach the
    requested grade or subject (section 30).
    """
    score = 0.0
    labels: list[str] = []

    entrance = normalised_entrance_score(tutor)
    if entrance is not None:
        score += _ACADEMIC_SIGNAL_WEIGHTS["entrance"] * (entrance / 100.0)
        labels.append(f"Entrance exam {tutor.entrance_exam_score:g}/{tutor.entrance_exam_max_score:g}")
    elif tutor.entrance_exam_score is not None:
        score += _ACADEMIC_SIGNAL_WEIGHTS["entrance"] * NEUTRAL
        labels.append(f"Entrance exam {tutor.entrance_exam_score:g} (scale not recorded)")

    if tutor.cgpa is not None and settings.cgpa_max > 0:
        cgpa_ratio = min(1.0, float(tutor.cgpa) / settings.cgpa_max)
        score += _ACADEMIC_SIGNAL_WEIGHTS["cgpa"] * cgpa_ratio
        labels.append(f"CGPA {tutor.cgpa:g}/{settings.cgpa_max:g}")

    if tutor.education:
        degree_relevance = _degree_relevance(tutor, profile)
        if degree_relevance is not None:
            score += _ACADEMIC_SIGNAL_WEIGHTS["degree"] * degree_relevance[0]
            labels.append(degree_relevance[1])

    documents = _academic_document_count(tutor)
    if documents:
        score += _ACADEMIC_SIGNAL_WEIGHTS["documents"] * min(1.0, documents / 2.0)
        labels.append(f"{documents} academic document(s) on file")

    if score <= 0 and not labels:
        return CriterionResult(
            criterion="academic",
            score=NEUTRAL,
            weight=weight,
            missing=("No academic records provided",),
        )

    score = min(ACADEMIC_MAX_CONTRIBUTION, max(ACADEMIC_MIN_CONTRIBUTION, score))

    result = CriterionResult(criterion="academic", score=score, weight=weight)
    if score >= 0.7:
        result = _with(result, strengths=tuple(labels))
    elif score <= 0.45:
        result = _with(result, weaknesses=tuple(labels))
    else:
        result = _with(result, strengths=tuple(labels[:1]))
    if not tutor.education:
        result = _with(result, missing=("No degree recorded",))
    return result


#: Degree fields that count as a relevant qualification for a subject.
_RELEVANT_DEGREE_FIELDS: Final[frozenset[str]] = frozenset(
    {"MATH", "SCIENCE", "ENGLISH", "AMHARIC", "PHYSICS", "CHEMISTRY", "BIOLOGY"}
)


def _degree_relevance(tutor: Tutor, profile: JobProfile) -> tuple[float, str] | None:
    """Reward a degree in, or adjacent to, the job's subject.

    The department appears twice in most records — once on the tutor and once on
    the ``TutorEducation`` row — so the two are merged and de-duplicated before
    resolving. Without that, "Mathematics Mathematics" would canonicalise to a
    tag no lookup recognises and every degree would score the same.
    """
    record = tutor.education[0] if tutor.education else None
    parts: list[str] = []
    for candidate in (record.field if record else "", tutor.department or ""):
        for name in str(candidate).split(","):
            text = name.strip()
            if text and text.casefold() not in {item.casefold() for item in parts}:
                parts.append(text)
    if not parts:
        return None

    tags: set[str] = set()
    for part in parts:
        tags.update(grade_service.canonical_subjects(part))
    if not tags:
        return None

    label = ", ".join(parts)
    if profile.subjects and tags & set(profile.subjects):
        return (1.0, f"Degree in {label}")
    if tags & _RELEVANT_DEGREE_FIELDS:
        return (0.7, f"Teaching-adjacent degree ({label})")
    return (0.5, f"Degree in {label}")


def _academic_document_count(tutor: Tutor) -> int:
    from app.enums import ACADEMIC_DOCUMENT_TYPES

    academic = set(ACADEMIC_DOCUMENT_TYPES)
    return sum(1 for document in tutor.documents if document.document_type in academic)


def experience_years(tutor: Tutor) -> float:
    """Total teaching years, preferring the specific field over the legacy one."""
    if tutor.teaching_experience_years is not None:
        return float(tutor.teaching_experience_years)
    return float(tutor.experience_years or 0)


def score_experience(tutor: Tutor, profile: JobProfile, weight: float) -> CriterionResult:
    """Years of teaching, weighted by *relevance*.

    Five years of Grade 4 mathematics beats five years of university-level
    programming for a Grade 4 mathematics job, so the years curve saturates at
    :data:`EXPERIENCE_SATURATION_YEARS` and the subject/grade relevance of the
    recorded roles decides beyond that.
    """
    years = experience_years(tutor)
    roles = tutor.experiences or []

    if not roles and years <= 0:
        return CriterionResult(
            criterion="experience",
            score=NEUTRAL,
            weight=weight,
            missing=("No documented tutoring experience",),
        )

    years_score = min(1.0, years / EXPERIENCE_SATURATION_YEARS)
    relevance = _experience_relevance(tutor, roles, profile)

    if relevance is None:
        # Years only, with nothing to judge relevance against.
        score = years_score * 0.7
        result = CriterionResult(criterion="experience", score=score, weight=weight)
        if years > 0:
            result = _with(result, strengths=(f"{years:g} year(s) of teaching",))
        return result

    score = min(1.0, years_score * 0.4 + relevance * 0.6)
    result = CriterionResult(criterion="experience", score=score, weight=weight)
    detail = f"{years:g} year(s) of teaching"
    if relevance >= 0.75:
        result = _with(result, strengths=(detail, f"Relevant experience: {_relevance_note(tutor, profile)}"))
    elif relevance >= 0.4:
        result = _with(result, strengths=(detail,), weaknesses=("Partially relevant teaching experience",))
    else:
        result = _with(result, weaknesses=(f"{detail}, mostly other subjects or grades",))
    return result


def _relevance_note(tutor: Tutor, profile: JobProfile) -> str:
    for role in tutor.experiences or []:
        if _role_relevance(role, profile) >= 0.75:
            return (role.description or role.subjects or role.grades or "a previous role")[:60]
    return "a related subject or grade"


def _experience_relevance(
    tutor: Tutor, roles: Sequence[TutorExperience], profile: JobProfile
) -> float | None:
    """Best relevance across the tutor's recorded roles, or ``None`` if unknown."""
    if not roles:
        return None
    return max(_role_relevance(role, profile) for role in roles)


def _role_relevance(role: TutorExperience, profile: JobProfile) -> float:
    """How close one past role is to the job: half subject, half grade."""
    parts: list[float] = []

    if profile.subjects and role.subjects:
        parts.append(grade_service.subject_overlap(profile.subjects, role.subjects))
    if profile.grade is not None and role.grades:
        bands = [grade_service.grade_band(value) for value in role.grades.split(",")]
        overlaps = [
            grade_service.grade_overlap(profile.grade, band)
            for band in bands
            if band is not None
        ]
        if overlaps:
            parts.append(max(overlaps))

    if not parts:
        return 0.0
    return sum(parts) / len(parts)


def score_category(tutor: Tutor, profile: JobProfile, weight: float) -> CriterionResult:
    """Tutor category / gender — the smallest weight in the profile."""
    if not profile.wants_male and not profile.wants_female:
        return CriterionResult(
            criterion="category",
            score=1.0,
            weight=weight,
            strengths=(f"Job accepts {profile.post.tutor_category or 'any tutor'}",),
        )

    gender_words = {word.lower() for word in _GENDER_WORD_RE.findall(str(tutor.gender or ""))}
    if not gender_words:
        return CriterionResult(
            criterion="category",
            score=NEUTRAL,
            weight=weight,
            missing=("Gender not provided",),
            weaknesses=(f"Job asks for {profile.post.tutor_category}",),
        )

    wanted = "male" if profile.wants_male else "female"
    if wanted in gender_words:
        return CriterionResult(
            criterion="category",
            score=1.0,
            weight=weight,
            strengths=(f"Matches the requested category ({profile.post.tutor_category})",),
        )

    # A floor, never a zero: this criterion carries at most 5% of the score, so
    # a mismatch can never remove a tutor from the list.
    return CriterionResult(
        criterion="category",
        score=CATEGORY_MISMATCH_FLOOR,
        weight=weight,
        weaknesses=(f"Job asks for {profile.post.tutor_category}",),
    )


#: Criterion name -> scorer, in the canonical order.
_SCORERS = {
    "location": score_location,
    "grade": score_grade,
    "subject": score_subject,
    "english": score_english,
    "academic": score_academic,
    "experience": score_experience,
    "category": score_category,
}


# ---------------------------------------------------------------------------
# the service
# ---------------------------------------------------------------------------


class MatchingService:
    """Scores tutors against published jobs."""

    def score_tutor(self, tutor: Tutor, profile: JobProfile) -> MatchResult:
        """Score one tutor against one job, with the full explanation."""
        weights = _profile_weights(profile)
        criteria: dict[str, CriterionResult] = {}
        weighted_total = 0.0

        for name in MATCH_CRITERIA:
            result = _SCORERS[name](tutor, profile, weights[name])
            criteria[name] = result
            weighted_total += result.weighted

        return MatchResult(
            tutor=tutor,
            job=profile.post,
            score=max(0.0, min(1.0, weighted_total)),
            criteria=criteria,
        )

    def verified_tutors(self, session: Session) -> list[Tutor]:
        """Every VERIFIED tutor, with the collections the scorers read.

        Only VERIFIED tutors appear in recommendations (section 5), so the
        filter is applied here rather than per criterion.
        """
        stmt = (
            select(Tutor)
            .where(Tutor.status == "VERIFIED")
            .options(
                selectinload(Tutor.subjects),
                selectinload(Tutor.levels),
                selectinload(Tutor.grades),
                selectinload(Tutor.tutoring_locations),
                selectinload(Tutor.experiences),
                selectinload(Tutor.documents),
                selectinload(Tutor.education),
            )
            .order_by(Tutor.public_tutor_id)
        )
        return list(session.execute(stmt).scalars().unique().all())

    def match(
        self,
        session: Session,
        job: JobPost | str,
        *,
        limit: int | None = None,
        weights: dict[str, float] | None = None,
        tutors: Iterable[Tutor] | None = None,
    ) -> list[MatchResult]:
        """Rank the VERIFIED tutors for a job, best first.

        ``job`` may be a :class:`~app.models.JobPost` or its public reference.
        Ranking is deterministic: equal scores break on the TDR ID, so the same
        database always produces the same top 5.

        Nothing is assigned. The returned candidates are advice for the admin.
        """
        post = _resolve_job(session, job)
        if post is None:
            raise ValueError(f"Unknown job: {job!r}")

        profile = job_profile(post)
        if weights is not None:
            effective = normalise_weights(weights)
        else:
            effective = weights_for_job(profile)
        # A caller-supplied profile is honoured by rewriting the weights the
        # scorers use, so the two paths cannot diverge.
        profile = _with_weights(profile, effective)

        candidates = list(tutors) if tutors is not None else self.verified_tutors(session)
        results = [self.score_tutor(tutor, profile) for tutor in candidates]

        floor = settings.match_min_score
        results = [item for item in results if item.score * 100 >= floor]
        results.sort(key=lambda item: (-item.score, item.public_tutor_id))

        top = limit if limit is not None else settings.match_top_n
        results = results[:top]
        for index, item in enumerate(results, start=1):
            item.rank = index
        return results

    def match_for_new_tutor(
        self,
        session: Session,
        tutor: Tutor,
        jobs: Iterable[JobPost],
        *,
        threshold: float | None = None,
    ) -> list[tuple[JobPost, MatchResult]]:
        """Open jobs this newly verified tutor is a strong match for.

        Used when a tutor becomes VERIFIED (section 22). Only scores at or above
        the configured notification threshold are returned, so the admin is not
        pestered about weak matches.
        """
        cutoff = settings.match_notification_threshold if threshold is None else threshold
        strong: list[tuple[JobPost, MatchResult]] = []

        for post in jobs:
            if not post.is_open:
                continue
            profile = job_profile(post)
            result = self.score_tutor(tutor, profile)
            if result.percentage >= cutoff:
                result.rank = 0
                strong.append((post, result))

        strong.sort(key=lambda pair: (-pair[1].score, pair[0].public_post_id))
        return strong


@dataclass(frozen=True)
class _WeightedJobProfile(JobProfile):
    """A :class:`JobProfile` whose weights were chosen by the caller."""

    weights: dict[str, float] = field(default_factory=dict)


def _with_weights(profile: JobProfile, weights: dict[str, float]) -> JobProfile:
    """Attach explicit weights so :meth:`MatchingService.score_tutor` uses them."""
    return _WeightedJobProfile(
        post=profile.post,
        grade=profile.grade,
        subjects=profile.subjects,
        online=profile.online,
        english_level_required=profile.english_level_required,
        wants_male=profile.wants_male,
        wants_female=profile.wants_female,
        weights=dict(weights),
    )


def _profile_weights(profile: JobProfile) -> dict[str, float]:
    """The weights in force for this profile."""
    override = getattr(profile, "weights", None)
    if override:
        return dict(override)
    return weights_for_job(profile)


def _resolve_job(session: Session, job: JobPost | str) -> JobPost | None:
    if isinstance(job, JobPost):
        return job
    reference = str(job or "").strip().upper()
    if not reference:
        return None
    return session.execute(
        select(JobPost).where(JobPost.public_post_id == reference)
    ).scalar_one_or_none()


#: Shared instance; the service holds no state.
service = MatchingService()


def match(
    session: Session, job: JobPost | str, *, limit: int | None = None, **kwargs: Any
) -> list[MatchResult]:
    """Module-level shortcut for :meth:`MatchingService.match`."""
    return service.match(session, job, limit=limit, **kwargs)


def match_for_new_tutor(
    session: Session, tutor: Tutor, jobs: Iterable[JobPost], **kwargs: Any
) -> list[tuple[JobPost, MatchResult]]:
    """Module-level shortcut for :meth:`MatchingService.match_for_new_tutor`."""
    return service.match_for_new_tutor(session, tutor, jobs, **kwargs)


def score_tutor(tutor: Tutor, job: JobPost) -> MatchResult:
    """Score one tutor against one job without touching the database."""
    return service.score_tutor(tutor, job_profile(job))


__all__ = [
    "ACADEMIC_MAX_CONTRIBUTION",
    "ACADEMIC_MIN_CONTRIBUTION",
    "CriterionResult",
    "EXPERIENCE_SATURATION_YEARS",
    "JobProfile",
    "MATCH_CRITERIA",
    "MatchingService",
    "MatchResult",
    "NEUTRAL",
    "experience_years",
    "job_profile",
    "match",
    "match_for_new_tutor",
    "normalised_entrance_score",
    "score_academic",
    "score_category",
    "score_english",
    "score_experience",
    "score_grade",
    "score_location",
    "score_subject",
    "score_tutor",
    "service",
    "weights_for_job",
]