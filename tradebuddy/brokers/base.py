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


def protection_error(side: str, price: float, stop_loss: float | None, take_profit: float | None) -> str:
    """Why these SL/TP levels cannot protect a position opened on `side` ("buy"/"long" or "sell"/"short") at `price`, or ""."""
    long = side in ("buy", "long")
    for name, level in (("stop loss", stop_loss), ("take profit", take_profit)):
        if level is not None and level <= 0:
            return f"{name} must be a positive price"
    if stop_loss is not None and ((stop_loss >= price) if long else (stop_loss <= price)):
        return f"a {'long' if long else 'short'} stop loss must be {'below' if long else 'above'} the price {price:g}"
    if take_profit is not None and ((take_profit <= price) if long else (take_profit >= price)):
        return f"a {'long' if long else 'short'} take profit must be {'above' if long else 'below'} the price {price:g}"
    return ""


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
    async def update_protection(self, symbol: str, stop_loss: float, take_profit: float | None) -> None:
        """Replace the position's SL/TP. Never leaves the position without a stop: on failure the old one stays."""
        ...

    async def close_all(self) -> dict[str, Any]: ...
    async def size_for_margin(self, symbol: str, price: float, margin: float) -> int:
        """Whole contracts that `margin` pays for at `price`; 0 when it buys none. Raises BrokerError when it cannot tell."""
        ...
