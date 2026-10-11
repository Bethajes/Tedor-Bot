"""SQLAlchemy ORM models.

Requirement 12.1: every database interaction goes through the ORM so the engine
can be swapped (SQLite -> PostgreSQL) by changing the connection string only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

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

# ---------------------------------------------------------------------------
# Location vocabulary
# ---------------------------------------------------------------------------
#
# Stored as plain strings for the same reason every other status is: the ORM
# never uses a native enum type, so widening or adding a member never needs a
# table rebuild on a populated database.

#: Where a location record came from. ``GEOAPIFY`` rows carry a provider place
#: id and may be trusted as a real geographic result; ``MANUAL`` rows are typed
#: by a user and are never presented as verified until an admin approves them;
#: ``REGISTRY`` rows are the curated set in ``app.services.location_service``
#: seeded into the table so existing free-text locations resolve to a row;
#: ``CHANNEL`` rows are synchronised from the curated Telegram location channel
#: and keep their ``location_code`` plus the message they came from so an edit
#: in the channel updates exactly one row here.
LOCATION_PROVIDERS = ("GEOAPIFY", "MANUAL", "REGISTRY", "CHANNEL")

#: Provider value for rows imported from the Telegram location channel.
LOCATION_PROVIDER_CHANNEL: Final[str] = "CHANNEL"

#: Admin-owned review state. A user-facing request may only ever *create* a
#: ``PENDING`` location — nothing in the bot or API writes ``VERIFIED``.
LOCATION_VERIFICATION_STATUSES = ("PENDING", "VERIFIED", "REJECTED")

#: How a tutor's tutoring-location row was added. ``HOME_ADDRESS`` exists so an
#: admin can tell a location the tutor explicitly confirmed after being asked
#: about their home area from one they typed into the selector.
TUTOR_LOCATION_SOURCES = ("SEARCH", "MANUAL", "HOME_ADDRESS", "IMPORTED")

COUNTRY_ETHIOPIA_DEFAULT_CITY = "Addis Ababa"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


class Location(Base):
    """One canonical tutoring location.

    A location is shared: many tutors and many jobs point at the same row, so
    a place is entered once and reused. Two rules keep that honest:

    * A ``GEOAPIFY`` row is a real geographic result and may carry coordinates.
      A ``MANUAL`` row is something a person typed, so it starts ``PENDING``
      with null coordinates and is never described as verified until an admin
      says so.
    * Coordinates and a provider place id are never inferred. A manual row with
      no coordinates simply has no coordinates, and proximity scoring reports
      that as uncertain rather than guessing.

    ``(normalized_name, city)`` is unique so the same place cannot be created
    twice from two different spellings; ``(provider, provider_place_id)`` is
    unique so the same geocoder result is stored once. Both are enforced by the
    database, not only by the service layer.

    ``location_code`` is the stable public identifier an administrator types in
    the channel (``AA-0001``). It is unique, so re-posting a record can never
    fork it into two rows, and it survives a rename of ``name``.
    """

    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("normalized_name", "city", name="uq_locations_name_city"),
        UniqueConstraint("provider", "provider_place_id", name="uq_locations_provider_place"),
        Index("uq_locations_location_code", "location_code", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # -- naming ---------------------------------------------------------
    name: Mapped[str] = mapped_column(String(160))
    #: Search key produced by ``location_service.normalize_location``; every
    #: spelling the platform has ever seen folds onto this one value.
    normalized_name: Mapped[str] = mapped_column(String(160), index=True)
    city: Mapped[str] = mapped_column(String(120))
    sub_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    landmark: Mapped[str | None] = mapped_column(String(240), nullable=True)
    #: Woreda (sub-city district) as published by the administrator. Optional
    #: and free text: a channel record may simply not carry one.
    woreda: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # -- coordinates ----------------------------------------------------
    #: Null means "we do not know", never "somewhere near here". Proximity
    #: scoring treats a null pair as uncertain instead of inventing a distance.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # -- provenance -----------------------------------------------------
    provider: Mapped[str] = mapped_column(String(20), index=True)
    #: The geocoder's own identifier. Deliberately *not* interchangeable with
    #: a manually created location: a manual row has none, and two providers
    #: issuing the same id are two different places.
    provider_place_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(20), index=True, default="PENDING", server_default="PENDING"
    )
    #: Stable public identifier assigned by the administrator, e.g. ``AA-0001``.
    #: Unique across the table. ``None`` for every row the channel did not
    #: create, which is why the unique index tolerates nulls.
    location_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Telegram provenance for a ``CHANNEL`` row. ``source_chat_id`` scopes the
    #: message id, because message ids are only unique inside one chat, and
    #: ``source_message_id`` is what makes a re-sync idempotent and lets an
    #: edited channel post update exactly one row.
    source_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_message_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    #: When this row was last written by a channel synchronisation. ``None``
    #: means it was never synchronised, so "no sync ran" and "synced and
    #: unchanged" stay distinguishable.
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Deactivation, kept separate from ``verification_status`` because the two
    #: answer different questions. A record can be perfectly verified and still
    #: be withdrawn from the directory (its channel post was deleted, or an
    #: admin marked it ``Status: disabled``).
    #:
    #: Deactivation is what a sync does instead of deleting: a ``Location`` row
    #: may already be referenced by a tutor's tutoring areas and by a published
    #: job, and dropping it would silently rewrite history for both. A
    #: deactivated row keeps its name, coordinates and references so those
    #: stay explainable; it simply stops being offered in search and stops
    #: being verifiable for new selections.
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql.true(), index=True
    )
    #: Set when an admin merges this row into a canonical one. The row is kept
    #: as a tombstone so historical references stay explainable.
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    aliases: Mapped[list["LocationAlias"]] = relationship(
        back_populates="location", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def has_coordinates(self) -> bool:
        """Whether a distance between this place and another can be computed."""
        return self.latitude is not None and self.longitude is not None

    @property
    def is_verified(self) -> bool:
        return self.verification_status == "VERIFIED"

    @property
    def is_rejected(self) -> bool:
        return self.verification_status == "REJECTED"

    @property
    def is_merged(self) -> bool:
        return self.merged_into_id is not None

    @property
    def display(self) -> str:
        """The label shown wherever a location is listed."""
        return self.name


class LocationAlias(Base):
    """A verified alternative spelling that resolves to a location.

    An admin adds these, so an alias is stronger evidence than the fuzzy
    normalisation of raw user text: a match here is treated as the same place,
    not merely similar to it.
    """

    __tablename__ = "location_aliases"
    __table_args__ = (
        UniqueConstraint("normalized_alias", name="uq_location_alias_normalized"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location_id: Mapped[int] = mapped_column(
        ForeignKey("locations.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(160))
    normalized_alias: Mapped[str] = mapped_column(String(160), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    location: Mapped[Location] = relationship(back_populates="aliases")


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
    #: Preparatory stream the tutor studied (``NATURAL`` / ``SOCIAL``). The
    #: Ethiopian preparatory stream determines which subjects a tutor focused on,
    #: so it is stored with the academic record rather than the grade list.
    academic_stream: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # -- secondary school results (ranking signals) ----------------------
    #: Average marks out of 100 for the Grade 8 national ("ministry") exam and
    #: for each secondary year. They are the evidence that actually speaks to a
    #: tutor's own result in the subjects and levels they will teach, so they
    #: feed the academic ranking signal. Every one is optional: a tutor who did
    #: not sit an exam simply leaves it null and it contributes nothing, which is
    #: why none of these is required at submission.
    grade8_exam_average: Mapped[float | None] = mapped_column(Float, nullable=True)
    grade9_average: Mapped[float | None] = mapped_column(Float, nullable=True)
    grade10_average: Mapped[float | None] = mapped_column(Float, nullable=True)
    grade11_average: Mapped[float | None] = mapped_column(Float, nullable=True)
    grade12_average: Mapped[float | None] = mapped_column(Float, nullable=True)

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

    ``location_id`` links the row to the shared :class:`Location` when one
    exists. It is nullable on purpose: an imported tutor's free text may name
    nothing the registry knows, and an unresolved row is more honest than a
    forced link. ``location`` and ``normalized_location`` are kept exactly as
    the tutor typed them for display and audit.

    ``is_confirmed`` records that the tutor explicitly said they will teach
    there. Rows backfilled from the legacy text column default to confirmed,
    because the tutor typed it into the "areas you can tutor in" answer; a
    location proposed from a home address is only ever written after the tutor
    answers yes.

    Two uniqueness rules apply, because a tutor may name the same place twice
    with different spellings:

    * ``uq_tutor_location`` on the free-text pair, which stops the same
      spelling being stored twice.
    * ``uq_tutor_location_canonical`` on ``(tutor_id, location_id)``, which
      stops "Bole" and "bole" becoming two rows once both resolve to the same
      :class:`Location`. ``location_id`` is nullable and every SQL dialect
      allows repeated nulls in a unique index, so unresolved legacy rows are
      unaffected.
    """

    __tablename__ = "tutor_locations"
    __table_args__ = (
        UniqueConstraint("tutor_id", "location", name="uq_tutor_location"),
        Index("uq_tutor_location_canonical", "tutor_id", "location_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("tutors.id", ondelete="CASCADE"), index=True
    )
    location: Mapped[str] = mapped_column(String(120), index=True)
    normalized_location: Mapped[str] = mapped_column(String(120), index=True)

    #: Canonical location, when this row has been resolved to one.
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Did the tutor explicitly confirm they will teach in person here?
    is_confirmed: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql.true()
    )
    #: One of :data:`TUTOR_LOCATION_SOURCES`.
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    location_record: Mapped[Location | None] = relationship()

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
    # Widened from String(80): a request may now name several grades, stored
    # comma-joined. Sized so even selecting every option cannot be truncated.
    education_level: Mapped[str] = mapped_column(String(280), index=True)
    subject: Mapped[str] = mapped_column(String(120), index=True)
    preferred_language: Mapped[str] = mapped_column(String(80), default="")
    mode: Mapped[str] = mapped_column(String(20), index=True)
    #: Where the student wants to be taught, as the parent typed or selected it.
    #: Nullable because the parent flow asks for it only when it matters, and
    #: online requests have no place at all.
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
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
    #: The canonical location this text resolved to, when one was selected.
    #: ``location`` above is never overwritten: the post template renders it as
    #: a hashtag and an admin needs to see what the poster actually wrote.
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # May hold several grades, comma-joined, so it is sized for every option.
    student_level: Mapped[str] = mapped_column(String(200), default="")
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


