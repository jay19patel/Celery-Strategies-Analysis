"""System service — operational health, metrics, and audit log queries."""

from __future__ import annotations

import os
import time
from typing import Any

import psutil

from app.application.base_service import BaseService
from app.infrastructure.database.repositories.event_repo import EventRepository
from app.infrastructure.redis.pubsub import EventPublisher


class SystemService(BaseService):
    """Orchestrates system audit logs, health checks, and runtime metrics."""

    def __init__(
        self,
        event_repo: EventRepository,
        publisher: EventPublisher | None = None,
    ) -> None:
        super().__init__(publisher)
        self._events = event_repo
        self._start_time = time.time()

    def get_events(
        self,
        limit: int = 100,
        event_type: str | None = None,
        since_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch historical system events from the database."""
        return self._events.list_recent(limit=limit, event_type=event_type, since_id=since_id)

    def log_event(self, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        """Record an event to the audit trail and publish it."""
        event = self._events.append(event_type, data)
        self._info("system_event_recorded", type=event_type)
        return {"id": event.id, "ts": event.ts, "type": event.type}

    def get_system_health(self) -> dict[str, Any]:
        """Collect host system metrics (CPU, RAM, disk, uptime)."""
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()

        return {
            "status": "healthy",
            "uptime_seconds": round(time.time() - self._start_time, 2),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_usage_mb": round(mem_info.rss / (1024 * 1024), 2),
            "total_system_memory_gb": round(psutil.virtual_memory().total / (1024**3), 2),
            "available_memory_percent": round(psutil.virtual_memory().available * 100 / psutil.virtual_memory().total, 1),
        }
