"""Delta Exchange India as a broker: each leg an immediate-or-cancel limit order on the exchange."""

from __future__ import annotations

from typing import Any

from app.brokers.base import Account, Fill, LegOrder
from app.errors import BrokerError, BrokerTimeout
from app.market.delta import DeltaClient


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_fill(client_order_id: str, raw: dict[str, Any]) -> Fill | None:
    """An order as Delta returns it, as a fill — None when nothing of it filled."""
    size = int(_num(raw.get("size")))
    filled = size - int(_num(raw.get("unfilled_size")))
    if filled <= 0:
        return None
    return Fill(
        client_order_id=client_order_id, size=filled,
        price=_num(raw.get("average_fill_price")) or _num(raw.get("limit_price")),
        fee=_num(raw.get("paid_commission")), exchange_id=str(raw.get("id") or ""),
    )


class DeltaBroker:
    name = "delta"

    def __init__(self, client: DeltaClient) -> None:
        self.client = client

    def not_ready(self) -> str:
        return "" if self.client.has_credentials else "the Delta API key and secret are not set (Settings)"

    async def account(self) -> Account:
        balances = await self.client.balances()
        usd = next((b for b in balances if b.get("asset_symbol") in ("USD", "USDT")), balances[0] if balances else {})
        balance = _num(usd.get("balance"))
        available = _num(usd.get("available_balance"))
        margin = _num(usd.get("position_margin")) + _num(usd.get("order_margin"))
        positions = await self.client.positions()
        upnl = sum(_num(p.get("unrealized_pnl")) for p in positions)
        return Account(
            broker=self.name, currency=str(usd.get("asset_symbol") or "USD"), equity=balance + upnl, available=available,
            margin_used=margin, cash=balance,
            extra={"assets": [{"asset": b.get("asset_symbol"), "balance": _num(b.get("balance"))} for b in balances if _num(b.get("balance"))]},
        )

    async def leverage(self, symbol: str) -> float:
        """The account's order leverage on this product, as set on the exchange. Unreadable, 1x:
        margin is then over-counted, never under-counted."""
        try:
            return max(1.0, _num(await self.client.order_leverage(symbol), 1.0))
        except (BrokerError, BrokerTimeout):
            return 1.0

    async def positions(self) -> dict[str, int]:
        """Signed contracts held per symbol (positive long), as the exchange reports them."""
        return {str(p.get("product_symbol")): int(_num(p.get("size"))) for p in await self.client.positions() if p.get("product_symbol")}

    async def execute(self, order: LegOrder) -> Fill:
        raw = await self.client.place_order(order.symbol, order.side, order.size, order.client_order_id, order.limit_price, order.reduce_only)
        fill = parse_fill(order.client_order_id, raw or {})
        if fill is None:
            raise BrokerError(f"delta: {order.symbol} {order.side} did not fill at {order.limit_price:,.2f} or better")
        return fill

    async def lookup(self, client_order_id: str) -> Fill | None:
        raw = await self.client.order_by_client_id(client_order_id)
        return parse_fill(client_order_id, raw) if raw else None

    async def protect(self, symbol: str, side: str, size: int, stop_price: float, client_order_id: str) -> str:
        raw = await self.client.place_stop_order(symbol, side, size, client_order_id, stop_price)
        order_id = str((raw or {}).get("id") or "")
        if not order_id:
            raise BrokerError(f"delta: no id for the stop on {symbol}")
        return order_id

    async def unprotect(self, symbol: str, order_id: str) -> None:
        await self.client.cancel_order(symbol, order_id)

    async def settle(self, order: LegOrder, price: float) -> Fill:
        # The exchange settles an expired option itself; this only records what it paid.
        return Fill(order.client_order_id, order.size, price, 0.0, "settlement")
