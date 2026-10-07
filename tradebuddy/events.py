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
from typing import Any, ClassVar

from tradebuddy.delta import Candle

log = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Event:
    ts: float = field(default_factory=time.time)
    # How the event log treats it: "info" | "warning" (a refusal, a degraded state) | "error" (needs a look).
    LEVEL: ClassVar[str] = "info"

    @property
    def level(self) -> str:
        return self.LEVEL

    def to_dict(self) -> dict[str, Any]:
        return {"type": type(self).__name__, "level": self.level, **asdict(self)}


# -- market -------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Tick(Event):
    symbol: str
    price: float


@dataclass(frozen=True, kw_only=True)
class MarketStats(Event):
    """The exchange's rolling 24h view of a symbol, from the same ticker message as Tick."""

    symbol: str
    last: float | None = None  # last traded price
    mark: float | None = None
    index: float | None = None  # spot / index price
    open_24h: float | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    change_24h_pct: float | None = None  # last price vs 24h ago, as the exchange reports it
    mark_change_24h_pct: float | None = None
    volume_24h: float | None = None  # in the underlying (BTC for BTCUSD)
    turnover_24h_usd: float | None = None
    oi_usd: float | None = None
    funding_rate_pct: float | None = None
    bid: float | None = None
    ask: float | None = None


@dataclass(frozen=True, kw_only=True)
class CandleClosed(Event):
    symbol: str
    resolution: str
    bar_time: int
    source: str  # "websocket" (bar rolled over on the stream) | "clock" (bar time passed first)
    candle: Candle | None = None  # the bar as the WebSocket saw it, when it did


@dataclass(frozen=True, kw_only=True)
class FeedStatus(Event):
    """Published when the stream's state changes, not on every reconnect attempt."""

    LEVEL: ClassVar[str] = "warning"  # the event log's filter; a reconnect itself is info
    connected: bool
    authenticated: bool = False
    error: str = ""

    @property
    def level(self) -> str:
        return "info" if self.connected else "warning"


@dataclass(frozen=True, kw_only=True)
class FeedHeartbeat(Event):
    """The feed process's full status, every few seconds (distributed mode)."""

    status: dict[str, Any]
    process: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class OptionsSnapshot(Event):
    """The options book for one underlying, summarised (options.summarize). Every few seconds."""

    symbol: str  # the perpetual it belongs to: BTCUSD
    underlying: str  # BTC
    source: str  # "websocket" | "rest"
    summary: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class MarketAnalysis(Event):
    """The analyst's view of one symbol, every few minutes: context numbers, rule-based insights,
    the model forecast and the options playbook when a model is trained, and the AI review when on."""

    symbol: str
    context: dict[str, Any]
    insights: list[dict[str, Any]]
    forecast: dict[str, Any] | None = None
    playbook: dict[str, Any] | None = None
    ai: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class AIReport(Event):
    """One TradeBuddy AI (TB-AI) attempt: the written report, or why there is none this time."""

    ok: bool
    model: str
    report: dict[str, Any] | None = None  # tbai.clean(): headline, health, priorities, sections, symbols
    error: str = ""
    status: int | None = None  # Mistral's HTTP status on failure (429 = rate limited)
    paused_until: float | None = None  # no new request before this, after a rate limit
    limits: dict[str, str] = field(default_factory=dict)  # Mistral's rate-limit headers
    usage: dict[str, Any] = field(default_factory=dict)
    ms: float = 0.0
    shared_account: bool = False  # whether trades, positions and account figures were included

    @property
    def level(self) -> str:
        return "info" if self.ok else "warning"


@dataclass(frozen=True, kw_only=True)
class ProcessHeartbeat(Event):
    """A helper process (the analyst) saying it is alive: its load, its background jobs, its status."""

    role: str
    process: dict[str, Any]
    jobs: list[dict[str, Any]]
    status: dict[str, Any] = field(default_factory=dict)


# -- strategy -----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class StrategyEvaluated(Event):
    """One strategy ran on one closed bar, inline or on a Celery worker. side == "" means no signal."""

    strategy: str
    version: int
    symbol: str
    resolution: str
    bar_time: int
    side: str = ""
    reason: str = ""
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    error: str = ""
    ms: float = 0.0
    worker: str = "inline"
    dispatched_at: float = 0.0


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
    LEVEL: ClassVar[str] = "error"
    strategy: str
    symbol: str
    error: str


@dataclass(frozen=True, kw_only=True)
class TradeSkipped(Event):
    LEVEL: ClassVar[str] = "warning"
    strategy: str
    symbol: str
    side: str
    broker: str  # "" when the signal was refused before reaching any broker
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
    stop_loss: float | None
    take_profit: float | None


@dataclass(frozen=True, kw_only=True)
class OrderPlaced(Event):
    client_order_id: str
    order_id: str
    status: str


@dataclass(frozen=True, kw_only=True)
class OrderFailed(Event):
    LEVEL: ClassVar[str] = "error"
    client_order_id: str
    error: str


@dataclass(frozen=True, kw_only=True)
class OrderUnknown(Event):
    """The exchange did not answer clearly. The order is looked up, never resent."""

    LEVEL: ClassVar[str] = "warning"
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


@dataclass(frozen=True, kw_only=True)
class ProtectionTrailed(Event):
    """The guard moved a position's SL/TP after price covered most of the way to the target."""

    broker: str
    symbol: str
    step: int
    max_steps: int
    price: float
    old_stop_loss: float | None
    stop_loss: float
    old_take_profit: float | None
    take_profit: float


@dataclass(frozen=True, kw_only=True)
class DailyLossHalt(Event):
    """A broker lost its daily limit: its positions are closed and it opens nothing more today."""

    LEVEL: ClassVar[str] = "error"
    broker: str
    day: str
    loss_pct: float
    limit_pct: float
    start_equity: float
    equity: float
    closed: list[str]
    errors: list[str]


@dataclass(frozen=True, kw_only=True)
class GuardAlert(Event):
    """The position guard could not do something it should have (e.g. trail a stop)."""

    LEVEL: ClassVar[str] = "error"
    broker: str
    symbol: str
    message: str


# -- control ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ToggleChanged(Event):
    key: str
    enabled: bool


@dataclass(frozen=True, kw_only=True)
class SettingsChanged(Event):
    changed: list[str]  # field names only, never values: some are secrets
    trading_stopped: bool


@dataclass(frozen=True, kw_only=True)
class EmailReport(Event):
    """One attempt to email a day's report. No recipient or SMTP detail: the event log is on the dashboard."""

    day: str
    trigger: str  # "schedule" | "manual"
    ok: bool
    error: str = ""

    @property
    def level(self) -> str:
        return "info" if self.ok else "warning"


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
