"""SQLite engine and sessions.

The engine runs in one asyncio process and is the only writer, so a short synchronous session per
operation is enough: SQLite in WAL mode answers these in well under a millisecond.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.core import models  # noqa: F401 - registers the tables

_engine: Engine | None = None


def init_db(path: str) -> Engine:
    global _engine
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    url = "sqlite://" if path == ":memory:" else f"sqlite:///{path}"
    kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
    if path == ":memory:":
        from sqlalchemy.pool import StaticPool

        kwargs["poolclass"] = StaticPool  # one shared in-memory database, not one per connection
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _pragmas(conn: Any, _record: Any) -> None:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)
    if path != ":memory:":
        _incremental_vacuum(engine)
    _engine = engine
    return engine


def _add_missing_columns(engine: Engine) -> None:
    """`create_all` makes missing tables, never missing columns. A column added to a model later is
    added here to an existing database, with its default, so an upgrade needs no manual step."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                default = column.default.arg if column.default is not None and not callable(column.default.arg) else None
                literal = "" if default is None else f" DEFAULT {default!r}" if isinstance(default, str) else f" DEFAULT {default}"
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(engine.dialect)}{literal}'
                conn.execute(text(ddl))


def _incremental_vacuum(engine: Engine) -> None:
    """Deleted rows leave free pages that a SQLite file never gives back on its own; over a year of
    pruning that is most of the file. Switched once to incremental auto-vacuum (it takes one full
    VACUUM, here at start-up before anything else writes), `maintain` then returns pages each hour."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        if conn.exec_driver_sql("PRAGMA auto_vacuum").scalar() != 2:
            conn.exec_driver_sql("PRAGMA auto_vacuum = INCREMENTAL")
            conn.exec_driver_sql("VACUUM")
    # Connections opened before the VACUUM keep the old mode, and on them `incremental_vacuum` is a
    # silent no-op: start the pool afresh.
    engine.dispose()


def maintain(pages: int = 4000) -> dict[str, int]:
    """Hourly upkeep for a database meant to run for years: give back up to `pages` free pages, fold
    the write-ahead log into the file and truncate it, and refresh the planner's statistics.

    On the driver's own cursor, drained: SQLite frees one page per step of `incremental_vacuum`, and
    a result closed after its first step (as SQLAlchemy closes one that returns no rows) frees one."""
    if _engine is None:
        raise RuntimeError("init_db() has not run")
    raw = _engine.raw_connection()
    try:
        cur = raw.driver_connection.cursor()
        free = int(cur.execute("PRAGMA freelist_count").fetchone()[0])
        cur.execute(f"PRAGMA incremental_vacuum({int(pages)})").fetchall()
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        cur.execute("PRAGMA optimize").fetchall()
        left = int(cur.execute("PRAGMA freelist_count").fetchone()[0])
    finally:
        raw.close()
    return {"freed_pages": free - left, "free_pages": left}


@contextmanager
def session() -> Iterator[Session]:
    if _engine is None:
        raise RuntimeError("init_db() has not run")
    with Session(_engine, expire_on_commit=False) as s:
        yield s


def kv_get(key: str, default: Any = None) -> Any:
    from app.core.models import KV

    with session() as s:
        row = s.get(KV, key)
        return default if row is None else row.value


def kv_set(key: str, value: Any) -> None:
    from app.core.models import KV

    with session() as s:
        s.merge(KV(key=key, value=value))
        s.commit()
