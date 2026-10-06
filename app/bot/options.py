"""Selectable option lists offered by the conversation keyboards."""

from __future__ import annotations

SUBJECTS: tuple[str, ...] = (
    "Mathematics",
    "Physics",
    "Chemistry",
    "Biology",
    "English",
    "Amharic",
    "Geography",
    "History",
    "Economics",
    "Computer Science",
    "ICT",
    "Programming",
    "Accounting",
    "Business",
    "Creative Arts",
    "Music",
    "Sports",
    "French",
    "Arabic",
    "Other",
)

LEVELS: tuple[str, ...] = (
    "Grade 1-3",
    "Grade 4-6",
    "Grade 7-8",
    "Grade 9-10",
    "Grade 11-12",
    "University",
    "Postgraduate",
    "Adult/Professional",
    "All Levels",
)

LANGUAGES: tuple[str, ...] = (
    "Amharic",
    "English",
    "Afaan Oromo",
    "Tigrinya",
    "Somali",
    "Afar",
    "Sidamo",
    "Arabic",
    "French",
    "Other",
)

TEACHING_MODES: tuple[tuple[str, str], ...] = (
    ("ONLINE", "💻 Online"),
    ("IN_PERSON", "🏠 In person"),
    ("BOTH", "🌐 Both"),
)

COUNTRIES: tuple[str, ...] = (
    "Ethiopia",
    "Kenya",
    "Eritrea",
    "Somalia",
    "Djibouti",
    "Sudan",
    "United States",
    "United Kingdom",
    "Canada",
    "Australia",
    "Germany",
    "Saudi Arabia",
    "United Arab Emirates",
    "Other",
)

WEEKDAYS: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

TIMEZONES: tuple[str, ...] = (
    "EAT (UTC+3)",
    "UTC",
    "CAT (UTC+2)",
    "EET (UTC+2)",
    "WAT (UTC+1)",
    "Other",
)

CUSTOM_SUBJECTS_HINT = "Send other subjects as comma separated text."

# ---------------------------------------------------------------------------
# Job-post generator
# ---------------------------------------------------------------------------
#
# Only the seven essential variables get a curated list. There is no
# requirements list, and no English/academic/passion/maths question, because
# those are static template content.

JOB_POST_STUDENT_LEVELS: tuple[str, ...] = (
    "KG",
    "1st Grader",
    "2nd Grader",
    "3rd Grader",
    "4th Grader",
    "5th Grader",
    "6th Grader",
    "7th Grader",
    "8th Grader",
    "9th Grader",
    "10th Grader",
    "11th Grader",
    "12th Grader",
    "University Student",
    "Other",
)

JOB_POST_TUTOR_CATEGORIES: tuple[str, ...] = (
    "Male",
    "Female",
    "Both Male and Female",
)

JOB_POST_DURATIONS: tuple[str, ...] = (
    "Half hour/day",
    "1 hour/day",
    "2 hours/day",
    "1-2 hours/day",
    "3 hours/day",
    "Other",
)

JOB_POST_FREQUENCIES: tuple[str, ...] = (
    "2 days/week",
    "3 days/week",
    "4 days/week",
    "5 days/week",
    "6 days/week",
    "Daily",
    "Other",
)

JOB_POST_SUBJECTS: tuple[str, ...] = (
    "Major Subjects",
    "Mathematics",
    "English",
    "Physics",
    "Chemistry",
    "Biology",
    "Science",
    "Computer Science",
    "Geography",
    "History",
    "Other",
)

JOB_POST_LOCATIONS: tuple[str, ...] = (
    "Ayat Tsebel",
    "Bole",
    "Saris",
    "CMC",
    "Gerji",
    "Addis Ababa",
    "Other",
)

JOB_POST_FREETEXT_HINT = "Or type your own answer."

__all__ = [
    "COUNTRIES",
    "CUSTOM_SUBJECTS_HINT",
    "JOB_POST_DURATIONS",
    "JOB_POST_FREETEXT_HINT",
    "JOB_POST_FREQUENCIES",
    "JOB_POST_LOCATIONS",
    "JOB_POST_STUDENT_LEVELS",
    "JOB_POST_SUBJECTS",
    "JOB_POST_TUTOR_CATEGORIES",
    "LANGUAGES",
    "LEVELS",
    "SUBJECTS",
    "TEACHING_MODES",
    "TIMEZONES",
    "WEEKDAYS",
]

# ---------------------------------------------------------------------------
# Ethiopian tutor onboarding
# ---------------------------------------------------------------------------

#: Areas offered in the tutoring-locations step. Derived from the same
#: registry the matching engine uses, so a recorded location never needs to be
#: guessed again.
from app.services.location_service import LOCATIONS, LocationZone

#: Areas offered in the tutoring-locations step. Derived from the same
#: registry the matching engine uses, so a recorded location never needs to be
#: guessed again.
ETH_LOCATIONS: tuple[str, ...] = tuple(
    spec.name for spec in LOCATIONS.values() if spec.zone != LocationZone.OUTSIDE
)

ETH_GRADES: tuple[str, ...] = (
    "Elementary",
    *(f"Grade {n}" for n in range(1, 13)),
    "University",
)

#: Integer level -> human label for the English proficiency picker.
ETH_ENGLISH_LEVELS: tuple[tuple[int, str], ...] = (
    (1, "1 — Beginner"),
    (2, "2 — Basic"),
    (3, "3 — Elementary"),
    (4, "4 — Lower-intermediate"),
    (5, "5 — Intermediate"),
    (6, "6 — Upper-intermediate"),
    (7, "7 — Good"),
    (8, "8 — Very good"),
    (9, "9 — Excellent"),
    (10, "10 — Near-fluent"),
)

ETH_EDUCATION_LEVELS: tuple[tuple[str, str], ...] = (
    ("UNIVERSITY_STUDENT", "University student"),
    ("UNIVERSITY_GRADUATE", "University graduate"),
    ("MASTERS_STUDENT", "Master's student"),
    ("MASTERS_GRADUATE", "Master's graduate"),
    ("OTHER", "Other"),
)

ETH_ENTRANCE_EXAM_TYPES: tuple[tuple[str, str], ...] = (
    ("EHE", "Ethiopian Higher Education Entrance Exam (EHE)"),
    ("ETHIOPIAN_ENTRANCE", "Ethiopian Secondary / School-leaving exam"),
    ("OTHER", "Other examination system"),
)

#: Documents the summary wants to see. Telegram accepts PDF and images; the
#: profile service validates against this list.
ETH_DOCUMENT_HINTS: tuple[str, ...] = (
    "PDF or image",
    "photo of a certificate",
    "document upload",
)
