"""Job post service.

The generated text is *never* accepted from the caller: :func:`create_job_post`
renders it from the static template so the requirements block cannot be
supplied, overridden, or edited (Requirement: requirements are STATIC).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.enums import JobPostStatus
from app.models import IdCounter, JobPost, utcnow
from app.templates.tutor_job_post import (
    REQUIRED_FIELDS,
    JobPostTemplateError,
    location_hashtag,
    render_job_post,
)

logger = logging.getLogger(__name__)

JOB_POST_PREFIX = "JOB"
JOB_POST_PATTERN = re.compile(r"^JOB-\d{6}$")
JOB_POST_COUNTER = "job_post"


class JobPostValidationError(ValueError):
    """Raised when job post data fails validation."""


def _next_job_post_sequence(session: Session) -> int:
    highest = (
        session.execute(
            select(JobPost.id).order_by(JobPost.id.desc()).limit(1)
        ).scalar_one_or_none()
        or 0
    )
    counter_row = session.execute(
        select(IdCounter).where(IdCounter.name == JOB_POST_COUNTER)
    ).scalar_one_or_none()
    if counter_row is None:
        try:
            with session.begin_nested():
                session.add(IdCounter(name=JOB_POST_COUNTER, value=highest + 1))
            return highest + 1
        except IntegrityError:  # pragma: no cover - concurrent first insert
            session.expire_all()
            row = session.execute(
                select(IdCounter).where(IdCounter.name == JOB_POST_COUNTER)
            ).scalar_one()
            row.value += 1
            session.flush()
            return int(row.value)
    counter_row.value = max(int(counter_row.value), highest) + 1
    session.flush()
    return int(counter_row.value)


def generate_job_post_id(session: Session) -> str:
    """Return the next unique public job post ID (``JOB-000001``)."""
    sequence = _next_job_post_sequence(session)
    post_id = f"{JOB_POST_PREFIX}-{sequence:06d}"
    while (
        session.execute(
            select(JobPost.id).where(JobPost.public_post_id == post_id)
        ).scalar_one_or_none()
        is not None
    ):  # pragma: no cover - defensive
        sequence = _next_job_post_sequence(session)
        post_id = f"{JOB_POST_PREFIX}-{sequence:06d}"
    return post_id


def validate_job_post_data(data: dict[str, Any]) -> None:
    """Reject anything that is not one of the seven collected variables."""
    missing = [name for name in REQUIRED_FIELDS if not str(data.get(name) or "").strip()]
    if missing:
        raise JobPostValidationError(
            "Missing required job post fields: " + ", ".join(missing)
        )
    if not data.get("telegram_user_id"):
        raise JobPostValidationError("Missing required job post fields: telegram_user_id")


def build_generated_text(data: dict[str, Any]) -> str:
    """Render the static template from the collected variables."""
    try:
        return render_job_post(
            location=str(data.get("location") or ""),
            student_level=str(data.get("student_level") or ""),
            tutor_category=str(data.get("tutor_category") or ""),
            duration=str(data.get("duration") or ""),
            frequency=str(data.get("frequency") or ""),
            subjects=str(data.get("subjects") or ""),
            compensation=str(data.get("compensation") or ""),
        )
    except JobPostTemplateError as exc:
        raise JobPostValidationError(str(exc)) from exc


def create_job_post(session: Session, data: dict[str, Any]) -> JobPost:
    """Persist a job post; ``generated_text`` always comes from the template."""
    validate_job_post_data(data)
    generated_text = build_generated_text(data)

    post = JobPost(
        public_post_id=generate_job_post_id(session),
        telegram_user_id=int(data["telegram_user_id"]),
        location=str(data["location"]).strip(),
        student_level=str(data["student_level"]).strip(),
        tutor_category=str(data["tutor_category"]).strip(),
        duration=str(data["duration"]).strip(),
        frequency=str(data["frequency"]).strip(),
        subjects=str(data["subjects"]).strip(),
        compensation=str(data["compensation"]).strip(),
        generated_text=generated_text,
        status=str(data.get("status") or JobPostStatus.DRAFT).upper(),
    )
    session.add(post)
    session.flush()
    logger.info(
        "Job post created",
        extra={
            "post_id": post.public_post_id,
            "location": post.location,
            "location_hashtag": location_hashtag(post.location),
        },
    )
    return post


def get_job_post_by_id(session: Session, post_id: str) -> JobPost | None:
    return session.execute(
        select(JobPost).where(
            JobPost.public_post_id == str(post_id).strip().upper()
        )
    ).scalar_one_or_none()


def get_job_post_by_telegram_id(
    session: Session, telegram_user_id: int
) -> JobPost | None:
    return session.execute(
        select(JobPost)
        .where(JobPost.telegram_user_id == int(telegram_user_id))
        .order_by(JobPost.id.desc())
    ).scalars().first()


def list_job_posts(session: Session, limit: int = 20) -> list[JobPost]:
    return list(
        session.execute(
            select(JobPost).order_by(JobPost.id.desc()).limit(limit)
        ).scalars()
    )


def mark_published(
    session: Session,
    post: JobPost,
    chat_id: int | None,
    message_id: int | None,
) -> JobPost:
    """Record the Telegram message the post was published to."""
    post.status = str(JobPostStatus.PUBLISHED)
    post.telegram_chat_id = int(chat_id) if chat_id is not None else None
    post.telegram_message_id = int(message_id) if message_id is not None else None
    post.published_at = utcnow()
    post.updated_at = utcnow()
    session.flush()
    logger.info(
        "Job post published",
        extra={
            "post_id": post.public_post_id,
            "telegram_message_id": post.telegram_message_id,
        },
    )
    return post


def format_job_post_summary(post: JobPost) -> str:
    """Plain-text variable list shown next to the preview."""
    return "\n".join(
        [
            f"Post ID: {post.public_post_id}",
            f"Location: {post.location}",
            f"Student's Level: {post.student_level}",
            f"Tutor Category: {post.tutor_category}",
            f"Duration: {post.duration}",
            f"Frequency: {post.frequency}",
            f"Target Subjects: {post.subjects}",
            f"Compensation: {post.compensation}",
            f"Status: {post.status}",
        ]
    )


__all__ = [
    "JOB_POST_PATTERN",
    "JOB_POST_PREFIX",
    "JobPostValidationError",
    "build_generated_text",
    "create_job_post",
    "format_job_post_summary",
    "generate_job_post_id",
    "get_job_post_by_id",
    "get_job_post_by_telegram_id",
    "list_job_posts",
    "mark_published",
    "validate_job_post_data",
]