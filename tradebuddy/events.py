"""Events and the in-process event bus.

Every component talks through the bus. Each subscriber has its own queue and
worker, so one slow handler never stalls another, and a single subscriber
handles its events strictly in order.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from tradebuddy.delta import Candle

log = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Event:
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"type": type(self).__name__, **asdict(self)}


# -- market -------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Tick(Event):
    symbol: str
    price: float


@dataclass(frozen=True, kw_only=True)
class CandleClosed(Event):
    symbol: str
    resolution: str
    bar_time: int
    source: str  # "websocket" (bar rolled over on the stream) | "clock" (bar time passed first)
    candle: Candle | None = None  # the bar as the WebSocket saw it, when it did


@dataclass(frozen=True, kw_only=True)
class FeedStatus(Event):
    connected: bool
    authenticated: bool = False
    error: str = ""


# -- strategy -----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class SignalGenerated(Event):
    strategy: str
    version: int
    symbol: str
    side: str  # "buy" | "sell"
    reason: str
    bar_time: int
    size: int
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None


@dataclass(frozen=True, kw_only=True)
class StrategyError(Event):
    strategy: str
    symbol: str
    error: str


@dataclass(frozen=True, kw_only=True)
class TradeSkipped(Event):
    strategy: str
    symbol: str
    side: str
    reason: str


# -- orders -------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class OrderRequested(Event):
    client_order_id: str
    broker: str
    strategy: str
    symbol: str
    side: str
    size: int
    price: float
    stop_loss: float
    take_profit: float


@dataclass(frozen=True, kw_only=True)
class OrderPlaced(Event):
    client_order_id: str
    order_id: str
    status: str


@dataclass(frozen=True, kw_only=True)
class OrderFailed(Event):
    client_order_id: str
    error: str


@dataclass(frozen=True, kw_only=True)
class OrderUnknown(Event):
    """The exchange did not answer clearly. The order is looked up, never resent."""

    client_order_id: str
    error: str


@dataclass(frozen=True, kw_only=True)
class OrderUpdate(Event):
    """An order change pushed by the private WebSocket."""

    client_order_id: str | None
    order_id: str
    symbol: str
    state: str


@dataclass(frozen=True, kw_only=True)
class PositionUpdate(Event):
    broker: str
    symbol: str
    size: float  # signed: + long, - short, 0 flat
    entry_price: float


@dataclass(frozen=True, kw_only=True)
class PositionClosed(Event):
    broker: str
    strategy: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    pnl: float
    reason: str


# -- control ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ToggleChanged(Event):
    key: str
    enabled: bool


@dataclass(frozen=True, kw_only=True)
class SettingsChanged(Event):
    changed: list[str]  # field names only, never values: some are secrets
    trading_stopped: bool


# -- bus ----------------------------------------------------------------------

Handler = Callable[[Any], Awaitable[None]]


@dataclass
class _Subscription:
    name: str
    types: tuple[type[Event], ...]
    handler: Handler
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    pending: int = 0
    handled: int = 0
    errors: int = 0
    busy_seconds: float = 0.0
    max_seconds: float = 0.0
    last_at: float = 0.0
    last_error: str = ""


@dataclass
class _TypeStats:
    count: int = 0
    last_at: float = 0.0


class EventBus:
    def __init__(self) -> None:
        self._subs: list[_Subscription] = []
        self._tasks: list[asyncio.Task] = []
        self._types: dict[str, _TypeStats] = {}

    def subscribe(self, handler: Handler, *types: type[Event]) -> None:
        self._subs.append(_Subscription(getattr(handler, "__qualname__", repr(handler)), types or (Event,), handler))

    def publish(self, event: Event) -> None:
        stats = self._types.setdefault(type(event).__name__, _TypeStats())
        stats.count += 1
        stats.last_at = event.ts
        for sub in self._subs:
            if isinstance(event, sub.types):
                sub.pending += 1
                sub.queue.put_nowait(event)

    def stats(self) -> dict[str, Any]:
        """Per-worker and per-event-type counters for the dashboard."""
        return {
            "workers": [
                {
                    "name": s.name,
                    "listens_to": [t.__name__ for t in s.types],
                    "queue": s.queue.qsize(),
                    "handled": s.handled,
                    "errors": s.errors,
                    "avg_ms": round(1000 * s.busy_seconds / s.handled, 2) if s.handled else 0.0,
                    "max_ms": round(1000 * s.max_seconds, 2),
                    "last_at": s.last_at,
                    "last_error": s.last_error,
                }
                for s in self._subs
            ],
            "events": {name: {"count": t.count, "last_at": t.last_at} for name, t in sorted(self._types.items())},
        }

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._worker(s), name=f"bus:{s.name}") for s in self._subs]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def drain(self) -> None:
        """Wait until every published event, including follow-on events, has been handled."""
        while any(s.pending for s in self._subs):
            await asyncio.sleep(0)

    async def _worker(self, sub: _Subscription) -> None:
        while True:
            event = await sub.queue.get()
            started = time.perf_counter()
            try:
                await sub.handler(event)
            except Exception as exc:
                sub.errors += 1
                sub.last_error = f"{type(event).__name__}: {exc!r}"
                log.exception("handler_failed handler=%s event=%s", sub.name, type(event).__name__)
            finally:
                elapsed = time.perf_counter() - started
                sub.handled += 1
                sub.busy_seconds += elapsed
                sub.max_seconds = max(sub.max_seconds, elapsed)
                sub.last_at = time.time()
                sub.pending -= 1
