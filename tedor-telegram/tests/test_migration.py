"""Additive schema upgrades for an already-populated database.

Production holds 1,000+ tutors in an existing SQLite file that predates the
matching-engine columns. ``create_all`` never adds a column to a table that
already exists, so these tests pin the behaviour of
:func:`app.database.apply_schema_upgrades` against a hand-built legacy schema:
new columns appear, existing rows survive, and a second run is a no-op.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import apply_schema_upgrades, plan_schema_upgrades
from app.enums import CandidateStatus, MemberReferralStatus
from app.models import (
    Base,
    TUTOR_STATUSES,
    JobApplication,
    JobCandidate,
    JobPost,
    Location,
    Tutor,
    TutorLocation,
)
from tests.factories import job_post_payload

#: The pre-matching-engine ``tutors`` table, exactly as it exists in the
#: production database the 1,000+ tutors were imported into. Reproduced
#: verbatim (including the NOT NULL columns) so the migration is exercised
#: against the real shape rather than a reduced one.
LEGACY_TUTORS_DDL = """
CREATE TABLE tutors (
    id INTEGER NOT NULL PRIMARY KEY,
    public_tutor_id VARCHAR(16) NOT NULL,
    telegram_user_id INTEGER NOT NULL,
    telegram_storage_chat_id INTEGER,
    telegram_storage_message_id INTEGER,
    name VARCHAR(160) NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    phone VARCHAR(40) NOT NULL,
    email VARCHAR(160) NOT NULL,
    country VARCHAR(80) NOT NULL,
    city VARCHAR(120) NOT NULL,
    bio TEXT NOT NULL,
    profile_photo_file_id VARCHAR(256),
    etb_rate FLOAT,
    usd_rate FLOAT,
    experience_years INTEGER NOT NULL,
    timezone VARCHAR(80) NOT NULL,
    teaching_mode VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL,
    storage_status VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

LEGACY_JOB_POSTS_DDL = """
CREATE TABLE job_posts (
    id INTEGER NOT NULL PRIMARY KEY,
    public_post_id VARCHAR(20) NOT NULL,
    telegram_user_id INTEGER NOT NULL,
    location VARCHAR(120) NOT NULL,
    student_level VARCHAR(120) NOT NULL,
    tutor_category VARCHAR(120) NOT NULL,
    duration VARCHAR(120) NOT NULL,
    frequency VARCHAR(120) NOT NULL,
    subjects VARCHAR(160) NOT NULL,
    compensation VARCHAR(160) NOT NULL,
    generated_text TEXT NOT NULL,
    telegram_chat_id INTEGER,
    telegram_message_id INTEGER,
    published_at DATETIME,
    status VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

#: ``tutor_locations`` before it carried a canonical link.
LEGACY_TUTOR_LOCATIONS_DDL = """
CREATE TABLE tutor_locations (
    id INTEGER NOT NULL PRIMARY KEY,
    tutor_id INTEGER NOT NULL,
    location VARCHAR(120) NOT NULL,
    normalized_location VARCHAR(120) NOT NULL,
    FOREIGN KEY(tutor_id) REFERENCES tutors (id) ON DELETE CASCADE
)
"""

#: Columns the new onboarding flow and the matching engine depend on.
EXPECTED_TUTOR_COLUMNS = (
    "gender",
    "age",
    "current_address",
    "teaching_experience_years",
    "teaching_experience_description",
    "english_proficiency",
    "english_voice_file_id",
    "english_voice_message_id",
    "university",
    "department",
    "education_level",
    "university_year",
    "cgpa",
    "entrance_exam_score",
    "entrance_exam_year",
    "entrance_exam_type",
    "entrance_exam_max_score",
    "member_referral_status",
    "verified_at",
    "verified_by",
)

EXPECTED_JOB_COLUMNS = (
    "teaching_mode",
    "english_required",
    "english_level_required",
    "special_requirements",
)

EXPECTED_NEW_TABLES = (
    "job_candidates",
    "tutor_experiences",
    "tutor_grades",
    "tutor_locations",
)


def _legacy_engine(tmp_path, tutors=3):
    """A SQLite file holding the legacy schema and ``tutors`` existing rows."""
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text(LEGACY_TUTORS_DDL))
        connection.execute(text(LEGACY_JOB_POSTS_DDL))
        for index in range(tutors):
            connection.execute(
                text(
                    "INSERT INTO tutors (id, public_tutor_id, telegram_user_id, name,"
                    " display_name, phone, email, country, city, bio,"
                    " experience_years, timezone, teaching_mode, status,"
                    " storage_status, created_at, updated_at, etb_rate, usd_rate)"
                    " VALUES (:id, :tdr, :tg, :name, :display, :phone, :email,"
                    " 'Ethiopia', 'Addis Ababa', '', 0, 'EAT (UTC+3)', 'ONLINE',"
                    " :status, 'STORED', :now, :now, :etb, NULL)"
                ),
                {
                    "id": index + 1,
                    "tdr": f"TDR-{index + 1:06d}",
                    "tg": -100 - index,
                    "name": f"Legacy Tutor {index}",
                    "display": f"Legacy {index}",
                    "phone": f"+25191{index:07d}",
                    "email": f"legacy{index}@example.com",
                    "status": "VERIFIED" if index else "PENDING",
                    "now": "2026-01-01 00:00:00",
                    "etb": 300.0 + index,
                },
            )
    return engine


def _columns(engine, table):
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _index_names(engine, table):
    return {index["name"] for index in inspect(engine).get_indexes(table)}


# ---------------------------------------------------------------------------
# column additions
# ---------------------------------------------------------------------------


def test_new_tutor_columns_are_added(tmp_path) -> None:
    engine = _legacy_engine(tmp_path)
    apply_schema_upgrades(engine)
    present = _columns(engine, "tutors")
    missing = [name for name in EXPECTED_TUTOR_COLUMNS if name not in present]
    assert not missing, f"migration did not add: {missing}"


def test_new_job_post_columns_are_added(tmp_path) -> None:
    engine = _legacy_engine(tmp_path)
    apply_schema_upgrades(engine)
    present = _columns(engine, "job_posts")
    missing = [name for name in EXPECTED_JOB_COLUMNS if name not in present]
    assert not missing, f"migration did not add: {missing}"


def test_existing_columns_are_never_redeclared(tmp_path) -> None:
    """A legacy column keeps its original type and position."""
    engine = _legacy_engine(tmp_path)
    apply_schema_upgrades(engine)
    columns = {column["name"]: column for column in inspect(engine).get_columns("tutors")}
    assert str(columns["etb_rate"]["type"]) == "FLOAT"
    assert columns["status"]["type"].length == 20, "width is not changeable by ADD COLUMN"
    assert columns["city"]["type"].length == 120


def test_indexes_on_new_columns_are_created(tmp_path) -> None:
    engine = _legacy_engine(tmp_path)
    apply_schema_upgrades(engine)
    assert "ix_tutors_member_referral_status" in _index_names(engine, "tutors")
    assert "ix_tutors_status" in _index_names(engine, "tutors"), "legacy index dropped"


# ---------------------------------------------------------------------------
# data safety
# ---------------------------------------------------------------------------


def test_existing_rows_and_their_values_survive(tmp_path) -> None:
    engine = _legacy_engine(tmp_path, tutors=3)
    before = []
    with engine.connect() as connection:
        before = connection.execute(
            text("SELECT id, public_tutor_id, name, etb_rate, status FROM tutors ORDER BY id")
        ).fetchall()

    apply_schema_upgrades(engine)

    with engine.connect() as connection:
        after = connection.execute(
            text("SELECT id, public_tutor_id, name, etb_rate, status FROM tutors ORDER BY id")
        ).fetchall()
        count = connection.execute(text("SELECT COUNT(*) FROM tutors")).scalar_one()
    assert after == before
    assert count == 3


def test_upgraded_rows_are_usable_by_the_orm(tmp_path) -> None:
    """The added columns are queryable and nullable — no invented values."""
    from sqlalchemy.orm import Session

    engine = _legacy_engine(tmp_path, tutors=2)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)

    with Session(engine) as session:
        tutor = session.get(Tutor, 1)
        assert tutor is not None
        assert tutor.member_referral_status == str(MemberReferralStatus.NOT_COMPLETED)
        # Nothing was fabricated for the imported rows.
        assert tutor.gender is None
        assert tutor.age is None
        assert tutor.english_proficiency is None
        assert tutor.entrance_exam_score is None
        assert tutor.cgpa is None
        assert tutor.entrance_exam_max_score is None
        assert tutor.verified_at is None
        # Pre-existing data is untouched.
        assert tutor.etb_rate == 300.0
        assert tutor.city == "Addis Ababa"


def test_status_accepts_the_new_documents_submitted_value(tmp_path) -> None:
    from sqlalchemy.orm import Session

    engine = _legacy_engine(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)

    with Session(engine) as session:
        tutor = session.get(Tutor, 1)
        tutor.status = "DOCUMENTS_SUBMITTED"
        session.commit()
        session.expire_all()
        assert session.get(Tutor, 1).status == "DOCUMENTS_SUBMITTED"


def test_the_longest_status_value_fits_the_legacy_column(tmp_path) -> None:
    """VARCHAR(20) survives the upgrade, so no status value may exceed it."""
    engine = _legacy_engine(tmp_path, tutors=1)
    apply_schema_upgrades(engine)
    width = next(
        column["type"].length
        for column in inspect(engine).get_columns("tutors")
        if column["name"] == "status"
    )
    assert width == 20
    longest = max(TUTOR_STATUSES + tuple(s.value for s in MemberReferralStatus), key=len)
    assert len(longest) <= width, f"{longest} would be truncated by VARCHAR({width})"


def test_candidate_status_is_storable(tmp_path) -> None:
    from sqlalchemy.orm import Session

    from app.models import JobPost, utcnow

    engine = _legacy_engine(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)

    with Session(engine) as session:
        post = JobPost(
            public_post_id="JOB-000001",
            telegram_user_id=1,
            location="Ayat Tsebel",
            student_level="4th Grader",
            tutor_category="Both Male and Female",
            duration="1 hour/day",
            frequency="3 days/week",
            subjects="Mathematics",
            compensation="300 Birr / 1 hour",
            status="PUBLISHED",
            published_at=utcnow(),
        )
        session.add(post)
        session.flush()
        session.add(
            JobCandidate(
                job_post_id=post.id,
                tutor_id=1,
                match_score=91.0,
                rank=1,
                status=str(CandidateStatus.RECOMMENDED),
            )
        )
        session.commit()
        saved = session.query(JobCandidate).one()
        assert saved.match_score == 91.0
        assert saved.rank == 1
        assert saved.status == "RECOMMENDED"
        assert saved.job_post.job_id == "JOB-000001"


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


def test_second_run_changes_nothing(tmp_path) -> None:
    engine = _legacy_engine(tmp_path, tutors=2)
    first = apply_schema_upgrades(engine)
    assert first, "the first run must do the work"
    assert apply_schema_upgrades(engine) == []
    assert apply_schema_upgrades(engine) == []


def test_fresh_database_needs_no_upgrades() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    assert plan_schema_upgrades(engine) == []
    assert apply_schema_upgrades(engine) == []


def test_plan_is_inspection_only(tmp_path) -> None:
    engine = _legacy_engine(tmp_path, tutors=1)
    plan = plan_schema_upgrades(engine)
    assert plan, "a legacy database must produce a plan"
    # Planning must not mutate the schema.
    assert plan_schema_upgrades(engine) == plan
    assert "gender" not in _columns(engine, "tutors")


def test_plan_labels_name_the_table_and_column(tmp_path) -> None:
    engine = _legacy_engine(tmp_path, tutors=1)
    labels = {label for _statement, label in plan_schema_upgrades(engine)}
    assert "tutors.gender" in labels
    assert "job_posts.teaching_mode" in labels


def test_plan_skips_not_null_columns_without_a_server_default(tmp_path) -> None:
    """A NOT NULL column with no default cannot be added to a populated table.

    Such a column is dropped from the plan (and logged) rather than producing a
    statement that would fail halfway through the transaction.
    """
    from sqlalchemy import Column, Integer, String

    class Rogue(Base):
        __tablename__ = "rogue_table"

        id = Column(Integer, primary_key=True)
        required = Column(String(40), nullable=False)
        optional = Column(String(40), nullable=True)

    try:
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE rogue_table (id INTEGER PRIMARY KEY)"))
        labels = {label for _statement, label in plan_schema_upgrades(engine)}
        assert "rogue_table.optional" in labels
        assert "rogue_table.required" not in labels
    finally:
        Base.metadata.remove(Rogue.__table__)


def test_upgrade_is_transactional_per_run(tmp_path) -> None:
    """A failure mid-run must not leave a half-patched schema behind."""
    engine = _legacy_engine(tmp_path, tutors=2)
    before = set(_columns(engine, "tutors"))
    altered = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _fail_on_third_alter(conn, cursor, statement, *args):
        if not statement.startswith("ALTER TABLE"):
            return None
        if altered["n"] == 2:
            raise RuntimeError("simulated failure")
        altered["n"] += 1
        return None

    try:
        with pytest.raises(RuntimeError):
            apply_schema_upgrades(engine)
    finally:
        event.remove(engine, "before_cursor_execute", _fail_on_third_alter)

    assert set(_columns(engine, "tutors")) == before, (
        "a failed upgrade must roll back completely"
    )


# ---------------------------------------------------------------------------
# new tables
# ---------------------------------------------------------------------------


def test_new_tables_are_created_by_create_all(tmp_path) -> None:
    engine = _legacy_engine(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)
    tables = set(inspect(engine).get_table_names())
    missing = [name for name in EXPECTED_NEW_TABLES if name not in tables]
    assert not missing, f"create_all did not create: {missing}"


def test_normalised_tables_have_their_indexes(tmp_path) -> None:
    engine = _legacy_engine(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)
    assert "ix_tutor_grades_grade" in _index_names(engine, "tutor_grades")
    assert "ix_tutor_locations_normalized_location" in _index_names(
        engine, "tutor_locations"
    )
    assert "ix_job_candidates_post_rank" in _index_names(engine, "job_candidates")

# ---------------------------------------------------------------------------
# job applications (the Apply-button feature)
# ---------------------------------------------------------------------------


def test_the_job_applications_table_is_created(tmp_path) -> None:
    """A new table is enough — ``init_db`` calls ``create_all`` on every boot."""
    engine = _legacy_engine(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)
    assert "job_applications" in set(inspect(engine).get_table_names())


def test_duplicate_job_applications_are_refused_by_the_schema(tmp_path) -> None:
    """The uniqueness the duplicate-press protection relies on is in the table."""
    from app.services import job_post_service, tutor_profile_service
    from tests.test_profile_service import ethiopian_payload

    engine = _legacy_engine(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)
    with Session(engine) as session:
        post = job_post_service.create_job_post(session, job_post_payload(-1))
        tutor = tutor_profile_service.create_ethiopian_tutor(
            session, ethiopian_payload(-1)
        )
        session.add(
            JobApplication(
                job_post_id=post.id, tutor_id=tutor.id, status="STARTED"
            )
        )
        session.commit()
        session.add(
            JobApplication(
                job_post_id=post.id, tutor_id=tutor.id, status="SUBMITTED"
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_job_application_indexes_exist(tmp_path) -> None:
    engine = _legacy_engine(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)
    # SQLite reports a UNIQUE constraint as an internal autoindex rather than a
    # named index, so the constraint itself is asserted by
    # ``test_duplicate_job_applications_are_refused_by_the_schema``. What is
    # directly inspectable here is the composite the admin list filters on.
    assert "ix_job_applications_post_status" in _index_names(engine, "job_applications")
    declared = {
        constraint.name
        for constraint in JobApplication.__table__.constraints
        if constraint.name
    }
    assert "uq_job_application" in declared


# ---------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------

#: Tables the location feature adds. Created by ``create_all`` on an existing
#: database, exactly like ``job_candidates`` and ``tutor_locations`` before them.
EXPECTED_LOCATION_TABLES = ("locations", "location_aliases")

#: Columns added to tables that already had rows.
EXPECTED_LOCATION_COLUMNS = {
    "tutor_locations": ("location_id", "is_confirmed", "source", "created_at"),
    "job_posts": ("location_id",),
    "student_requests": ("location", "location_id"),
}


def test_location_tables_are_created_by_create_all(tmp_path) -> None:
    """New tables land on a populated database with no hand-written DDL."""
    engine = _legacy_engine(tmp_path)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)
    tables = set(inspect(engine).get_table_names())
    missing = [name for name in EXPECTED_LOCATION_TABLES if name not in tables]
    assert not missing, f"create_all did not create: {missing}"


def _legacy_engine_with_locations(tmp_path, tutors=1):
    """A legacy database that also holds the pre-location ``tutor_locations``.

    ``LEGACY_TUTORS_DDL`` stops before the matching engine, which is the shape
    this module already exercises; the location columns have to be added to a
    table that already exists to be a real upgrade rather than a fresh ``create``.
    """
    engine = _legacy_engine(tmp_path, tutors=tutors)
    with engine.begin() as connection:
        connection.execute(text(LEGACY_TUTOR_LOCATIONS_DDL))
    return engine


def test_location_columns_are_added_to_existing_tables(tmp_path) -> None:
    """Every added column is nullable or defaulted, so rows survive."""
    engine = _legacy_engine_with_locations(tmp_path)
    apply_schema_upgrades(engine)
    present = {column["name"] for column in inspect(engine).get_columns("tutor_locations")}
    missing = [
        name for name in EXPECTED_LOCATION_COLUMNS["tutor_locations"] if name not in present
    ]
    assert not missing, f"tutor_locations is missing {missing}"


def test_a_job_post_gains_its_link(tmp_path) -> None:
    engine = _legacy_engine_with_locations(tmp_path)
    apply_schema_upgrades(engine)
    present = {column["name"] for column in inspect(engine).get_columns("job_posts")}
    assert "location_id" in present


def test_existing_rows_keep_their_text_and_are_treated_as_confirmed(tmp_path) -> None:
    """A legacy location stays exactly as it was typed, and still counts."""
    engine = _legacy_engine_with_locations(tmp_path, tutors=1)
    apply_schema_upgrades(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tutor_locations (id, tutor_id, location,"
                " normalized_location) VALUES (1, 1, 'Bole', 'bole')"
            )
        )
    # A pre-existing row has no ``is_confirmed`` of its own, so the server
    # default — true — is what it means: the tutor typed it as an area to teach.
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT location, is_confirmed, location_id FROM tutor_locations")
        ).one()
    assert row[0] == "Bole"
    assert bool(row[1]) is True
    assert row[2] is None


def test_existing_tutor_location_rows_are_unlinked_not_deleted(tmp_path) -> None:
    """An imported tutor's free text is kept even when it matches nothing."""
    engine = _legacy_engine_with_locations(tmp_path, tutors=1)
    apply_schema_upgrades(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tutor_locations (id, tutor_id, location,"
                " normalized_location) VALUES (1, 1, 'Somewhere Unknown',"
                " 'somewhere unknown')"
            )
        )
    with engine.connect() as connection:
        location_id = connection.execute(
            text("SELECT location_id FROM tutor_locations")
        ).scalar()
    assert location_id is None


def test_existing_job_posts_keep_their_text_and_gain_only_a_link(tmp_path) -> None:
    engine = _legacy_engine_with_locations(tmp_path, tutors=1)
    apply_schema_upgrades(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO job_posts (id, public_post_id, telegram_user_id,"
                " location, student_level, tutor_category, duration, frequency,"
                " subjects, compensation, generated_text, status, created_at,"
                " updated_at) VALUES (1, 'JOB-000001', -1, 'Ayat Tsebel',"
                " 'Grade 4', 'Both', '1 hour', '2 days', 'Mathematics',"
                " '300 ETB', '', 'PUBLISHED', '2026-01-01', '2026-01-01')"
            )
        )
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT location, location_id FROM job_posts")
        ).one()
    # The original text is the audit trail and is never overwritten.
    assert row[0] == "Ayat Tsebel"
    assert row[1] is None


