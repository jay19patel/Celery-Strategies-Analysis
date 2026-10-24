"""What a broker does: execute one option leg, look an order up, report the account.

Structures — which legs belong together, their P&L, when they close — are the trading service's,
recorded the same way for every broker. A broker never decides anything; it fills or refuses.
Every broker is idempotent on `client_order_id` and raises `BrokerTimeout` (never `BrokerError`)
when it cannot tell whether an order exists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Account:
    broker: str
    currency: str
    equity: float  # cash plus open positions marked to market
    available: float  # free for a new structure's margin
    margin_used: float
    cash: float
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LegOrder:
    client_order_id: str
    symbol: str
    side: str  # buy | sell
    size: int
    limit_price: float  # the worst price accepted
    contract_value: float  # underlying per contract
    reduce_only: bool = False


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    size: int
    price: float
    fee: float
    exchange_id: str = ""


class Broker(Protocol):
    name: str

    def not_ready(self) -> str:
        """Why this broker cannot take orders right now, or ""."""
        ...

    async def account(self) -> Account: ...

    async def execute(self, order: LegOrder) -> Fill:
        """Fill now at `limit_price` or better, or raise BrokerError. A partial fill returns the filled size."""
        ...

    async def lookup(self, client_order_id: str) -> Fill | None:
        """The fill of an order sent earlier, or None when the broker never filled it."""
        ...

    async def settle(self, order: LegOrder, price: float) -> Fill:
        """A leg that reached expiry, settled at its intrinsic value `price`."""
        ...
