"""Job applications — the tutor-initiated side of the job workflow.

A job post published to the channel carries an "Apply for This Job" button. A
tutor pressing it does not immediately submit anything: the press records
*interest* (:attr:`~app.enums.JobApplicationStatus.STARTED`), and only an
explicit "Continue Application" confirmation creates a submission. This module
owns that distinction and everything needed to keep it true under retries.

The interesting rules:

* **Applying twice is free.** ``uq_job_application`` on
  ``(job_post_id, tutor_id)`` makes a second row impossible, so
  :func:`start_or_get_application` can be called on every single press. When
  the insert loses a race, the ``IntegrityError`` is caught and the winner's row
  is re-read rather than surfacing as a failure.
* **Submitting twice is free too.** :func:`submit` re-reads the row and returns
  it untouched when ``confirmed_submitted`` is already set, so pressing
  Continue twice cannot produce a second notification or a second timestamp.
* **The evaluation is stored, not recomputed at display time.** The score, the
  serialised :class:`~app.services.matching_service.MatchResult` and the
  explanation the tutor was shown all live on the row. A bot restart between
  the recommendation and the confirmation loses nothing, and the admin reviews
  the exact reasoning the tutor saw rather than a later re-score.
* **Nothing here trusts the caller.** The tutor is always resolved from the
  Telegram user id by the caller; the job reference is validated against
  ``JOB_POST_PATTERN`` and looked up, never trusted as an identity.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.enums import JobApplicationStatus
from app.models import JobApplication, JobPost, Tutor, utcnow
from app.services import matching_service
from app.services.matching_service import MatchResult

logger = logging.getLogger(__name__)

#: Score at or above which the tutor is shown a positive recommendation.
#: Below it the tutor is still allowed to apply — this only changes the wording,
#: it never blocks anyone (see :func:`recommendation_kind`).
STRONG_MATCH_PERCENTAGE = 70.0


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------


def get_job(session: Session, job_reference: str) -> JobPost | None:
    """Resolve a job by its public reference. ``None`` when there is no such job."""
    reference = str(job_reference or "").strip().upper()
    if not reference:
        return None
    return session.execute(
        select(JobPost).where(JobPost.public_post_id == reference)
    ).scalar_one_or_none()


def get_application(
    session: Session, job_reference: str, tutor: Tutor
) -> JobApplication | None:
    """This tutor's application for this job, if one exists."""
    post = get_job(session, job_reference)
    if post is None:
        return None
    return session.execute(
        select(JobApplication).where(
            JobApplication.job_post_id == post.id,
            JobApplication.tutor_id == tutor.id,
        )
    ).scalar_one_or_none()


def list_applications(
    session: Session,
    *,
    status: Any = None,
    job_reference: str | None = None,
    limit: int = 20,
) -> list[JobApplication]:
    """Applications, newest first, optionally filtered.

    Used by the admin console. Ordered by ``id`` descending rather than by
    ``created_at`` so the order is stable when two rows share a timestamp.
    """
    stmt = select(JobApplication)
    if status is not None:
        stmt = stmt.where(JobApplication.status == str(status))
    if job_reference:
        post = get_job(session, job_reference)
        if post is None:
            return []
        stmt = stmt.where(JobApplication.job_post_id == post.id)
    stmt = stmt.order_by(JobApplication.id.desc()).limit(limit)
    return list(session.execute(stmt).unique().scalars())


def applications_for_tutor(
    session: Session, tutor: Tutor, *, limit: int = 20
) -> list[JobApplication]:
    """Every application this tutor has started, newest first."""
    return list(
        session.execute(
            select(JobApplication)
            .where(JobApplication.tutor_id == tutor.id)
            .order_by(JobApplication.id.desc())
            .limit(limit)
        )
        .unique()
        .scalars()
    )


# ---------------------------------------------------------------------------
# starting an application
# ---------------------------------------------------------------------------


