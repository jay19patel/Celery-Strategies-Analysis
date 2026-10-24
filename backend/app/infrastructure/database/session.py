"""SQLModel engine and session factory.

A single engine is created at startup and shared across all requests via
FastAPI's dependency injection. Each request gets its own session that is
committed or rolled back when the request ends.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger(__name__)

# Module-level engine — initialised once by ``init_db``.
_engine = None


def init_db(database_url: str) -> None:
    """Create the engine and all tables.

    Called once at application startup from the FastAPI lifespan.
    SQLite-specific pragmas are set via event listeners for performance:
    WAL mode, foreign keys, and synchronous=NORMAL.
    """
    global _engine  # noqa: PLW0603 — intentional module-level singleton

    # Ensure the data directory exists
    if database_url.startswith("sqlite:///"):
        db_path = Path(database_url.replace("sqlite:///", ""))
        db_path.parent.mkdir(parents=True, exist_ok=True)

    connect_args = {"check_same_thread": False} if "sqlite" in database_url else {}

    _engine = create_engine(
        database_url,
        echo=False,
        connect_args=connect_args,
    )

    # SQLite performance pragmas
    if "sqlite" in database_url:
        from sqlalchemy import event

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    SQLModel.metadata.create_all(_engine)
    logger.info("database_ready url=%s", database_url)


def get_engine():
    """Return the module-level engine. Crashes if ``init_db`` was not called."""
    if _engine is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    return _engine


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a session per request.

    The session is committed if the request handler succeeds,
    rolled back if it raises.
    """
    engine = get_engine()
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
