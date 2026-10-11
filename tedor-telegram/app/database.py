"""Database engine, session factory and helpers."""

from __future__ import annotations

import logging
from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.models import Base

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _engine_kwargs(url: str) -> dict:
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        # A shared in-memory database needs a single connection.
        if ":memory:" in url or url.endswith("sqlite://"):
            kwargs["connect_args"] = {"check_same_thread": False}
            kwargs["poolclass"] = StaticPool
        else:
            kwargs["connect_args"] = {"check_same_thread": False}
    return kwargs


def create_db_engine(url: str | None = None) -> Engine:
    """Create a new engine for ``url`` (defaults to the configured database)."""
    resolved = url or settings.database_url
    settings.ensure_data_dir()
    engine = create_engine(resolved, **_engine_kwargs(resolved))

    if resolved.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def configure_database(url: str | None = None) -> Engine:
    """Point the application at ``url`` and rebuild the session factory."""
    global _engine, _session_factory
    _engine = create_db_engine(url)
    _session_factory = sessionmaker(
        bind=_engine, autoflush=False, expire_on_commit=False, future=True
    )
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        configure_database()
    assert _engine is not None
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        configure_database()
    assert _session_factory is not None
    return _session_factory


def init_db() -> None:
    """Create all tables and bring an existing database up to date.

    ``create_all`` only creates *missing tables*; it never adds a column to a
    table that already exists. So the additive upgrade below runs afterwards
    and patches the columns introduced after the first release.
    """
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    applied = apply_schema_upgrades(engine)
    logger.info(
        "Database schema ready",
        extra={"upgraded_columns": len(applied)} if applied else None,
    )
    _seed_location_registry()


def _seed_location_registry() -> int:
    """Materialise the curated location registry into the ``locations`` table.

    ``app.services.location_service.LOCATIONS`` is the curated set the matching
    engine has always used, keyed by hand-written zones and adjacency. Seeding
    it as ``REGISTRY`` rows lets existing free-text locations resolve to a real
    ``Location`` without changing what they mean, so the new canonical model
    and the old zone ladder agree instead of competing.

    Idempotent and additive: rows are matched on ``(normalized_name, city)``,
    so running this against a populated database adds only what is missing and
    never rewrites an existing row's name, status or coordinates.
    """
    from app.services import location_registry_service

    factory = get_session_factory()
    session = factory()
    try:
        created = location_registry_service.seed_registry(session)
        session.commit()
    except Exception:
        session.rollback()
        # A seeding failure must never stop the bot or API from starting: the
        # curated registry still works as a pure-code fallback.
        logger.exception("Could not seed the location registry")
        return 0
    finally:
        session.close()
    if created:
        logger.info("Seeded location registry", extra={"locations": created})
    return created


# ---------------------------------------------------------------------------
# Additive schema upgrades
# ---------------------------------------------------------------------------
#
# Production holds 1,000+ tutors in an already-populated SQLite file, so the
# new columns cannot be introduced by recreating tables. Every statement here
# is additive and idempotent:
#
#   * a column is added only when the table exists and lacks it;
#   * a column is added only when the model declares it nullable or defaulted,
#     because ``ALTER TABLE ... ADD COLUMN`` cannot backfill safely otherwise;
#   * indexes are created separately, as SQLite cannot index inline in ADD
#     COLUMN.
#
# The mapping is generated from the models rather than hand-written, so a new
# column on a mapped class is picked up automatically and the two lists can
# never drift.
#
# Known limitation: ``status`` is widened from VARCHAR(20) to VARCHAR(32) in the
# model, but neither SQLite nor a plain ``ADD COLUMN`` can widen an existing
# column, so an upgraded database keeps VARCHAR(20). That is sufficient — the
# longest new status value, ``PENDING_CONFIRMATION``, is exactly 20 characters
# — and widening it properly belongs in the first Alembic revision.

#: Columns to skip: created by ``create_all`` on a fresh database, and never
#: meaningful to backfill onto an existing row.
_SKIP_COLUMN_NAMES = frozenset({"id"})


def _is_addable(column) -> bool:
    """Can this column be added to a table that already has rows?

    ``server_default`` counts: a NOT NULL column with a default is safe, while
    a NOT NULL column without one would fail on a populated table.
    """
    if column.primary_key:
        return False
    if not column.nullable and column.server_default is None:
        return False
    return True


