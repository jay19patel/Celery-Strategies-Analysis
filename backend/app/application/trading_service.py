"""Trading service — order placement and position management."""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.application.base_service import BaseService
from app.domain.exceptions import NotFoundError, TradeRefusedError
from app.domain.models import Order, PositionControl
from app.infrastructure.database.repositories.order_repo import OrderRepository
from app.infrastructure.database.repositories.position_repo import PositionRepository
from app.infrastructure.redis.pubsub import EventPublisher


class TradingService(BaseService):
    """Orchestrates order lifecycle from request to execution."""

    def __init__(
        self,
        order_repo: OrderRepository,
        position_repo: PositionRepository,
        publisher: EventPublisher,
    ) -> None:
        super().__init__(publisher)
        self._orders = order_repo
        self._positions = position_repo

    async def place_order(
        self,
        symbol: str,
        side: str,
        size: int,
        price: float,
        strategy: str = "manual",
        broker: str = "paper",
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> dict[str, Any]:
        """Place a new order.

        Raises:
            TradeRefusedError: If inputs are invalid.
        """
        if side not in ("buy", "sell"):
            raise TradeRefusedError(f"Invalid side: {side}")
        if size <= 0:
            raise TradeRefusedError("Size must be positive")
        if price <= 0:
            raise TradeRefusedError("Price must be positive")

        client_order_id = f"{strategy}_{symbol}_{uuid.uuid4().hex[:8]}"

        order = Order(
            client_order_id=client_order_id,
            ts=time.time(),
            broker=broker,
            strategy=strategy,
            symbol=symbol,
            side=side,
            size=size,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status="pending",
        )
        self._orders.create(order)

        await self._publish("OrderPlaced", {
            "client_order_id": client_order_id,
            "broker": broker,
            "symbol": symbol,
            "side": side,
            "size": size,
            "price": price,
            "strategy": strategy,
        })

        self._info("order_placed", id=client_order_id, symbol=symbol, side=side)
        return {"client_order_id": client_order_id, "status": "pending", "symbol": symbol, "side": side}

    def get_orders(self, limit: int = 100, broker: str | None = None) -> list[dict[str, Any]]:
        """List recent orders."""
        return [
            {
                "client_order_id": o.client_order_id, "ts": o.ts, "broker": o.broker,
                "strategy": o.strategy, "symbol": o.symbol, "side": o.side,
                "size": o.size, "price": o.price, "stop_loss": o.stop_loss,
                "take_profit": o.take_profit, "status": o.status,
                "order_id": o.order_id, "error": o.error,
            }
            for o in self._orders.list_recent(limit=limit, broker=broker)
        ]

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Orders not yet in a terminal state."""
        return [
            {
                "client_order_id": o.client_order_id, "ts": o.ts, "broker": o.broker,
                "symbol": o.symbol, "side": o.side, "size": o.size,
                "price": o.price, "status": o.status,
            }
            for o in self._orders.list_open()
        ]

    def get_positions(self) -> list[dict[str, Any]]:
        """All open paper positions."""
        return [
            {
                "symbol": p.symbol, "strategy": p.strategy, "side": p.side,
                "size": p.size, "entry_price": p.entry_price, "leverage": p.leverage,
                "margin": p.margin, "stop_loss": p.stop_loss, "take_profit": p.take_profit,
                "opened_at": p.opened_at, "mae_pct": p.mae_pct, "mfe_pct": p.mfe_pct,
            }
            for p in self._positions.get_all_open()
        ]

    async def close_position(self, symbol: str, reason: str = "manual") -> dict[str, Any]:
        """Close a specific position.

        Raises:
            NotFoundError: If no position exists for the symbol.
        """
        position = self._positions.get_by_symbol(symbol)
        if not position:
            raise NotFoundError(f"No open position for {symbol}")

        self._positions.close(symbol)

        await self._publish("PositionClosed", {
            "broker": "paper", "symbol": symbol,
            "side": position.side, "entry_price": position.entry_price,
            "reason": reason,
        })

        self._info("position_closed", symbol=symbol, reason=reason)
        return {"symbol": symbol, "closed": True, "reason": reason}

    async def close_all_positions(self) -> list[dict[str, Any]]:
        """Close every open position."""
        return [
            await self.close_position(p.symbol, reason="close_all")
            for p in self._positions.get_all_open()
        ]

    def set_position_control(self, broker: str, symbol: str, trailing: bool, max_steps: int) -> dict[str, Any]:
        """Update trailing-stop config for a position."""
        control = PositionControl(
            broker=broker, symbol=symbol,
            position=f"{broker}:{symbol}",
            trailing=trailing, max_steps=max_steps,
            updated_at=time.time(),
        )
        self._positions.upsert_control(control)
        self._info("control_set", broker=broker, symbol=symbol, trailing=trailing)
        return {"broker": broker, "symbol": symbol, "trailing": trailing, "max_steps": max_steps}
