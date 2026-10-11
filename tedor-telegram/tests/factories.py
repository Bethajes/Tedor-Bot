"""Test data factories."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.enums import TutorStatus
from app.models import Tutor
from app.services import tutor_service


def tutor_payload(telegram_id: int, **overrides: Any) -> dict[str, Any]:
    """A complete, valid tutor application payload."""
    payload: dict[str, Any] = {
        "telegram_user_id": telegram_id,
        "name": f"Tutor {telegram_id}",
        "display_name": f"Tutor {telegram_id}",
        "phone": f"+2519{telegram_id:08d}"[:15],
        "email": f"tutor{abs(telegram_id)}@example.com",
        "country": "Ethiopia",
        "city": "Addis Ababa",
        "bio": "Experienced tutor.",
        "subjects": ["Mathematics"],
        "levels": ["Grade 9-10"],
        "languages": ["English"],
        "experience_years": 4,
        "teaching_mode": "ONLINE",
        "etb_rate": 400.0,
        "usd_rate": 4.0,
        "timezone": "EAT (UTC+3)",
        "education": [
            {
                "institution": "Addis Ababa University",
                "degree": "BSc",
                "field": "Mathematics",
                "graduation_year": 2018,
            }
        ],
        "availability": [
            {"day": "Monday", "start_time": "09:00", "end_time": "12:00", "timezone": "EAT"}
        ],
    }
    payload.update(overrides)
    return payload


def create_tutor(db: Session, telegram_id: int, **overrides: Any) -> Tutor:
    return tutor_service.create_tutor(db, tutor_payload(telegram_id, **overrides))


def student_request_payload(telegram_id: int, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "telegram_user_id": telegram_id,
        "parent_name": "Parent",
        "student_name": "Student",
        "phone": "+251911000000",
        "contact_handle": "@parent",
        "country": "Ethiopia",
        "education_level": "Grade 9-10",
        "subject": "Mathematics",
        "preferred_language": "English",
        "mode": "ONLINE",
        "budget": 500,
        "schedule": "Weekdays 16:00-18:00",
        "additional_requirements": "",
    }
    payload.update(overrides)
    return payload


def job_post_payload(telegram_id: int, **overrides: Any) -> dict[str, Any]:
    """A complete, valid job-post payload — the seven collected variables only.

    There is no ``requirements`` key on purpose: that block is static template
    content and must never arrive from a caller.
    """
    payload: dict[str, Any] = {
        "telegram_user_id": telegram_id,
        "location": "Ayat Tsebel",
        "student_level": "4th Grader",
        "tutor_category": "Both Male and Female",
        "duration": "1 hour/day",
        "frequency": "3 days/week",
        "subjects": "Major Subjects",
        "compensation": "300 Birr / 1 hour",
    }
    payload.update(overrides)
    return payload


__all__ = [
    "TutorStatus",
    "create_tutor",
    "job_post_payload",
    "student_request_payload",
    "tutor_payload",
]