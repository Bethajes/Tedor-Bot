"""Database engine, session factory and helpers."""

from __future__ import annotations

import logging
from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
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


def plan_schema_upgrades(engine: Engine) -> list[tuple[str, str]]:
    """Return ``(statement, "table.column")`` pairs that would be applied.

    Pure inspection — nothing is executed — so the plan can be logged or
    asserted on before it is run.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    dialect = engine.dialect
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


def apply_schema_upgrades(engine: Engine | None = None) -> list[str]:
    """Add any missing columns and indexes. Returns the applied column names.

    Safe to call repeatedly: a second run on an up-to-date database applies
    nothing.
    """
    target = engine if engine is not None else get_engine()
    applied: list[str] = []

    with target.begin() as connection:
        for statement, label in plan_schema_upgrades(target):
            connection.execute(text(statement))
            applied.append(label)
            logger.info("Added column %s", label)

        applied.extend(_ensure_indexes(target, connection))

    if applied:
        logger.info("Schema upgrade applied", extra={"changes": len(applied)})
    return applied


def _ensure_indexes(target: Engine, connection) -> list[str]:
    """Create indexes that ``ADD COLUMN`` could not, using the inspector.

    An index on a column added above does not exist yet, and SQLite has no
    inline form. ``CREATE INDEX IF NOT EXISTS`` keeps this idempotent, and the
    inspector lookup is portable rather than querying ``sqlite_master``.
    """
    inspector = inspect(target)
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
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {name} ON {table.name} "
                    f"({', '.join(columns)})"
                )
            )
            created.append(name)
            logger.info("Created index %s on %s", name, table.name)
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