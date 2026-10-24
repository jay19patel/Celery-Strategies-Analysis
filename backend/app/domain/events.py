"""Domain event definitions.

Thin dataclasses that travel through the in-process event bus and over
Redis PubSub to connected WebSocket clients. No framework imports —
these are pure domain objects.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """Base class for all events flowing through the system."""

    ts: float = field(default_factory=time.time)
    LEVEL: ClassVar[str] = "info"

    @property
    def event_type(self) -> str:
        """Machine-readable event name, matches the class name."""
        return type(self).__name__

    @property
    def level(self) -> str:
        """Severity level for event log filtering."""
        return self.LEVEL

    def to_dict(self) -> dict[str, Any]:
        """Serialise for JSON transport over WebSocket / Redis."""
        return {"type": self.event_type, "level": self.level, **asdict(self)}


# ---------------------------------------------------------------------------
# Market Events
# ---------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class TickEvent(DomainEvent):
    """A price update from the exchange."""

    symbol: str
    price: float


@dataclass(frozen=True, kw_only=True)
class MarketStatsEvent(DomainEvent):
    """Rolling 24h statistics from the exchange ticker."""

    symbol: str
    last: float | None = None
    mark: float | None = None
    index: float | None = None
    open_24h: float | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    change_24h_pct: float | None = None
    volume_24h: float | None = None
    turnover_24h_usd: float | None = None
    oi_usd: float | None = None
    funding_rate_pct: float | None = None
    bid: float | None = None
    ask: float | None = None


@dataclass(frozen=True, kw_only=True)
class CandleClosedEvent(DomainEvent):
    """A bar has closed — either by stream roll-over or by clock."""

    symbol: str
    resolution: str
    bar_time: int
    source: str  # "websocket" | "clock"
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None


@dataclass(frozen=True, kw_only=True)
class FeedStatusEvent(DomainEvent):
    """Market data feed connected or disconnected."""

    LEVEL: ClassVar[str] = "warning"
    connected: bool
    symbols: list[str] = field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------------------
# Trading Events
# ---------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class SignalGeneratedEvent(DomainEvent):
    """A strategy produced a trading signal."""

    strategy: str
    symbol: str
    side: str
    price: float
    confidence: float = 0.0
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class OrderPlacedEvent(DomainEvent):
    """An order was successfully placed with a broker."""

    client_order_id: str
    broker: str
    symbol: str
    side: str
    size: int
    price: float
    strategy: str


@dataclass(frozen=True, kw_only=True)
class OrderFailedEvent(DomainEvent):
    """An order placement was attempted but failed."""

    LEVEL: ClassVar[str] = "error"
    client_order_id: str
    broker: str
    symbol: str
    error: str
    strategy: str


@dataclass(frozen=True, kw_only=True)
class TradeSkippedEvent(DomainEvent):
    """A signal was produced but the trade was not placed (risk, toggle, etc.)."""

    LEVEL: ClassVar[str] = "warning"
    strategy: str
    symbol: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class PositionClosedEvent(DomainEvent):
    """A position was closed — paper or live."""

    broker: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    pnl: float
    reason: str


@dataclass(frozen=True, kw_only=True)
class PositionUpdateEvent(DomainEvent):
    """A live position was updated by the exchange."""

    symbol: str
    side: str
    size: float
    entry_price: float
    unrealized_pnl: float


@dataclass(frozen=True, kw_only=True)
class OrderUpdateEvent(DomainEvent):
    """An order status change from the exchange."""

    order_id: str
    symbol: str
    status: str
    filled_size: float = 0.0


# ---------------------------------------------------------------------------
# Strategy Events
# ---------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class StrategyErrorEvent(DomainEvent):
    """A strategy raised an unhandled exception during evaluation."""

    LEVEL: ClassVar[str] = "error"
    strategy: str
    symbol: str
    error: str


# ---------------------------------------------------------------------------
# System Events
# ---------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class SystemMetricsEvent(DomainEvent):
    """Periodic system health snapshot."""

    cpu_pct: float
    memory_mb: float
    loop_lag_ms: float | None = None
    ws_connections: int = 0
    events_per_sec: float = 0.0


@dataclass(frozen=True, kw_only=True)
class RiskHaltEvent(DomainEvent):
    """Daily loss limit was hit — trading is halted."""

    LEVEL: ClassVar[str] = "error"
    broker: str
    day: str
    reason: str


# ---------------------------------------------------------------------------
# Registry for deserialisation
# ---------------------------------------------------------------------------

EVENT_TYPES: dict[str, type[DomainEvent]] = {
    cls.__name__: cls
    for cls in [
        TickEvent,
        MarketStatsEvent,
        CandleClosedEvent,
        FeedStatusEvent,
        SignalGeneratedEvent,
        OrderPlacedEvent,
        OrderFailedEvent,
        TradeSkippedEvent,
        PositionClosedEvent,
        PositionUpdateEvent,
        OrderUpdateEvent,
        StrategyErrorEvent,
        SystemMetricsEvent,
        RiskHaltEvent,
    ]
}