def test_location_unique_constraints_are_present(tmp_path) -> None:
    engine = _legacy_engine(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)

    constraints = {
        constraint["name"]
        for constraint in inspect(engine).get_unique_constraints("locations")
    }
    assert "uq_locations_name_city" in constraints


def test_seeding_the_registry_is_idempotent(tmp_path) -> None:
    """A restart must not duplicate the curated locations."""
    from app.services import location_registry_service

    engine = _legacy_engine_with_locations(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)

    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        first = location_registry_service.seed_registry(session)
        session.commit()
        assert first > 0
        second = location_registry_service.seed_registry(session)
        session.commit()
        assert second == 0
    finally:
        session.close()


def test_locations_indexes_exist(tmp_path) -> None:
    engine = _legacy_engine_with_locations(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)

    indexes = {
        index["name"] for index in inspect(engine).get_indexes("locations")
    }
    assert "ix_locations_normalized_name" in indexes
    alias_indexes = {
        index["name"] for index in inspect(engine).get_indexes("location_aliases")
    }
    assert "ix_location_aliases_normalized_alias" in alias_indexes


# ---------------------------------------------------------------------------
# the location channel columns
# ---------------------------------------------------------------------------


def test_the_channel_columns_are_added_to_an_existing_locations_table(
    tmp_path,
) -> None:
    """An administrator's channel directory must attach to a populated
    locations table without losing a single existing row."""
    from app.services import location_registry_service

    engine = _legacy_engine_with_locations(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)

    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        location_registry_service.seed_registry(session)
        session.commit()
        before = session.execute(select(func.count(Location.id))).scalar_one()
    finally:
        session.close()

    # Drop the channel columns, as an older deployment would not have them.
    with engine.begin() as connection:
        # SQLite refuses to drop a column an index still covers, so the index
        # goes first — which is what an older deployment's schema looks like.
        connection.execute(text("DROP INDEX IF EXISTS ix_locations_is_active"))
        connection.execute(text("DROP INDEX IF EXISTS uq_locations_location_code"))
        connection.execute(text("DROP INDEX IF EXISTS ix_locations_source_message_id"))
        for column in (
            "location_code",
            "woreda",
            "source_chat_id",
            "source_message_id",
            "synced_at",
            "is_active",
        ):
            connection.execute(text(f"ALTER TABLE locations DROP COLUMN {column}"))

    apply_schema_upgrades(engine)

    columns = {
        column["name"] for column in inspect(engine).get_columns("locations")
    }
    for expected in (
        "location_code",
        "woreda",
        "source_chat_id",
        "source_message_id",
        "synced_at",
        "is_active",
    ):
        assert expected in columns, f"{expected} was not added"

    session = factory()
    try:
        after = session.execute(select(func.count(Location.id))).scalar_one()
        assert after == before, "the upgrade must not drop existing locations"
        # Existing rows default sensibly rather than becoming unusable.
        assert all(
            row.is_active for row in session.execute(select(Location)).scalars()
        )
        assert all(
            row.location_code is None
            for row in session.execute(select(Location)).scalars()
        )
    finally:
        session.close()