def _column_ddl(table_name: str, column, dialect) -> str:
    """Render one ``ALTER TABLE ... ADD COLUMN`` statement.

    Compiles only the column *type* and its server default — never
    ``str(column)`` — because a compiled ``Column`` embeds constraints that
    ``ADD COLUMN`` cannot accept.
    """
    ddl = f"{column.name} {column.type.compile(dialect=dialect)}"
    if column.server_default is not None:
        ddl += f" DEFAULT {_render_default(column.server_default.arg)}"
    return f"ALTER TABLE {table_name} ADD COLUMN {ddl}"


#: Bare SQL keywords that must never be quoted. ``sql.false()`` reaches the
#: renderer as the text ``false``; emitting ``'false'`` would store the *string*
#: "false", which is truthy in a boolean column.
_BARE_SQL_KEYWORDS = frozenset({"true", "false"})


def _render_default(default) -> str:
    """Render a server default as SQL.

    ``server_default`` reaches us either as a ``TextClause`` (``sql.false()``,
    ``text("'x'")``) or as a bare Python value when it was declared as a plain
    string, so both shapes are handled.
    """
    rendered = getattr(default, "text", None)
    if rendered is not None:
        return rendered
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, (int, float)):
        return repr(default)
    text_value = str(default)
    if text_value.strip().casefold() in _BARE_SQL_KEYWORDS:
        return text_value.strip().casefold()
    return f"'{text_value}'"


def plan_schema_upgrades(target: Engine | Connection) -> list[tuple[str, str]]:
    """Return ``(statement, "table.column")`` pairs that would be applied.

    Pure inspection — nothing is executed — so the plan can be logged or
    asserted on before it is run.

    ``target`` may be an :class:`~sqlalchemy.Engine` or an open
    :class:`~sqlalchemy.Connection`. Callers that are *about to* execute the
    plan must pass their own connection. Inspecting through a second connection
    while the first sits inside an open transaction is not safe on SQLite: each
    connection caches the schema it last saw, so a plan computed elsewhere can
    describe a schema the executing connection has not yet re-read — which
    surfaces as ``duplicate column name`` on a column that is provably absent.
    """
    inspector = inspect(target)
    existing_tables = set(inspector.get_table_names())
    dialect = target.dialect
    statements: list[tuple[str, str]] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            # ``create_all`` handles new tables; their columns already exist.
            continue
        present = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present or column.name in _SKIP_COLUMN_NAMES:
                continue
            if not _is_addable(column):
                logger.error(
                    "Cannot add NOT NULL column %s.%s to an existing table",
                    table.name,
                    column.name,
                )
                continue
            statements.append(
                (_column_ddl(table.name, column, dialect), f"{table.name}.{column.name}")
            )
    return statements


def _force_ddl_transaction(connection: Connection) -> bool:
    """Make SQLite treat the enclosing block as one real transaction.

    SQLite has supported transactional DDL for a very long time, but the
    stdlib ``sqlite3`` driver in its default (legacy) mode only issues
    ``BEGIN`` when it sees an INSERT/UPDATE/DELETE. An ``ALTER TABLE`` therefore
    runs in autocommit, and a failure part-way through
    :func:`apply_schema_upgrades` would leave the schema half-patched — which is
    precisely what the surrounding ``begin()`` exists to prevent.

    Emitting ``BEGIN`` explicitly puts SQLite inside a transaction, so the
    driver's ``commit()``/``rollback()`` on the way out have something to act
    on. Returns whether it had to do anything, for the tests.
    """
    if connection.dialect.name != "sqlite":
        # Every other dialect emits BEGIN eagerly; ``in_transaction`` is enough.
        return False
    driver_connection = connection.connection.dbapi_connection
    if driver_connection.in_transaction:  # pragma: no cover - defensive
        return False
    driver_connection.execute("BEGIN")
    return True


def apply_schema_upgrades(engine: Engine | None = None) -> list[str]:
    """Add any missing columns and indexes. Returns the applied column names.

    Safe to call repeatedly: a second run on an up-to-date database applies
    nothing. Either the whole run lands or none of it does.
    """
    target = engine if engine is not None else get_engine()
    applied: list[str] = []

    with target.begin() as connection:
        _force_ddl_transaction(connection)
        # Planned through the executing connection, see plan_schema_upgrades.
        for statement, label in plan_schema_upgrades(connection):
            connection.execute(text(statement))
            applied.append(label)
            logger.info("Added column %s", label)

        _repair_duplicate_tutor_locations(connection)
        applied.extend(_ensure_indexes(connection))

    if applied:
        logger.info("Schema upgrade applied", extra={"changes": len(applied)})
    return applied


