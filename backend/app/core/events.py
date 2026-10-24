"""The in-process event hub.

Everything that happens — a structure opened, an auto decision, a feed going quiet — is published
here once. The hub stores it in the event log, pushes it to every dashboard WebSocket, and hands it
to each subscriber (the notifier) on that subscriber's own queue and worker, so a slow Telegram
request never delays a dashboard update or another subscriber.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.db import session
from app.core.models import EventLog

log = logging.getLogger(__name__)

Handler = Callable[["Event"], Awaitable[None]]
CLIENT_QUEUE = 500  # a browser tab that stops reading loses its oldest events, never blocks the hub


@dataclass(frozen=True)
class Event:
    type: str
    message: str = ""
    level: str = "info"  # info | warning | error
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventHub:
    def __init__(self) -> None:
        self._handlers: list[tuple[str, Handler, asyncio.Queue[Event]]] = []
        self._workers: list[asyncio.Task[None]] = []
        self._clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self.counts: dict[str, int] = {}

    def subscribe(self, name: str, handler: Handler) -> None:
        self._handlers.append((name, handler, asyncio.Queue()))

    async def start(self) -> None:
        for name, handler, queue in self._handlers:
            self._workers.append(asyncio.create_task(self._work(name, handler, queue), name=f"hub:{name}"))

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._workers.clear()

    def publish(self, type: str, message: str = "", level: str = "info", persist: bool = True, **data: Any) -> Event:
        event = Event(type=type, message=message, level=level, data=data)
        self.counts[type] = self.counts.get(type, 0) + 1
        if persist:
            with session() as s:
                s.add(EventLog(ts=event.ts, type=type, level=level, message=message, data=data or None))
                s.commit()
        if level != "info":
            log.log(logging.ERROR if level == "error" else logging.WARNING, "%s: %s", type, message)
        payload = event.to_dict()
        for client in list(self._clients):
            if client.full():
                client.get_nowait()
            client.put_nowait(payload)
        for _name, _handler, queue in self._handlers:
            queue.put_nowait(event)
        return event

    def connect(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(CLIENT_QUEUE)
        self._clients.add(queue)
        return queue

    def disconnect(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._clients.discard(queue)

    @property
    def clients(self) -> int:
        return len(self._clients)

    @staticmethod
    async def _work(name: str, handler: Handler, queue: asyncio.Queue[Event]) -> None:
        while True:
            event = await queue.get()
            try:
                await handler(event)
            except Exception:
                log.exception("event_handler_failed handler=%s type=%s", name, event.type)
