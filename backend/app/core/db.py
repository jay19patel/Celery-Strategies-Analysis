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
    _engine = engine
    return engine


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