def _repair_duplicate_tutor_locations(connection) -> int:
    """Collapse tutor-area rows that share a canonical location, pre-index.

    ``uq_tutor_location`` only ever stopped the same *spelling* being stored
    twice. Before ``uq_tutor_location_canonical`` can be created, "Bole" and
    "bole" — two rows for one tutor pointing at the same ``Location`` — would
    make the unique index fail and take the whole upgrade down with it. The
    earliest row wins, because it is the one the tutor confirmed first.

    Returns the number of rows removed.
    """
    inspector = inspect(connection)
    if "tutor_locations" not in set(inspector.get_table_names()):
        return 0
    live_columns = {
        column["name"] for column in inspector.get_columns("tutor_locations")
    }
    if not {"tutor_id", "location_id"} <= live_columns:
        return 0

    duplicates = connection.execute(
        text(
            "SELECT id FROM tutor_locations WHERE location_id IS NOT NULL "
            "AND id NOT IN (SELECT MIN(id) FROM tutor_locations "
            "WHERE location_id IS NOT NULL GROUP BY tutor_id, location_id)"
        )
    ).fetchall()
    if not duplicates:
        return 0
    removed = 0
    for (row_id,) in duplicates:
        connection.execute(
            text("DELETE FROM tutor_locations WHERE id = :id"), {"id": row_id}
        )
        removed += 1
    logger.info("Removed %s duplicate tutor-area rows", removed)
    return removed


def _ensure_indexes(connection) -> list[str]:
    """Create indexes that ``ADD COLUMN`` could not, using the inspector.

    An index on a column added above does not exist yet, and SQLite has no
    inline form. ``CREATE INDEX IF NOT EXISTS`` keeps this idempotent, and the
    inspector lookup is portable rather than querying ``sqlite_master``.

    The inspector is bound to ``connection``, not to the engine: the columns
    added moments ago are still uncommitted, so a second connection would read
    the pre-upgrade schema and conclude every index was already present.

    Each statement runs inside its own savepoint. A unique index can legitimately
    fail on an already-populated legacy table, and one missing index must not
    abort the columns that were added successfully — the upgrade would be
    retried forever instead of converging.
    """
    inspector = inspect(connection)
    live_tables = set(inspector.get_table_names())
    created: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in live_tables:
            continue
        existing = {
            index["name"] for index in inspector.get_indexes(table.name) if index["name"]
        }
        live_columns = {
            column["name"] for column in inspector.get_columns(table.name)
        }
        for index in table.indexes:
            name = index.name
            if name is None or name in existing:  # pragma: no cover - always named
                continue
            columns = [column.name for column in index.columns]
            if not columns:  # pragma: no cover - expression indexes unsupported
                continue
            if any(column not in live_columns for column in columns):
                # An index over a column this run could not add (for example a
                # NOT NULL column with no server default). Skipping keeps the
                # whole upgrade transactional instead of failing on it.
                logger.warning(
                    "Skipping index %s: one of its columns is missing", name
                )
                continue
            unique = "UNIQUE " if index.unique else ""
            statement = text(
                f"CREATE {unique}INDEX IF NOT EXISTS {name} ON {table.name} "
                f"({', '.join(columns)})"
            )
            connection.execute(text("SAVEPOINT index_upgrade"))
            try:
                connection.execute(statement)
            except SQLAlchemyError as exc:
                connection.execute(text("ROLLBACK TO SAVEPOINT index_upgrade"))
                connection.execute(text("RELEASE SAVEPOINT index_upgrade"))
                logger.error("Could not create index %s: %s", name, exc)
                continue
            connection.execute(text("RELEASE SAVEPOINT index_upgrade"))
            created.append(name)
            logger.info("Created %sindex %s on %s", unique.lower(), name, table.name)
    return created


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session scope: commit on success, rollback on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.error("Database transaction failed and was rolled back")
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_database() -> None:
    """Dispose of the current engine (used by tests)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None