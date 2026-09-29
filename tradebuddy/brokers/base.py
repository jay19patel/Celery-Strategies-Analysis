"""The broker interface. Paper and Delta both implement it; nothing else knows which is active."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Position:
    broker: str
    symbol: str
    side: str  # "long" | "short"
    size: float  # contracts
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    margin: float = 0.0
    liquidation_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    opened_at: float | None = None
    strategy: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Account:
    broker: str
    currency: str
    balance: float  # wallet cash
    available: float  # free for new margin
    margin_used: float
    unrealized_pnl: float
    realized_pnl: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def equity(self) -> float:
        return self.balance + self.unrealized_pnl

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "equity": self.equity}


class Broker(Protocol):
    name: str

    def not_ready(self) -> str:
        """Why this broker cannot take orders right now, or "" when it can."""
        ...

    async def place_order(
        self, symbol: str, side: str, size: int, client_order_id: str,
        stop_loss: float | None = None, take_profit: float | None = None, strategy: str = "",
    ) -> dict[str, Any]:
        """Market entry with SL/TP. Returns {"id", "state"}. Raises BrokerError / BrokerTimeout."""
        ...

    async def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None: ...
    async def positions(self) -> list[Position]: ...
    async def account(self) -> Account: ...
    async def open_orders(self) -> list[dict[str, Any]]: ...
    async def close_position(self, symbol: str) -> dict[str, Any]: ...
    async def close_all(self) -> dict[str, Any]: ...
    async def size_for_margin(self, symbol: str, price: float, margin: float) -> int: ...