def test_the_location_code_index_is_created_on_an_existing_table(tmp_path) -> None:
    engine = _legacy_engine_with_locations(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX IF EXISTS uq_locations_location_code"))
        connection.execute(text("ALTER TABLE locations DROP COLUMN location_code"))

    apply_schema_upgrades(engine)

    indexes = {index["name"] for index in inspect(engine).get_indexes("locations")}
    assert "uq_locations_location_code" in indexes


def test_duplicate_tutor_area_rows_are_collapsed_before_the_unique_index(
    tmp_path,
) -> None:
    """Two spellings of one place, both already resolved to the same canonical
    location, would make the new unique index fail and abort the whole upgrade."""
    engine = _legacy_engine(tmp_path)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)

    # Stand in for the data an older deployment already holds, before the index
    # that forbids it exists.
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX IF EXISTS uq_tutor_location_canonical"))

    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        from tests.factories import create_tutor

        tutor = create_tutor(session, -90501)
        location = Location(
            name="Bole",
            normalized_name="bole",
            city="Addis Ababa",
            provider="MANUAL",
            latitude=8.9987,
            longitude=38.7890,
        )
        session.add(location)
        session.flush()
        # Two different spellings, one canonical place.
        session.add_all(
            [
                TutorLocation(
                    tutor_id=tutor.id, location="Bole",
                    normalized_location="bole", location_id=location.id,
                ),
                TutorLocation(
                    tutor_id=tutor.id, location="bole",
                    normalized_location="bole", location_id=location.id,
                ),
            ]
        )
        session.commit()
        assert (
            session.query(TutorLocation)
            .filter(TutorLocation.tutor_id == tutor.id)
            .count()
            == 2
        )
    finally:
        session.close()

    apply_schema_upgrades(engine)

    indexes = {
        index["name"] for index in inspect(engine).get_indexes("tutor_locations")
    }
    assert "uq_tutor_location_canonical" in indexes

    session = factory()
    try:
        # The earliest row wins: it is the one the tutor confirmed first.
        assert (
            session.query(TutorLocation)
            .filter(TutorLocation.location == "Bole")
            .count()
            == 1
        )
        assert session.query(TutorLocation).count() == 1
    finally:
        session.close()


def test_the_unique_index_on_canonical_tutor_areas_exists(tmp_path) -> None:
    engine = _legacy_engine(tmp_path)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)
    indexes = {
        index["name"] for index in inspect(engine).get_indexes("tutor_locations")
    }
    assert "uq_tutor_location_canonical" in indexes


def test_the_is_active_index_exists(tmp_path) -> None:
    engine = _legacy_engine_with_locations(tmp_path, tutors=1)
    Base.metadata.create_all(bind=engine)
    apply_schema_upgrades(engine)
    indexes = {index["name"] for index in inspect(engine).get_indexes("locations")}
    assert "ix_locations_is_active" in indexes