def start_or_get_application(
    session: Session, tutor: Tutor, post: JobPost
) -> tuple[JobApplication, bool]:
    """Record interest, or return the existing application.

    Returns the row and whether it was created by this call. ``created`` is
    what tells the notification layer to tell the admin about a *new* press,
    which is how repeated presses avoid repeated notifications.

    Safe to call on every press: the unique constraint means a loser of the
    insert race simply reads the winner's row.
    """
    existing = session.execute(
        select(JobApplication).where(
            JobApplication.job_post_id == post.id,
            JobApplication.tutor_id == tutor.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    application = JobApplication(
        job_post_id=post.id,
        tutor_id=tutor.id,
        status=str(JobApplicationStatus.STARTED),
        confirmed_submitted=False,
        match_score=0.0,
        match_rank=0,
    )
    try:
        with session.begin_nested():
            session.add(application)
            session.flush()
    except IntegrityError:
        # Another press won the race. The nested block rolled our insert back;
        # read the committed row instead of reporting a failure.
        session.expire_all()
        winner = session.execute(
            select(JobApplication).where(
                JobApplication.job_post_id == post.id,
                JobApplication.tutor_id == tutor.id,
            )
        ).scalar_one()
        return winner, False

    logger.info(
        "Job application started",
        extra={
            "post_id": post.public_post_id,
            "tdr_id": tutor.public_tutor_id,
        },
    )
    return application, True


def set_status(
    session: Session, application: JobApplication, status: Any
) -> JobApplication:
    """Move an application to a new state.

    A withdrawn or rejected application is not resurrected by a tutor pressing
    Apply again: the caller decides whether to reopen, this only writes.
    """
    value = str(status).strip().upper()
    try:
        JobApplicationStatus(value)
    except ValueError:
        raise ValueError(f"Unknown job application status: {status!r}") from None

    if application.status != value:
        application.status = value
        application.updated_at = utcnow()
        session.flush()
        logger.info(
            "Job application status changed",
            extra={
                "application_id": application.id,
                "status": value,
                "tdr_id": application.public_tutor_id,
            },
        )
    return application


def mark_profile_incomplete(session: Session, application: JobApplication) -> JobApplication:
    """Record that the applicant still has profile work to do.

    Never downgrades a submitted application: once a tutor has confirmed, a
    later profile edit does not drag the application backwards.
    """
    if application.confirmed_submitted:
        return application
    return set_status(session, application, JobApplicationStatus.PROFILE_INCOMPLETE)


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Evaluation:
    """A tutor's fit for one job, plus what the tutor will be shown."""

    result: MatchResult
    #: Short bullet lines explaining the recommendation.
    reasons: tuple[str, ...]
    #: Caveats worth stating honestly (weaknesses and unknown information).
    cautions: tuple[str, ...]
    #: The one-sentence recommendation text stored on the row.
    text: str

    @property
    def percentage(self) -> int:
        return self.result.percentage

    @property
    def is_strong(self) -> bool:
        return self.result.score * 100 >= STRONG_MATCH_PERCENTAGE


def recommendation_kind(score: float) -> str:
    """Which wording to use for a score in 0-1.

    Both branches lead to the same buttons. The difference is honesty about how
    good the fit is, not permission: a weaker match is advised, never refused.
    """
    return "strong" if score * 100 >= STRONG_MATCH_PERCENTAGE else "partial"


def hard_block(tutor: Tutor, post: JobPost) -> str | None:
    """Why this job's mandatory requirements exclude this tutor, if they do.

    Only the tutor category is a hard requirement. Grade, subject and location
    are preferences the tutor is advised against but never blocked on: ranking
    data is incomplete often enough that treating it as a gate would refuse
    suitable tutors.
    """
    profile = matching_service.job_profile(post)
    if matching_service.category_blocks(tutor, profile):
        return (
            f"This opening is for {post.tutor_category} tutors only, and your "
            "profile is registered differently. You are welcome to apply to "
            "other openings."
        )
    return None


def evaluate(session: Session, tutor: Tutor, post: JobPost) -> Evaluation:
    """Score this tutor for this job and render the explanation.

    Uses :meth:`MatchingService.evaluate_tutor`, which applies no score floor
    and no rank cut, so a tutor below ``MATCH_MIN_SCORE`` still receives an
    honest assessment instead of disappearing.
    """
    result = matching_service.evaluate_tutor(session, tutor, post)
    reasons = tuple(result.strengths)
    cautions = (*result.weaknesses, *result.missing)

    header = (
        "🎯 This job may be a good match for you!"
        if result.score * 100 >= STRONG_MATCH_PERCENTAGE
        else "ℹ️ This job is open to you, but it is not a close fit."
    )
    lines = [header, ""]
    if reasons:
        lines.append("Reasons:")
        lines.extend(f"• {reason}" for reason in reasons)
    else:
        lines.append(
            "We could not find a strong overlap yet between this job and the "
            "details on your profile."
        )
    if cautions:
        lines.append("")
        lines.append("Worth knowing:")
        lines.extend(f"⚠️ {caution}" for caution in cautions)
    lines.append("")
    lines.append(
        "This is an automated recommendation based on your profile, not a "
        "guarantee of employment."
    )
    return Evaluation(
        result=result, reasons=reasons, cautions=cautions, text="\n".join(lines)
    )


def record_evaluation(
    session: Session, application: JobApplication, evaluation: Evaluation
) -> JobApplication:
    """Persist the evaluation on the application row.

    Storing the serialised :class:`MatchResult` alongside the rendered text is
    what lets the admin see, later, exactly why the tutor was recommended — the
    same reasoning they were shown, not a fresh re-score that may differ.
    """
    application.match_score = float(evaluation.result.score)
    application.match_rank = int(evaluation.result.rank or 0)
    application.match_details = json.dumps(
        evaluation.result.to_dict(), ensure_ascii=False
    )
    application.recommendation = evaluation.text
    application.updated_at = utcnow()
    session.flush()
    return application


def evaluate_and_store(
    session: Session, tutor: Tutor, post: JobPost, application: JobApplication
) -> Evaluation:
    """Evaluate, store the snapshot, and mark the application ready to submit."""
    evaluation = evaluate(session, tutor, post)
    record_evaluation(session, application, evaluation)
    if not application.confirmed_submitted:
        set_status(session, application, JobApplicationStatus.READY_TO_SUBMIT)
    return evaluation


# ---------------------------------------------------------------------------
# submitting
# ---------------------------------------------------------------------------


def submit(session: Session, application: JobApplication) -> tuple[JobApplication, bool]:
    """Confirm the tutor's candidacy for this job.

    Returns the row and whether *this* call performed the confirmation.
    ``confirmed`` is what gates the admin notification, so pressing Continue
    twice notifies once.
    """
    if application.confirmed_submitted:
        return application, False

    application.confirmed_submitted = True
    application.status = str(JobApplicationStatus.SUBMITTED)
    application.submitted_at = utcnow()
    application.updated_at = utcnow()
    session.flush()

    logger.info(
        "Job application submitted",
        extra={
            "application_id": application.id,
            "tdr_id": application.public_tutor_id,
        },
    )
    return application, True


def withdraw(session: Session, application: JobApplication) -> JobApplication:
    """The tutor pulls out.

    ``confirmed_submitted`` is left as it was: the fact that they once applied
    is history, and the admin console still shows the application. Only the
    status changes.
    """
    return set_status(session, application, JobApplicationStatus.WITHDRAWN)


def reopen(session: Session, application: JobApplication) -> JobApplication:
    """Let a withdrawn application be picked up again by a fresh press."""
    application.confirmed_submitted = False
    application.submitted_at = None
    return set_status(session, application, JobApplicationStatus.STARTED)


# ---------------------------------------------------------------------------
# presentation
# ---------------------------------------------------------------------------

#: How each status reads to a tutor who presses Apply again.
STATUS_LABELS: dict[str, str] = {
    str(JobApplicationStatus.STARTED): "started",
    str(JobApplicationStatus.PROFILE_INCOMPLETE): "waiting for your profile",
    str(JobApplicationStatus.READY_TO_SUBMIT): "ready to submit",
    str(JobApplicationStatus.SUBMITTED): "submitted — under review",
    str(JobApplicationStatus.UNDER_REVIEW): "under review",
    str(JobApplicationStatus.SHORTLISTED): "shortlisted",
    str(JobApplicationStatus.REJECTED): "not selected for this opening",
    str(JobApplicationStatus.WITHDRAWN): "withdrawn",
}


def status_label(status: str) -> str:
    """Readable description of an application status."""
    return STATUS_LABELS.get(
        str(status), str(status).replace("_", " ").lower()
    )


def is_terminal(status: str) -> bool:
    """Whether a further press changes nothing."""
    return str(status) in {
        str(JobApplicationStatus.SUBMITTED),
        str(JobApplicationStatus.UNDER_REVIEW),
        str(JobApplicationStatus.SHORTLISTED),
        str(JobApplicationStatus.REJECTED),
    }


def application_summary(application: JobApplication) -> str:
    """One line for a list: job, status and match percentage."""
    return (
        f"{application.job_id} • {status_label(application.status)} "
        f"• {100 * application.match_score:g}%"
    )


__all__ = [
    "STATUS_LABELS",
    "STRONG_MATCH_PERCENTAGE",
    "Evaluation",
    "applications_for_tutor",
    "application_summary",
    "evaluate",
    "evaluate_and_store",
    "get_application",
    "get_job",
    "hard_block",
    "is_terminal",
    "list_applications",
    "mark_profile_incomplete",
    "record_evaluation",
    "recommendation_kind",
    "reopen",
    "set_status",
    "start_or_get_application",
    "status_label",
    "submit",
    "withdraw",
]