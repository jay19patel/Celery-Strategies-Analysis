"""Event repository — append-only event log."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import col, select

from app.domain.models import Event
from app.infrastructure.database.repositories.base_repo import BaseRepository


class EventRepository(BaseRepository[Event]):
    """Data access for the events table — the system's audit trail."""

    model = Event

    def append(self, event_type: str, data: dict[str, Any]) -> Event:
        """Write an event to the log."""
        return self.create(Event(
            type=event_type,
            data=json.dumps(data, default=str),
        ))

    def list_recent(
        self,
        limit: int = 100,
        event_type: str | None = None,
        since_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch recent events with optional filters, parsed for the API."""
        stmt = select(Event).order_by(col(Event.id).desc()).limit(limit)
        if event_type:
            stmt = stmt.where(Event.type == event_type)
        if since_id:
            stmt = stmt.where(Event.id > since_id)

        results = []
        for row in self._session.exec(stmt).all():
            try:
                parsed = json.loads(row.data)
            except json.JSONDecodeError:
                parsed = {"raw": row.data}
            results.append({"id": row.id, "ts": row.ts, "type": row.type, **parsed})
        return results
