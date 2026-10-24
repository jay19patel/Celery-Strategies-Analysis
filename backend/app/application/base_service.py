"""Base service — common patterns every service inherits.

Provides structured logging, event publishing, and a consistent
interface. Business logic lives in subclass methods; infrastructure
wiring lives here.
"""

from __future__ import annotations

import logging
from typing import Any

from app.infrastructure.redis.pubsub import EventPublisher


class BaseService:
    """Abstract base for all application services.

    Every service gets:
    - A logger named after the concrete class
    - An optional event publisher for broadcasting domain events
    - A standard ``_publish`` helper that is safe to call even when
      no publisher is configured (engine-less mode)
    """

    def __init__(self, publisher: EventPublisher | None = None) -> None:
        self._publisher = publisher
        self._log = logging.getLogger(f"app.service.{type(self).__name__}")

    async def _publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish a domain event to Redis PubSub.

        Safe to call when no publisher is wired — silently skips.
        """
        if self._publisher:
            await self._publisher.publish(event_type, data)

    def _info(self, action: str, **kwargs: Any) -> None:
        """Structured info log: ``service.action key=value ...``"""
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
        self._log.info("%s %s", action, extra)

    def _warn(self, action: str, **kwargs: Any) -> None:
        """Structured warning log."""
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
        self._log.warning("%s %s", action, extra)

    def _error(self, action: str, **kwargs: Any) -> None:
        """Structured error log."""
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
        self._log.error("%s %s", action, extra)
