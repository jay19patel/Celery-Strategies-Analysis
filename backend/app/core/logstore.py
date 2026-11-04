"""Warnings and errors from the process, kept in the event log — counted, not repeated.

The journal rotates and nobody reads it; the System page does. Every WARNING and ERROR an `app` or
scheduler logger raises becomes an `EventLog` row of type "Log", with its traceback when it carried
one. The same message (by its template, so "websocket_down error=X" and "...error=Y" are one) within
`WINDOW` seconds is ONE row whose count rises, written back at most every `FLUSH` seconds: a socket
that drops a hundred times an hour is one line saying so, not a hundred rows filling the disk.

Kept `KEEP_DAYS`, longer than ordinary events: a fault worth finding is often found late.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import traceback

from app.core.db import session
from app.core.models import EventLog

WINDOW = 600.0
FLUSH = 30.0
KEEP_DAYS = 90
CAPTURED = ("app", "apscheduler", "uvicorn.error")
SKIPPED = ("app.core.events",)  # the hub stores its own warnings as events already
MAX_KEYS = 500


class EventLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self._lock = threading.Lock()
        # (logger, template) -> [row id, count, first ts, last ts, last written]
        self._open: dict[tuple[str, str], list[float]] = {}

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith(CAPTURED) or record.name.startswith(SKIPPED):
            return
        # A log that cannot be stored must never break the code that logged it.
        with contextlib.suppress(Exception):
            self._store(record)

    def _store(self, record: logging.LogRecord) -> None:
        now = record.created
        key = (record.name, str(record.msg)[:200])
        level = "error" if record.levelno >= logging.ERROR else "warning"
        with self._lock:
            row = self._open.get(key)
            if row and now - row[2] < WINDOW:
                row[1] += 1
                row[3] = now
                if now - row[4] < FLUSH:
                    return
                row[4] = now
                count, first, last, row_id = int(row[1]), row[2], row[3], int(row[0])
                with session() as s:
                    stored = s.get(EventLog, row_id)
                    if stored is not None:
                        stored.data = (stored.data or {}) | {"count": count, "first": first, "last": last}
                        stored.ts = last
                        s.add(stored)
                        s.commit()
                return
            if len(self._open) >= MAX_KEYS:
                for k in [k for k, v in self._open.items() if now - v[2] >= WINDOW]:
                    del self._open[k]
            data: dict[str, object] = {"logger": record.name, "count": 1, "first": now, "last": now}
            if record.exc_info and record.exc_info[0] is not None:
                data["trace"] = "".join(traceback.format_exception(*record.exc_info))[-2000:]
            with session() as s:
                stored = EventLog(ts=now, type="Log", level=level, message=record.getMessage()[:1000], data=data)
                s.add(stored)
                s.commit()
                s.refresh(stored)
            self._open[key] = [float(stored.id or 0), 1, now, now, now]


def install() -> EventLogHandler:
    """Attach once to the root logger, for as long as the app runs; the database must be open."""
    root = logging.getLogger()
    for existing in root.handlers:
        if isinstance(existing, EventLogHandler):
            return existing
    handler = EventLogHandler()
    root.addHandler(handler)
    return handler


def uninstall(handler: EventLogHandler) -> None:
    logging.getLogger().removeHandler(handler)
