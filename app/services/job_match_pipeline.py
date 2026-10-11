"""Automatic tutor-job matching at the point of publish (Requirements 17–22).

When an admin publishes a job, this module immediately scores every VERIFIED
tutor, persists the top 5 as :class:`~app.models.JobCandidate` rows and sends an
explainable recommendation report to the admin. It is also what the admin's
``/match <job_id>`` command reruns when tutors or job requirements change.

Nothing here assigns a tutor. A candidate row is advice for the admin, and only
the admin's explicit :class:`CandidateStatus.SELECTED` action closes the loop.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.enums import CandidateStatus
from app.models import (
    JobCandidate,
    JobPost,
    Tutor,
    TutorDocument,
    TutorExperience,
    TutorGrade,
    TutorLocation,
    TutorSubject,
)
from app.services import matching_service as matching
from app.services.matching_service import MatchResult

logger = logging.getLogger(__name__)

#: Rank medals, per the report format in the specification (section 17).
_MEDALS: tuple[str, ...] = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣")


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def persist_candidates(
    session: Session, job: JobPost, results: Sequence[MatchResult]
) -> list[JobCandidate]:
    """Replace the persisted recommendations for ``job`` with ``results``.

    A re-match replaces rather than appends: the shortlist is about *this* set
    of results, and an admin who ignored candidate 3 yesterday should see
    today's verdict. Previously existing rows for the job are deleted first so
    there are never stale recommendations for tutors the engine no longer
    recommends.
    """
    session.query(JobCandidate).filter(JobCandidate.job_post_id == job.id).delete(
        synchronize_session=False
    )
    rows: list[JobCandidate] = []
    for result in results:
        row = JobCandidate(
            job_post_id=job.id,
            tutor_id=result.tutor.id,
            match_score=float(result.percentage),
            rank=result.rank,
            status=str(CandidateStatus.RECOMMENDED),
            details=json.dumps(result.to_dict(), ensure_ascii=False),
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def get_job_candidates(session: Session, job_reference: str) -> list[JobCandidate]:
    """Persisted recommendations for a job, best first."""
    job = _resolve_job(session, job_reference)
    if job is None:
        return []
    return list(
        session.execute(
            select(JobCandidate)
            .where(JobCandidate.job_post_id == job.id)
            .options(
                selectinload(JobCandidate.tutor).selectinload(Tutor.subjects),
                selectinload(JobCandidate.tutor).selectinload(Tutor.grades),
                selectinload(JobCandidate.tutor).selectinload(Tutor.tutoring_locations),
            )
            .order_by(JobCandidate.rank)
        )
        .scalars()
        .all()
    )


def set_candidate_status(
    session: Session,
    job_reference: str,
    tutor_reference: str,
    status: CandidateStatus | str,
) -> JobCandidate | None:
    """Move one candidate through the admin's lifecycle.

    Only the admin can call this, and the candidate row must already exist — a
    status change for an un-recommended tutor is a no-op rather than an
    insertion, because matching is deterministic and a fresh row would imply a
    fresh match that never ran.
    """
    job = _resolve_job(session, job_reference)
    if job is None:
        return None
    candidate = session.execute(
        select(JobCandidate)
        .where(JobCandidate.job_post_id == job.id)
        .join(Tutor, JobCandidate.tutor_id == Tutor.id)
        .where(Tutor.public_tutor_id == str(tutor_reference).strip().upper())
    ).scalar_one_or_none()
    if candidate is None:
        return None
    candidate.status = str(CandidateStatus(str(status).upper()))
    session.flush()
    return candidate


# ---------------------------------------------------------------------------
# reporting (sections 17, 18)
# ---------------------------------------------------------------------------


def _medal(rank: int) -> str:
    index = rank - 1
    return _MEDALS[index] if 0 <= index < len(_MEDALS) else f"#{rank}"


def format_criterion_line(label: str, ok: bool) -> str:
    return f"{label}: {'✓' if ok else '—'}"


def _esc(value: Any) -> str:
    """Escape admin/tutor-entered text for Telegram's HTML parse mode.

    Defined locally rather than imported from the bot layer, because services
    must not depend on handlers.
    """
    text = str(value if value not in (None, "") else "—")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_candidate_card(result: MatchResult) -> str:
    """One candidate in the report's section-17 shape.

    Every interpolated value is HTML-escaped: tutors and admins type these
    strings, and a stray ``<b>`` would corrupt the message.
    """
    tutor = result.tutor
    grade = result.criterion("grade")
    subject = result.criterion("subject")
    english = result.criterion("english")
    location = result.criterion("location")

    locations = ", ".join(tutor.location_list) or tutor.city or "—"
    grades = ", ".join(tutor.grade_list[:3]) or ", ".join(tutor.level_list[:3]) or "—"
    subjects = ", ".join(tutor.subject_list[:3]) or "—"

    lines = [
        f"{_medal(result.rank)} <code>{_esc(tutor.public_tutor_id)}</code> — {result.percentage}% MATCH",
        f"👤 {_esc(tutor.display_name)}",
        "",
        f"📍 Location: {_esc(locations)} {'✓' if location and location.score >= 0.85 else ''}",
        f"🎓 Grade: {_esc(grades)} {'✓' if grade and grade.score >= 0.99 else ''}",
        f"📚 Subject: {_esc(subjects)} {'✓' if subject and subject.score >= 0.99 else ''}",
    ]
    if tutor.english_proficiency is not None:
        lines.append(f"🇬🇧 English: {tutor.english_proficiency}/10 {'✓' if english and english.score >= 0.7 else ''}")
    if tutor.entrance_exam_score is not None:
        exam = f"{tutor.entrance_exam_score:g}"
        if tutor.entrance_exam_max_score:
            exam += f"/{tutor.entrance_exam_max_score:g}"
        lines.append(f"🎓 Entrance exam: {exam}")
    if tutor.teaching_experience_years is not None:
        lines.append(f"👨‍🏫 Experience: {tutor.teaching_experience_years} years")
    elif tutor.experience_years:
        lines.append(f"👨‍🏫 Experience: {tutor.experience_years} years")
    if tutor.university:
        lines.append(f"🏫 University: {_esc(tutor.university)}")

    if result.strengths:
        lines.append("")
        lines.append("Why recommended:")
        lines.extend(f"✓ {_esc(note)}" for note in result.strengths)

    if result.weaknesses:
        lines.append("")
        lines.append("Weak:")
        lines.extend(f"⚠ {_esc(note)}" for note in result.weaknesses)

    if result.missing:
        lines.append("")
        lines.append("Missing information:")
        lines.extend(f"⚠ {_esc(note)}" for note in result.missing)

    return "\n".join(lines)


def format_report(job: JobPost, results: Sequence[MatchResult], *, threshold_note: str = "") -> str:
    """The whole recommendation report sent to the admin."""
    lines = [
        "🎯 <b>TOP TUTOR MATCHES</b>",
        "",
        f"Job: {_esc(f'{job.student_level} {job.subjects}')}".rstrip(),
        f"Location: {_esc(job.location)}",
        f"Compensation: {_esc(job.compensation)}",
        f"Status: {job.status}",
    ]
    if threshold_note:
        lines.append(threshold_note)
    lines.append("")

    if not results:
        lines.append("No verified tutors matched the configured minimum score yet.")
        lines.append("Verify more tutors or rerun with a wider net via /match.")
        return "\n".join(lines)

    for result in results:
        lines.append(format_candidate_card(result))
        lines.append("")
        lines.append("--------------------------------")
        lines.append("")
    # The trailing divider is noise.
    if lines[-1] == "":
        lines.pop()
    if lines and lines[-1] == "--------------------------------":
        lines.pop()
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# running the match (requirements 17, 21, 22)
# ---------------------------------------------------------------------------


def run_match_for_job(session: Session, job: JobPost) -> list[MatchResult]:
    """Run the engine for a job and persist the candidates."""
    results = matching.match(session, job)
    persist_candidates(session, job, results)
    logger.info(
        "Job matching completed",
        extra={"post_id": job.public_post_id, "candidates": len(results)},
    )
    return results


def open_jobs(session: Session) -> list[JobPost]:
    """Jobs still accepting candidates, for the new-tutor re-match."""
    rows = session.execute(
        select(JobPost).where(JobPost.status.in_(("DRAFT", "PUBLISHED")))
    ).scalars()
    return [post for post in rows if post.is_open]


# ---------------------------------------------------------------------------
# dashboard statistics (section 28)
# ---------------------------------------------------------------------------


def incomplete_profile_count(session: Session) -> int:
    """How many tutors still have at least one profile field unfilled.

    Counted in SQL rather than by loading every tutor and all of their
    collections, because production holds 1,000+ tutors and this runs on every
    ``/stats``. The conditions mirror ``PROFILE_FIELDS`` in
    :mod:`app.services.tutor_profile_service` exactly, so the dashboard count
    can never disagree with the per-tutor percentage shown in the detail view.

    A tutor with only the legacy ``TutorLevel`` bands still counts as
    incomplete: they have genuinely not told us their grade range yet, which is
    precisely what ``/complete_profile`` exists to collect.
    """
    from app.enums import ACADEMIC_DOCUMENT_TYPES

    academic = set(ACADEMIC_DOCUMENT_TYPES)
    has_academic_document = (
        select(TutorDocument.id)
        .where(
            TutorDocument.tutor_id == Tutor.id,
            TutorDocument.document_type.in_(academic),
        )
        .exists()
    )

    missing_any = (
        ~select(TutorLocation.id).where(TutorLocation.tutor_id == Tutor.id).exists()
        | ~select(TutorGrade.id).where(TutorGrade.tutor_id == Tutor.id).exists()
        | ~select(TutorSubject.id).where(TutorSubject.tutor_id == Tutor.id).exists()
        | (Tutor.english_proficiency.is_(None))
        | (Tutor.entrance_exam_score.is_(None))
        | (Tutor.university.is_(None))
        | (Tutor.age.is_(None))
        | (Tutor.current_address.is_(None))
        | ~has_academic_document
        | (
            ~select(TutorExperience.id)
            .where(TutorExperience.tutor_id == Tutor.id)
            .exists()
            & Tutor.teaching_experience_years.is_(None)
        )
    )

    subquery = select(Tutor.id).where(missing_any).subquery()
    return int(session.execute(select(func.count()).select_from(subquery)).scalar_one())


def platform_statistics(session: Session) -> dict[str, int]:
    """Every counter the admin dashboard shows (section 28).

    Composed from the existing tutor counters plus the job and candidate ones.
    ``DOCUMENTS_SUBMITTED`` appears alongside the legacy statuses because the
    Ethiopian flow can leave a tutor in it.
    """
    from app.services import tutor_service

    stats: dict[str, int] = dict(tutor_service.statistics(session))
    stats.setdefault("DOCUMENTS_SUBMITTED", 0)

    stats["incomplete_profiles"] = incomplete_profile_count(session)
    stats["open_jobs"] = len(open_jobs(session))
    stats["total_job_posts"] = int(
        session.execute(select(func.count()).select_from(JobPost)).scalar_one()
    )
    stats["job_candidates"] = int(
        session.execute(select(func.count()).select_from(JobCandidate)).scalar_one()
    )
    stats["selected_tutors"] = int(
        session.execute(
            select(func.count())
            .select_from(JobCandidate)
            .where(JobCandidate.status == str(CandidateStatus.SELECTED))
        ).scalar_one()
    )
    stats["shortlisted_candidates"] = int(
        session.execute(
            select(func.count())
            .select_from(JobCandidate)
            .where(JobCandidate.status == str(CandidateStatus.SHORTLISTED))
        ).scalar_one()
    )
    return stats


# ---------------------------------------------------------------------------
# resolution helpers
# ---------------------------------------------------------------------------


def _resolve_job(session: Session, job_reference: str) -> JobPost | None:
    reference = str(job_reference or "").strip().upper()
    if not reference:
        return None
    return session.execute(
        select(JobPost).where(JobPost.public_post_id == reference)
    ).scalar_one_or_none()


def get_job(session: Session, job_reference: str) -> JobPost | None:
    """Public accessor for the candidate action handlers."""
    return _resolve_job(session, job_reference)


def candidate_for(
    session: Session, job_reference: str, tutor_reference: str
) -> JobCandidate | None:
    """One candidate row, resolved by the same references the callbacks carry."""
    job = _resolve_job(session, job_reference)
    if job is None:
        return None
    return session.execute(
        select(JobCandidate)
        .where(JobCandidate.job_post_id == job.id)
        .join(Tutor, JobCandidate.tutor_id == Tutor.id)
        .where(Tutor.public_tutor_id == str(tutor_reference).strip().upper())
        .options(selectinload(JobCandidate.tutor))
    ).scalar_one_or_none()


def candidate_details(row: JobCandidate) -> dict[str, Any]:
    """The persisted explanation for a candidate row."""
    try:
        return json.loads(row.details or "{}")
    except (ValueError, TypeError):
        return {}


__all__ = [
    "CandidateStatus",
    "candidate_details",
    "candidate_for",
    "format_candidate_card",
    "format_report",
    "get_job",
    "get_job_candidates",
    "incomplete_profile_count",
    "open_jobs",
    "persist_candidates",
    "platform_statistics",
    "run_match_for_job",
    "set_candidate_status",
]