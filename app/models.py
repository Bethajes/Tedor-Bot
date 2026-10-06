"""SQLAlchemy ORM models.

Requirement 12.1: every database interaction goes through the ORM so the engine
can be swapped (SQLite -> PostgreSQL) by changing the connection string only.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    sql,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

TEACHING_MODES = ("ONLINE", "IN_PERSON", "BOTH")

#: Union of the legacy 26-step statuses and the Ethiopian onboarding statuses.
#: ``DOCUMENTS_SUBMITTED`` is only reachable through the new flow, but every
#: status handler accepts it so an admin can move a tutor between the two.
TUTOR_STATUSES = (
    "PENDING",
    "DOCUMENTS_SUBMITTED",
    "UNDER_REVIEW",
    "VERIFIED",
    "REJECTED",
    "SUSPENDED",
)

#: Length of ``status`` and ``member_referral_status``. The longest value,
#: ``PENDING_CONFIRMATION`` (20 chars), is too close to the original
#: VARCHAR(20) for comfort once PostgreSQL becomes the target, hence 32.
STATUS_LENGTH = 32

MEMBER_REFERRAL_STATUSES = (
    "NOT_COMPLETED",
    "PENDING_CONFIRMATION",
    "COMPLETED",
    "VERIFIED",
)

STORAGE_STATUSES = ("PENDING", "STORED", "STORAGE_FAILED")

DOCUMENT_TYPES = (
    "PROFILE_PHOTO",
    "CV",
    "DEGREE",
    "CERTIFICATE",
    "SUPPORTING",
    "PRIMARY_LEAVING_RESULT",
    "GRADE_9_12_TRANSCRIPT",
    "SECONDARY_LEAVING_RESULT",
    "UNIVERSITY_ENTRANCE_RESULT",
    "UNIVERSITY_TRANSCRIPT",
    "CURRENT_UNIVERSITY_DOCUMENT",
    "EXIT_EXAMINATION_RESULT",
    "BACHELORS_DEGREE",
    "MSC_DEGREE",
    "OTHER_CERTIFICATE",
    "ENGLISH_VOICE",
)

#: The separate 50-member group onboarding condition. Verified by an admin;
#: never read by the matching engine.
MEMBER_REFERRAL_NOT_SCORED_NOTE = (
    "member_referral_status is an onboarding condition only and must never "
    "contribute to a tutor match score"
)

STUDENT_REQUEST_STATUSES = ("NEW", "MATCHED", "CONTACTED", "CLOSED")

SUPPORT_TICKET_STATUSES = ("OPEN", "ANSWERED", "CLOSED")

SUPPORT_CATEGORIES = (
    "GENERAL_QUESTION",
    "TUTOR_APPLICATION",
    "FINDING_A_TUTOR",
    "PAYMENT",
    "TECHNICAL_PROBLEM",
    "TALK_TO_ADMIN",
)

JOB_POST_STATUSES = ("DRAFT", "PUBLISHED", "CLOSED")

COUNTRY_ETHIOPIA = "Ethiopia"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


# ---------------------------------------------------------------------------
# Tutor
# ---------------------------------------------------------------------------


class Tutor(Base):
    __tablename__ = "tutors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_tutor_id: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)

    telegram_storage_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    telegram_storage_message_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    name: Mapped[str] = mapped_column(String(160))
    display_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(40))
    email: Mapped[str] = mapped_column(String(160))
    country: Mapped[str] = mapped_column(String(80), index=True)
    city: Mapped[str] = mapped_column(String(120))
    bio: Mapped[str] = mapped_column(Text, default="")
    profile_photo_file_id: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )

    etb_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    usd_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    experience_years: Mapped[int] = mapped_column(Integer, default=0)
    timezone: Mapped[str] = mapped_column(String(80), default="UTC")

    teaching_mode: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(
        String(STATUS_LENGTH), index=True, default="PENDING"
    )
    storage_status: Mapped[str] = mapped_column(
        String(20), default="PENDING", index=True
    )

    # -- personal (Ethiopian onboarding) ---------------------------------
    # NOTE: the full legal name is the existing ``name`` column; there is
    # deliberately no separate ``full_name`` column, so the two can never drift.
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_address: Mapped[str | None] = mapped_column(String(240), nullable=True)

    # -- teaching capability --------------------------------------------
    teaching_experience_years: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    teaching_experience_description: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # -- English proficiency (self-rated 1-10 + optional voice evidence) ---
    english_proficiency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    english_voice_file_id: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    english_voice_message_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    # -- academic background ---------------------------------------------
    university: Mapped[str | None] = mapped_column(String(200), nullable=True)
    department: Mapped[str | None] = mapped_column(String(160), nullable=True)
    education_level: Mapped[str | None] = mapped_column(String(40), nullable=True)
    university_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cgpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    entrance_exam_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    entrance_exam_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entrance_exam_type: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    #: Denominator for score normalisation. Stored so scores from different
    #: examination systems are never compared raw.
    entrance_exam_max_score: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )

    # -- verification ----------------------------------------------------
    member_referral_status: Mapped[str] = mapped_column(
        String(STATUS_LENGTH),
        default="NOT_COMPLETED",
        server_default="NOT_COMPLETED",
        index=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    subjects: Mapped[list["TutorSubject"]] = relationship(
        back_populates="tutor", cascade="all, delete-orphan", lazy="selectin"
    )
    levels: Mapped[list["TutorLevel"]] = relationship(
        back_populates="tutor", cascade="all, delete-orphan", lazy="selectin"
    )
    grades: Mapped[list["TutorGrade"]] = relationship(
        back_populates="tutor", cascade="all, delete-orphan", lazy="selectin"
    )
    tutoring_locations: Mapped[list["TutorLocation"]] = relationship(
        back_populates="tutor", cascade="all, delete-orphan", lazy="selectin"
    )
    experiences: Mapped[list["TutorExperience"]] = relationship(
        back_populates="tutor", cascade="all, delete-orphan", lazy="selectin"
    )
    languages: Mapped[list["TutorLanguage"]] = relationship(
        back_populates="tutor", cascade="all, delete-orphan", lazy="selectin"
    )
    education: Mapped[list["TutorEducation"]] = relationship(
        back_populates="tutor", cascade="all, delete-orphan", lazy="selectin"
    )
    availability: Mapped[list["TutorAvailability"]] = relationship(
        back_populates="tutor", cascade="all, delete-orphan", lazy="selectin"
    )
    documents: Mapped[list["TutorDocument"]] = relationship(
        back_populates="tutor", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (Index("ix_tutors_status_country", "status", "country"),)

    # -- convenience ----------------------------------------------------
    @property
    def subject_list(self) -> list[str]:
        return [item.subject for item in self.subjects]

    @property
    def level_list(self) -> list[str]:
        return [item.level for item in self.levels]

    @property
    def language_list(self) -> list[str]:
        return [item.language for item in self.languages]

    @property
    def grade_list(self) -> list[str]:
        """Fine-grained grades (``Elementary``, ``Grade 4``, ``University``)."""
        return [item.grade for item in self.grades]

    @property
    def location_list(self) -> list[str]:
        return [item.location for item in self.tutoring_locations]

    @property
    def normalized_location_list(self) -> list[str]:
        return [
            item.normalized_location
            for item in self.tutoring_locations
            if item.normalized_location
        ]

    @property
    def is_verified(self) -> bool:
        return self.status == "VERIFIED"

    @property
    def has_english_voice(self) -> bool:
        """Whether the optional English voice intro was received."""
        return bool(self.english_voice_file_id)


class TutorSubject(Base):
    __tablename__ = "tutor_subjects"
    __table_args__ = (UniqueConstraint("tutor_id", "subject", name="uq_tutor_subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("tutors.id", ondelete="CASCADE"), index=True
    )
    subject: Mapped[str] = mapped_column(String(120), index=True)

    tutor: Mapped[Tutor] = relationship(back_populates="subjects")


class TutorLevel(Base):
    __tablename__ = "tutor_levels"
    __table_args__ = (UniqueConstraint("tutor_id", "level", name="uq_tutor_level"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("tutors.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[str] = mapped_column(String(80), index=True)

    tutor: Mapped[Tutor] = relationship(back_populates="levels")


class TutorGrade(Base):
    """A single fine-grained grade a tutor can teach.

    Deliberately separate from :class:`TutorLevel`, which keeps the coarse
    bands (``Grade 1-3`` …) the 1,000+ legacy tutors and the public API use.
    The matching engine reads this table first and falls back to
    :class:`TutorLevel` when a tutor has no rows here.
    """

    __tablename__ = "tutor_grades"
    __table_args__ = (UniqueConstraint("tutor_id", "grade", name="uq_tutor_grade"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("tutors.id", ondelete="CASCADE"), index=True
    )
    grade: Mapped[str] = mapped_column(String(80), index=True)

    tutor: Mapped[Tutor] = relationship(back_populates="grades")


class TutorLocation(Base):
    """One area a tutor is available to teach in.

    ``normalized_location`` holds the canonical key produced by
    :mod:`app.services.location_service`, so ``Ayat_Tsebel``,
    ``Ayat Tsebel`` and ``Ayat Tsebel, Addis Ababa`` all compare equal. A
    tutor may hold several rows; location is a scoring signal, never a filter.
    """

    __tablename__ = "tutor_locations"
    __table_args__ = (
        UniqueConstraint("tutor_id", "location", name="uq_tutor_location"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("tutors.id", ondelete="CASCADE"), index=True
    )
    location: Mapped[str] = mapped_column(String(120), index=True)
    normalized_location: Mapped[str] = mapped_column(String(120), index=True)

    tutor: Mapped[Tutor] = relationship(back_populates="tutoring_locations")


class TutorExperience(Base):
    """A prior teaching role, used for experience *relevance* scoring.

    Subject/grade relevance matters more than total years, so the subjects and
    grades a role covered are stored explicitly rather than inferred from the
    tutor-level totals.
    """

    __tablename__ = "tutor_experiences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("tutors.id", ondelete="CASCADE"), index=True
    )
    years: Mapped[float] = mapped_column(Float, default=0.0)
    description: Mapped[str] = mapped_column(Text, default="")
    subjects: Mapped[str] = mapped_column(String(240), default="")
    grades: Mapped[str] = mapped_column(String(160), default="")
    institutions: Mapped[str] = mapped_column(String(240), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    tutor: Mapped[Tutor] = relationship(back_populates="experiences")


class TutorLanguage(Base):
    __tablename__ = "tutor_languages"
    __table_args__ = (
        UniqueConstraint("tutor_id", "language", name="uq_tutor_language"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("tutors.id", ondelete="CASCADE"), index=True
    )
    language: Mapped[str] = mapped_column(String(80), index=True)

    tutor: Mapped[Tutor] = relationship(back_populates="languages")


class TutorEducation(Base):
    __tablename__ = "tutor_education"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("tutors.id", ondelete="CASCADE"), index=True
    )
    institution: Mapped[str] = mapped_column(String(200))
    degree: Mapped[str] = mapped_column(String(120))
    field: Mapped[str] = mapped_column(String(160))
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tutor: Mapped[Tutor] = relationship(back_populates="education")


class TutorAvailability(Base):
    __tablename__ = "tutor_availability"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("tutors.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[str] = mapped_column(String(40))
    start_time: Mapped[str] = mapped_column(String(10))
    end_time: Mapped[str] = mapped_column(String(10))
    timezone: Mapped[str] = mapped_column(String(80), default="UTC")

    tutor: Mapped[Tutor] = relationship(back_populates="availability")


class TutorDocument(Base):
    __tablename__ = "tutor_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("tutors.id", ondelete="CASCADE"), index=True
    )
    document_type: Mapped[str] = mapped_column(String(40), index=True)
    telegram_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    telegram_file_unique_id: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    tutor: Mapped[Tutor] = relationship(back_populates="documents")


# ---------------------------------------------------------------------------
# Idempotency counter (TDR sequence)
# ---------------------------------------------------------------------------


class IdCounter(Base):
    """Atomic sequential counter backing the TDR identifiers."""

    __tablename__ = "id_counters"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------------------
# Student requests
# ---------------------------------------------------------------------------


class StudentRequest(Base):
    __tablename__ = "student_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_request_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)

    parent_name: Mapped[str] = mapped_column(String(160))
    student_name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(40), default="")
    contact_handle: Mapped[str] = mapped_column(String(120), default="")

    country: Mapped[str] = mapped_column(String(80), index=True)
    education_level: Mapped[str] = mapped_column(String(80), index=True)
    subject: Mapped[str] = mapped_column(String(120), index=True)
    preferred_language: Mapped[str] = mapped_column(String(80), default="")
    mode: Mapped[str] = mapped_column(String(20), index=True)
    schedule: Mapped[str] = mapped_column(String(160), default="")
    additional_requirements: Mapped[str] = mapped_column(Text, default="")

    budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    etb_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    usd_budget: Mapped[float | None] = mapped_column(Float, nullable=True)

    matched_tutor_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    telegram_request_chat_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    telegram_request_message_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    storage_status: Mapped[str] = mapped_column(String(20), default="PENDING")

    status: Mapped[str] = mapped_column(
        String(20), index=True, default="NEW"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------------------
# Support
# ---------------------------------------------------------------------------


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_ticket_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), index=True, default="OPEN")
    admin_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    admin_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    admin_chat_username: Mapped[str] = mapped_column(String(120), default="")
    telegram_user_fullname: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------------------
# Job posts (Tutor Tutors job-post generator)
# ---------------------------------------------------------------------------


class JobPost(Base):
    """A generated tutor job post.

    Only the seven collected variables are stored. There is deliberately no
    ``requirements`` column: the requirements block is static template content
    (see :mod:`app.templates.tutor_job_post`) and is persisted only as part of
    ``generated_text``.
    """

    __tablename__ = "job_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_post_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)

    location: Mapped[str] = mapped_column(String(120), index=True)
    student_level: Mapped[str] = mapped_column(String(120), default="")
    tutor_category: Mapped[str] = mapped_column(String(120), default="")
    duration: Mapped[str] = mapped_column(String(120), default="")
    frequency: Mapped[str] = mapped_column(String(120), default="")
    subjects: Mapped[str] = mapped_column(String(160), default="")
    compensation: Mapped[str] = mapped_column(String(160), default="")

    # -- structured matching profile -------------------------------------
    # These are derived from the seven collected variables when the post is
    # published; they are never asked as extra questions, so the ``/post`` flow
    # and the static template are untouched.
    # ``server_default`` mirrors the Python ``default`` on purpose: it is what
    # lets the additive migration add these columns to an already-populated
    # table, where NOT NULL without a server default would fail.
    teaching_mode: Mapped[str] = mapped_column(
        String(20), default="IN_PERSON", server_default="IN_PERSON"
    )
    english_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql.false()
    )
    english_level_required: Mapped[str] = mapped_column(
        String(20), default="STANDARD", server_default="STANDARD"
    )
    special_requirements: Mapped[str] = mapped_column(
        Text, default="", server_default=""
    )

    generated_text: Mapped[str] = mapped_column(Text, default="")

    telegram_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[str] = mapped_column(String(20), index=True, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    @property
    def is_published(self) -> bool:
        return self.status == "PUBLISHED" and self.published_at is not None

    @property
    def job_id(self) -> str:
        """The public job reference (``JOB-000001``).

        The collected field set stays the seven template variables, so this is
        an alias for :attr:`public_post_id` rather than a second column.
        """
        return self.public_post_id

    @property
    def is_open(self) -> bool:
        """Open for candidate recommendations."""
        return self.status in ("DRAFT", "PUBLISHED")


# ---------------------------------------------------------------------------
# Job candidates (tutor-job matching)
# ---------------------------------------------------------------------------


class JobCandidate(Base):
    """One recommended tutor for one published job.

    Persisted rather than recomputed on demand so the admin's shortlist, the
    contact attempts and the eventual selection survive a re-match. A tutor is
    never assigned automatically: the admin moves this row to ``SELECTED``.
    """

    __tablename__ = "job_candidates"
    __table_args__ = (
        UniqueConstraint("job_post_id", "tutor_id", name="uq_job_candidate"),
        Index("ix_job_candidates_post_rank", "job_post_id", "rank"),
        Index("ix_job_candidates_post_status", "job_post_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_post_id: Mapped[int] = mapped_column(
        ForeignKey("job_posts.id", ondelete="CASCADE"), index=True
    )
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("tutors.id", ondelete="CASCADE"), index=True
    )

    #: 0-100, stored as a rounded whole number.
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), index=True, default="RECOMMENDED")
    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    #: The serialised MatchResult at the time of matching. Stored so the admin
    #: sees the exact strengths/weaknesses of the recommendation that was made,
    #: not whatever the data happens to say on a later re-match.
    details: Mapped[str] = mapped_column(Text, default="", server_default="")

    job_post: Mapped[JobPost] = relationship()
    tutor: Mapped[Tutor] = relationship()

    @property
    def public_tutor_id(self) -> str:
        return self.tutor.public_tutor_id