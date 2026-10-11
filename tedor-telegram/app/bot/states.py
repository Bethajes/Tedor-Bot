"""ConversationHandler state constants (Requirement 2.1, 2.2)."""

from __future__ import annotations

from enum import IntEnum


class TutorStates(IntEnum):
    """The 26 steps of the tutor application."""

    FULL_NAME = 1
    DISPLAY_NAME = 2
    PHONE = 3
    EMAIL = 4
    COUNTRY = 5
    CITY = 6
    PHOTO = 7
    SUBJECTS = 8
    LEVELS = 9
    TEACHING_MODE = 10
    LANGUAGES = 11
    EXPERIENCE = 12
    BIO = 13
    EDU_INSTITUTION = 14
    EDU_DEGREE = 15
    EDU_FIELD = 16
    EDU_YEAR = 17
    ETB_RATE = 18
    USD_RATE = 19
    AVAIL_DAYS = 20
    AVAIL_TIMES = 21
    TIMEZONE = 22
    DOC_CV = 23
    DOC_DEGREE = 24
    DOC_EXTRA = 25
    SUMMARY = 26


class EthiopianTutorStates(IntEnum):
    """The Ethiopian tutor onboarding flow (new, structured).

    Kept as a separate enum from the legacy 26-step ``TutorStates`` so the old
    flow and its tests stay exactly as they were. Values are spaced well away
    from every other flow's band so ``state_name`` can never mislabel a state.
    """

    FULL_NAME = 500
    GENDER = 501
    AGE = 502
    PHONE = 503
    CURRENT_ADDRESS = 504
    LOCATIONS = 505
    GRADES = 506
    SUBJECTS = 507
    ENGLISH = 508
    ENGLISH_VOICE = 509
    EDUCATION_LEVEL = 510
    UNIVERSITY = 511
    DEPARTMENT = 512
    UNIVERSITY_YEAR = 513
    CGPA = 514
    ENTRANCE_TYPE = 515
    ENTRANCE_MAX_SCORE = 516
    ENTRANCE_SCORE = 517
    ENTRANCE_YEAR = 518
    #: Kept as a separate state after the entrance-exam block; it reuses no
    #: value from the removed ``ENTRANCE_TYPE`` so old callbacks stay inert.
    ACADEMIC_STREAM = 523
    #: Secondary-school results, collected for ranking. One state per exam so
    #: each can be skipped on its own — a tutor who sat only some of them still
    #: gets a comparable score.
    GRADE8_AVERAGE = 524
    GRADE9_AVERAGE = 525
    GRADE10_AVERAGE = 526
    GRADE11_AVERAGE = 527
    GRADE12_AVERAGE = 528
    EXPERIENCE_YEARS = 519
    EXPERIENCE_DESCRIPTION = 520
    DOCUMENTS = 521
    SUMMARY = 522


class StudentStates(IntEnum):
    """Parent/student tutor request flow."""

    PARENT_NAME = 100
    STUDENT_NAME = 101
    COUNTRY = 102
    EDUCATION_LEVEL = 103
    SUBJECT = 104
    LANGUAGE = 105
    MODE = 106
    #: Asked after the teaching mode, so an online-only request can skip it.
    #: 114 is the next free value in this flow's band; every other band is
    #: spaced away from 100-113, so the value cannot be mislabelled.
    LOCATION = 114
    BUDGET = 107
    SCHEDULE = 108
    REQUIREMENTS = 109
    CONTACT = 110
    SUMMARY = 111
    RESULTS = 112
    TUTOR_DETAIL = 113


class SupportStates(IntEnum):
    """Customer support flow."""

    CATEGORY = 200
    MESSAGE = 201
    DONE = 202


class AdminStates(IntEnum):
    """Admin interactive flows."""

    MENU = 300
    PENDING_LIST = 301
    SEARCH_SUBJECT = 302
    SEARCH_COUNTRY = 303
    SEARCH_STATUS = 304
    SEARCH_RESULTS = 305
    REVIEW_DETAIL = 306
    REJECT_REASON = 307
    REQUEST_INFO_INPUT = 308
    #: Dashboard sections (specification section 28).
    TUTOR_LIST = 309
    JOB_MATCHES_LIST = 310
    JOB_CANDIDATES = 311
    ADMIN_REPLY_INPUT = 309


class ApplicationAdminStates(IntEnum):
    """Reviewing job applications submitted through the Apply button.

    A separate enum rather than more ``AdminStates`` values so the review
    screens have room to grow and ``state_name`` can still label them.
    """

    LIST = 320
    DETAIL = 321


class JobPostStates(IntEnum):
    """Admin job-post generator flow.

    Exactly the seven essential variables are collected. There is no
    requirements step: that block is static template content.
    """

    LOCATION = 400
    STUDENT_LEVEL = 401
    TUTOR_CATEGORY = 402
    DURATION = 403
    FREQUENCY = 404
    SUBJECTS = 405
    COMPENSATION = 406
    PREVIEW = 407
    EDIT_MENU = 408


CANCEL_TEXT = "❌ Application cancelled. Type /start to begin again."


def state_name(value: int | None) -> str:
    """Human readable state name used in logs."""
    if value is None:
        return "NONE"
    for enum_cls in (
        TutorStates,
        EthiopianTutorStates,
        StudentStates,
        SupportStates,
        AdminStates,
        ApplicationAdminStates,
        JobPostStates,
    ):
        try:
            return f"{enum_cls.__name__}.{enum_cls(value).name}"
        except ValueError:
            continue
    return str(value)  # pragma: no cover


__all__ = [
    "AdminStates",
    "ApplicationAdminStates",
    "CANCEL_TEXT",
    "JobPostStates",
    "StudentStates",
    "SupportStates",
    "TutorStates",
    "state_name",
]

class LocationAdminStates(IntEnum):
    """Reviewing locations the platform has never seen before.

    Kept as its own band (600-605) rather than extending :class:`AdminStates`
    because the two conversations are independent: a location review is a
    work list an admin works through, not a page of the dashboard. Bands are
    asserted disjoint by the flow tests, so 600 is safely clear of every
    existing flow (1-26, 100-114, 200-202, 300-311, 400-408, 500-523).
    """

    LIST = 600
    DETAIL = 601
    MERGE_PICK = 602
    REASON_INPUT = 603
    ALIAS_INPUT = 604
    COORDS_INPUT = 605
