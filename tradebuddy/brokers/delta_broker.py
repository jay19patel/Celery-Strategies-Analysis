"""Delta Exchange as a Broker."""

from __future__ import annotations

import uuid
from typing import Any

from tradebuddy.brokers.base import Account, Position
from tradebuddy.delta import DeltaClient
from tradebuddy.errors import BrokerError


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class DeltaBroker:
    name = "delta"

    def __init__(self, client: DeltaClient) -> None:
        self.client = client

    def not_ready(self) -> str:
        return "" if self.client.has_credentials else "Delta API key and secret are not set (Settings)"

    async def place_order(self, symbol, side, size, client_order_id, stop_loss=None, take_profit=None, strategy=""):
        return await self.client.place_order(symbol, side, size, client_order_id, stop_loss, take_profit)

    async def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        return await self.client.order_by_client_id(client_order_id)

    async def positions(self) -> list[Position]:
        out = []
        for p in await self.client.positions():
            symbol = p.get("product_symbol") or (p.get("product") or {}).get("symbol") or ""
            size = _num(p.get("size"))
            entry, mark = _num(p.get("entry_price")), _num(p.get("mark_price"))
            contract_value = _num((p.get("product") or {}).get("contract_value"), 0.0)
            if not contract_value:
                contract_value = _num((await self.client.product(symbol)).get("contract_value"), 1.0)
            upnl = p.get("unrealized_pnl")
            out.append(
                Position(
                    broker=self.name,
                    symbol=symbol,
                    side="long" if size > 0 else "short",
                    size=abs(size),
                    entry_price=entry,
                    mark_price=mark,
                    unrealized_pnl=_num(upnl) if upnl is not None else (mark - entry) * size * contract_value,
                    margin=_num(p.get("margin")),
                    liquidation_price=_num(p.get("liquidation_price")) or None,
                )
            )
        return out

    async def account(self) -> Account:
        balances = await self.client.balances()
        usd = next((b for b in balances if b.get("asset_symbol") in ("USD", "USDT")), balances[0] if balances else {})
        positions = await self.positions()
        return Account(
            broker=self.name,
            currency=str(usd.get("asset_symbol") or "USD"),
            balance=_num(usd.get("balance")),
            available=_num(usd.get("available_balance")),
            margin_used=_num(usd.get("position_margin")) + _num(usd.get("order_margin")),
            unrealized_pnl=sum(p.unrealized_pnl for p in positions),
            extra={
                "assets": [
                    {"asset": b.get("asset_symbol"), "balance": _num(b.get("balance")), "available": _num(b.get("available_balance"))}
                    for b in balances
                    if _num(b.get("balance"))
                ]
            },
        )

    async def open_orders(self) -> list[dict[str, Any]]:
        return [
            {
                "id": str(o.get("id")),
                "client_order_id": o.get("client_order_id"),
                "symbol": o.get("product_symbol"),
                "side": o.get("side"),
                "type": o.get("stop_order_type") or o.get("order_type"),
                "size": _num(o.get("size")),
                "price": _num(o.get("stop_price") or o.get("limit_price")) or None,
                "state": o.get("state"),
            }
            for o in await self.client.open_orders()
        ]

    async def update_protection(self, symbol: str, stop_loss: float, take_profit: float) -> None:
        await self.client.update_position_protection(symbol, stop_loss=stop_loss or None, take_profit=take_profit or None)

    async def close_position(self, symbol: str) -> dict[str, Any]:
        position = next((p for p in await self.positions() if p.symbol == symbol), None)
        if position is None:
            raise BrokerError(f"no open {symbol} position")
        side = "sell" if position.side == "long" else "buy"
        await self.client.place_order(symbol, side, int(position.size), f"close-{uuid.uuid4().hex[:24]}", reduce_only=True)
        return {"closed": [symbol], "errors": []}

    async def close_all(self) -> dict[str, Any]:
        return await self.client.close_all()