# ---------------------------------------------------------------------------
# Job applications (tutor-initiated "Apply for This Job")
# ---------------------------------------------------------------------------


class JobApplication(Base):
    """One tutor's interest in, and submission of candidacy for, one job.

    Distinct from :class:`JobCandidate`, which is the *admin's* shortlist of
    tutors the matching engine recommended. This row records the tutor's own
    intent, so the two can disagree for a while: a tutor may apply to a job they
    were never recommended for, and an admin may shortlist a candidate who never
    applied.

    The unique ``(job_post_id, tutor_id)`` constraint is the duplicate guard:
    pressing Apply repeatedly, or retrying after a failed request, can never
    produce a second row.
    """

    __tablename__ = "job_applications"
    __table_args__ = (
        UniqueConstraint("job_post_id", "tutor_id", name="uq_job_application"),
        Index("ix_job_applications_post_status", "job_post_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_post_id: Mapped[int] = mapped_column(
        ForeignKey("job_posts.id", ondelete="CASCADE"), index=True
    )
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("tutors.id", ondelete="CASCADE"), index=True
    )

    #: The tutor-driven lifecycle (see :class:`app.enums.JobApplicationStatus`).
    status: Mapped[str] = mapped_column(String(20), index=True, default="STARTED")

    #: True only once the tutor explicitly confirmed "Continue Application".
    #: Clicking Apply expresses interest; this flag is the proof of submission.
    confirmed_submitted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql.false()
    )

    #: 0-100, from :meth:`app.services.matching_service.MatchingService.evaluate_tutor`.
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    match_rank: Mapped[int] = mapped_column(Integer, default=0)
    #: The serialised MatchResult at evaluation time, so the admin sees the
    #: exact explanation the tutor was shown rather than a later re-score.
    match_details: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: The short "why this may suit you" text the tutor was shown.
    recommendation: Mapped[str] = mapped_column(Text, default="", server_default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    job_post: Mapped[JobPost] = relationship()
    tutor: Mapped[Tutor] = relationship()

    @property
    def public_tutor_id(self) -> str:
        return self.tutor.public_tutor_id

    @property
    def job_id(self) -> str:
        return self.job_post.public_post_id


# ---------------------------------------------------------------------------
# Mini App drafts
# ---------------------------------------------------------------------------


#: The two draft types the Mini App persists. Stored as a plain string so
#: adding a new type never requires a schema migration.
DRAFT_TYPES = ("tutor", "parent")


class Draft(Base):
    """A partially-completed tutor application or parent request.

    One row per (telegram_user_id, draft_type) pair — the UNIQUE constraint
    makes every upsert idempotent. ``payload`` is a JSON blob; the service
    layer is responsible for serialising/deserialising it.
    """

    __tablename__ = "drafts"
    __table_args__ = (
        UniqueConstraint(
            "telegram_user_id", "draft_type", name="uq_draft_user_type"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    draft_type: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

class WebLoginCode(Base):
    """A one-time code for signing in to the Mini App from a plain browser.

    Only created for a phone number that already belongs to a tutor or a parent
    request, and consumed on first use. The code is stored hashed: a database
    leak must not hand out working logins, so reading a row is never enough to
    authenticate with it.
    """

    __tablename__ = "web_login_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: SHA-256 of the code, hex encoded. Never the code itself.
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Who the code was issued to. Null until a matching application is found.
    telegram_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PendingDocument(Base):
    """A document uploaded before the tutor application exists.

    The Mini App asks for academic documents as the *second to last* step, so
    they are uploaded before the ``Tutor`` row that will own them is created.
    They are staged here against the authenticated Telegram user and promoted
    to :class:`TutorDocument` the moment the application is submitted.

    Without this, a first-time applicant's upload is refused for the record
    not existing yet, and the form cannot be finished at all.
    """

    __tablename__ = "pending_documents"
    __table_args__ = (
        UniqueConstraint(
            "telegram_user_id", "document_type", name="uq_pending_document_user_type"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    telegram_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    telegram_file_unique_id: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
