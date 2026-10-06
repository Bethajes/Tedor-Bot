"""Admin-only job match queries (Requirements 25, 26).

These endpoints expose match *scores and explanations*. They exist so the
operator dashboard can show why a candidate was recommended; the owner of the
response is the platform's admin, checked by the same API-secret guard as the
document route. No other object in the running app returns candidate details,
so nothing here has to censor itself for public use.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.auth import require_api_secret
from app.database import get_db
from app.models import JobCandidate, JobPost, Tutor
from app.services import job_match_pipeline as pipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

DbSession = Annotated[Session, Depends(get_db)]
Secret = Annotated[bool, Depends(require_api_secret)]


def _rank(candidates: list[JobCandidate]) -> list[dict[str, Any]]:
    return [
        _candidate_payload(candidate)
        for candidate in sorted(candidates, key=lambda row: row.rank)
    ]


def _candidate_payload(candidate: JobCandidate) -> dict[str, Any]:
    details = pipeline.candidate_details(candidate)
    tutor = candidate.tutor
    return {
        "job_id": candidate.job_post.public_post_id if candidate.job_post else None,
        "tutor_id": tutor.public_tutor_id,
        "display_name": tutor.display_name,
        "match_score": int(round(candidate.match_score)),
        "rank": candidate.rank,
        "status": candidate.status,
        "matched_at": candidate.matched_at.isoformat() if candidate.matched_at else None,
        "city": tutor.city,
        "locations": tutor.location_list,
        "grades": tutor.grade_list or tutor.level_list,
        "subjects": tutor.subject_list,
        "english_proficiency": tutor.english_proficiency,
        "university": tutor.university,
        "teaching_experience_years": tutor.teaching_experience_years
        if tutor.teaching_experience_years is not None
        else tutor.experience_years,
        "explanation": {
            "strengths": details.get("strengths", []),
            "weaknesses": details.get("weaknesses", []),
            "missing_information": details.get("missing", []),
            "criteria": details.get("criteria", {}),
        },
    }


@router.get("/{job_id}/matches")
def list_matches(
    job_id: str, session: DbSession, _: Secret, include_ignored: bool = Query(default=False)
):
    """Ordered candidate list for a job. Admin only."""
    job = session.execute(
        select(JobPost).where(JobPost.public_post_id == job_id.strip().upper())
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    candidates = session.execute(
        select(JobCandidate)
        .where(JobCandidate.job_post_id == job.id)
        .options(
            selectinload(JobCandidate.tutor),
            selectinload(JobCandidate.job_post),
        )
        .order_by(JobCandidate.rank)
    ).scalars().all()
    if not include_ignored:
        candidates = [row for row in candidates if row.status != "REJECTED"]
    return {"job_id": job.public_post_id, "count": len(candidates), "candidates": _rank(list(candidates))}


@router.get("/{job_id}/matches/{tutor_id}")
def get_match(job_id: str, tutor_id: str, session: DbSession, _: Secret):
    """One candidate's explanation in detail. Admin only."""
    candidate = session.execute(
        select(JobCandidate)
        .join(JobPost, JobCandidate.job_post_id == JobPost.id)
        .join(Tutor, JobCandidate.tutor_id == Tutor.id)
        .where(JobPost.public_post_id == job_id.strip().upper())
        .where(Tutor.public_tutor_id == tutor_id.strip().upper())
        .options(
            selectinload(JobCandidate.tutor).selectinload(Tutor.subjects),
            selectinload(JobCandidate.job_post),
        )
    ).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    return _candidate_payload(candidate)


__all__ = ["router"]