"""Deterministic tutor matching (no AI / ML component in V1).

Requirement 7.6 + Property 8: results are ranked with a fixed scoring table and
always returned in descending score order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.enums import TeachingMode, TutorFilters, TutorStatus
from app.models import StudentRequest, Tutor
from app.services import grade_service
from app.services.currency import is_ethiopia
from app.services.tutor_service import search_tutors

# Deterministic scoring weights (design document).
SCORE_SUBJECT = 3
SCORE_LEVEL = 2
SCORE_COUNTRY = 2
SCORE_LANGUAGE = 1
SCORE_MODE = 1
SCORE_BUDGET = 2

MAX_SCORE = (
    SCORE_SUBJECT
    + SCORE_LEVEL
    + SCORE_COUNTRY
    + SCORE_LANGUAGE
    + SCORE_MODE
    + SCORE_BUDGET
)


@dataclass(frozen=True)
class ScoredTutor:
    """A tutor together with its match score and the reasons for it."""

    tutor: Tutor
    score: int
    reasons: tuple[str, ...] = ()

    @property
    def public_tutor_id(self) -> str:
        return self.tutor.public_tutor_id


def _matches(value: str | None, candidates: list[str]) -> bool:
    if not value:
        return False
    needle = value.strip().casefold()
    return any(needle in item.casefold() for item in candidates)


# ---------------------------------------------------------------------------
# Level vocabulary
# ---------------------------------------------------------------------------
#
# A request may now name several levels ("Grade 7-8, 9th Grader"), and requests
# can name a single grade while tutors register for a band. Parsing is delegated
# to grade_service so this module and the newer matching engine can never drift
# apart on what "Grade 9-10" means.

_LEVEL_SPLIT_RE = re.compile(r"\s*[,;]\s*|\s+and\s+", re.IGNORECASE)


def _bands(value: object) -> list[grade_service.GradeBand]:
    """Resolve one or several written levels into bands, dropping unknowns."""
    text = " ".join(str(value or "").split())
    if not text:
        return []
    resolved: list[grade_service.GradeBand] = []
    for token in [item for item in _LEVEL_SPLIT_RE.split(text) if item.strip()]:
        band = grade_service.grade_band(token)
        if band is not None:
            resolved.append(band)
    return resolved


def levels_overlap(request_levels: str | None, tutor_levels: list[str]) -> bool:
    """True when any selected level is covered by the levels a tutor teaches.

    Bands, not raw strings, so ``"9th Grader"`` matches a tutor registered for
    ``"Grade 9-10"``. Bespoke wording that cannot be resolved falls back to the
    original substring comparison, so custom labels keep their behaviour.
    """
    text = " ".join(str(request_levels or "").split())
    if not text:
        return False

    wanted = _bands(text)
    taught = [band for item in (tutor_levels or []) for band in _bands(item)]
    if not wanted or not taught:
        return _matches(text, [str(item) for item in (tutor_levels or [])])

    for job in wanted:
        for tutor in taught:
            if job.is_numeric and tutor.is_numeric:
                assert job.low is not None and job.high is not None
                assert tutor.low is not None and tutor.high is not None
                if min(job.high, tutor.high) >= max(job.low, tutor.low):
                    return True
            elif not job.is_numeric and not tutor.is_numeric:
                # University against university, adult against adult.
                if job.key == tutor.key:
                    return True
    return False


def tutor_rate_for_country(tutor: Tutor, country: str | None) -> float | None:
    return tutor.etb_rate if is_ethiopia(country) else tutor.usd_rate


def within_budget(tutor: Tutor, request: StudentRequest) -> bool:
    budget = request.budget
    rate = tutor_rate_for_country(tutor, request.country)
    if budget is None or rate is None:
        return False
    return rate <= float(budget)


def mode_matches(tutor_mode: str, wanted: str | None) -> bool:
    """``ONLINE`` wanted -> ONLINE or BOTH tutors match, and vice versa."""
    if not wanted:
        return False
    tutor_mode = (tutor_mode or "").upper()
    wanted = wanted.upper()
    return tutor_mode == wanted or tutor_mode == str(TeachingMode.BOTH)


def score_tutor(tutor: Tutor, request: StudentRequest) -> ScoredTutor:
    """Compute the deterministic score for one tutor/request pair."""
    score = 0
    reasons: list[str] = []

    if _matches(request.subject, tutor.subject_list):
        score += SCORE_SUBJECT
        reasons.append("subject")

    if levels_overlap(request.education_level, tutor.level_list):
        score += SCORE_LEVEL
        reasons.append("level")

    if is_ethiopia(request.country) and is_ethiopia(tutor.country):
        score += SCORE_COUNTRY
        reasons.append("country")
    elif _matches(request.country, [tutor.country]):
        score += SCORE_COUNTRY
        reasons.append("country")

    if _matches(request.preferred_language, tutor.language_list):
        score += SCORE_LANGUAGE
        reasons.append("language")

    if mode_matches(tutor.teaching_mode, request.mode):
        score += SCORE_MODE
        reasons.append("mode")

    if within_budget(tutor, request):
        score += SCORE_BUDGET
        reasons.append("budget")

    return ScoredTutor(tutor=tutor, score=score, reasons=tuple(reasons))


def match_tutors(
    session: Session,
    request: StudentRequest,
    limit: int = 10,
    require_subject: bool = True,
) -> list[ScoredTutor]:
    """Return VERIFIED tutors matching the request, best score first."""
    filters: dict[str, Any] = {
        "status": str(TutorStatus.VERIFIED),
        "subject": request.subject if require_subject else None,
        "mode": None,
    }
    candidates = search_tutors(session, TutorFilters(**filters))

    scored = [score_tutor(tutor, request) for tutor in candidates]
    scored = [item for item in scored if item.score > 0]
    scored.sort(key=lambda item: (-item.score, item.tutor.public_tutor_id))
    return scored[:limit]


def describe_request(request: StudentRequest) -> dict[str, Any]:
    """Normalised request attributes used for filtering and scoring."""
    return {
        "subject": request.subject,
        "level": request.education_level,
        "country": request.country,
        "language": request.preferred_language,
        "mode": request.mode,
        "budget": request.budget,
    }


def format_tutor_line(item: ScoredTutor, index: int | None = None) -> str:
    """Single line summary used in the Telegram match list."""
    tutor = item.tutor
    prefix = f"{index}. " if index else ""
    subjects = ", ".join(tutor.subject_list) or "—"
    rate = tutor.etb_rate if is_ethiopia(tutor.country) else tutor.usd_rate
    currency = "ETB" if is_ethiopia(tutor.country) else "USD"
    rate_text = f"{rate:g} {currency}/hr" if rate is not None else "Rate on request"
    return (
        f"{prefix}✅ {tutor.display_name} ({tutor.public_tutor_id})\n"
        f"    📚 {subjects}\n"
        f"    🎓 {', '.join(tutor.level_list) or '—'} | 🧑‍🏫 {tutor.teaching_mode} | 💰 {rate_text}\n"
        f"    Match score: {item.score}/{MAX_SCORE}"
    )


__all__ = [
    "MAX_SCORE",
    "ScoredTutor",
    "describe_request",
    "format_tutor_line",
    "levels_overlap",
    "match_tutors",
    "mode_matches",
    "score_tutor",
    "tutor_rate_for_country",
    "within_budget",
]