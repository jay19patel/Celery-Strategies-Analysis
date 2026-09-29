"""SignalGenerated -> gate -> OrderRequested -> broker -> OrderPlaced | OrderFailed | OrderUnknown."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from typing import Any

from tradebuddy.brokers import Broker
from tradebuddy.errors import BrokerError, BrokerTimeout
from tradebuddy.events import (
    EventBus,
    OrderFailed,
    OrderPlaced,
    OrderRequested,
    OrderUnknown,
    OrderUpdate,
    SignalGenerated,
    TradeSkipped,
)
from tradebuddy.runner import pair_key, strategy_key
from tradebuddy.settings import Settings
from tradebuddy.store import Store
from tradebuddy.stream import PriceBook

log = logging.getLogger(__name__)


def trading_key(broker: str) -> str:
    """Per-broker trading switch in the header."""
    return f"trading:{broker}"


# Paper trades by default; a broker that moves real orders starts off.
TRADING_DEFAULT = {"paper": True, "delta": False}

# Exchange order states -> our order status.
STATUS = {"open": "open", "pending": "open", "closed": "filled", "filled": "filled", "cancelled": "cancelled", "canceled": "cancelled", "rejected": "rejected"}


def client_order_id(broker: str, strategy: str, symbol: str, bar_time: int) -> str:
    """Same broker, strategy, symbol and bar -> same id, so one bar can never open two orders on one broker."""
    digest = hashlib.sha256(f"{broker}|{strategy}|{symbol}|{bar_time}".encode()).hexdigest()
    return f"tb{digest[:30]}"  # Delta allows 32 characters


class Trader:
    """Fans each signal out to every active broker and decides, per broker, whether it becomes an order.
    Every refusal is published with its reason."""

    def __init__(
        self, bus: EventBus, store: Store, brokers: dict[str, Broker], prices: PriceBook, settings: Callable[[], Settings]
    ) -> None:
        self.bus = bus
        self.store = store
        self.brokers = brokers
        self.prices = prices
        self.settings = settings

    async def on_signal(self, e: SignalGenerated) -> None:
        s = self.settings()
        if not self.store.enabled(strategy_key(e.strategy)):
            self._skip(e, "", "strategy is switched off")
            return
        if not self.store.enabled(pair_key(e.strategy, e.symbol)):
            self._skip(e, "", f"{e.symbol} is switched off for this strategy")
            return
        for name in s.active_brokers:
            await self._route(e, self.brokers[name], s)

    async def _route(self, e: SignalGenerated, broker: Broker, s: Settings) -> None:
        reason = await self._blocked(e, broker)
        if reason:
            self._skip(e, broker.name, reason)
            return

        price = self.prices.price(e.symbol)
        assert price is not None  # checked in _blocked
        sl_pct = e.stop_loss_pct or s.stop_loss_pct
        tp_pct = e.take_profit_pct or s.take_profit_pct
        direction = 1 if e.side == "buy" else -1
        account = await broker.account()
        margin_factor = s.trade_margin_pct / 100.0
        target_margin = account.available * margin_factor
        size = await broker.size_for_margin(e.symbol, price, target_margin)

        order = {
            "client_order_id": client_order_id(broker.name, e.strategy, e.symbol, e.bar_time),
            "broker": broker.name,
            "strategy": e.strategy,
            "symbol": e.symbol,
            "side": e.side,
            "size": size,
            "price": price,
            "stop_loss": price * (1 - direction * sl_pct / 100),
            "take_profit": price * (1 + direction * tp_pct / 100),
        }
        if not self.store.reserve_order(**order):
            self._skip(e, broker.name, "an order for this bar was already sent")
            return
        self.bus.publish(OrderRequested(**order))

    async def _blocked(self, e: SignalGenerated, broker: Broker) -> str:
        if not self.store.enabled(trading_key(broker.name), default=TRADING_DEFAULT[broker.name]):
            return "trading is switched off"
        if reason := broker.not_ready():
            return reason
        if self.prices.price(e.symbol) is None:
            return "no fresh live price from the WebSocket"
        if active := self.store.active_order_for(broker.name, e.symbol):
            return f"order {active['client_order_id']} on {e.symbol} is still {active['status']}"
        try:
            positions = await broker.positions()
        except BrokerError as exc:
            return f"could not read positions: {exc}"
        if any(p.symbol == e.symbol for p in positions):
            return f"a {e.symbol} position is already open"
        return ""

    def _skip(self, e: SignalGenerated, broker: str, reason: str) -> None:
        self.bus.publish(TradeSkipped(strategy=e.strategy, symbol=e.symbol, side=e.side, broker=broker, reason=reason))


class Executor:
    """Sends orders to the broker they were reserved for. A timeout is looked up by client_order_id, never resent."""

    def __init__(self, bus: EventBus, store: Store, brokers: dict[str, Broker], lookup_delays: tuple[float, ...] = (3, 10, 30, 60)) -> None:
        self.bus = bus
        self.store = store
        self.brokers = brokers
        self.lookup_delays = lookup_delays
        self._lookups: set[asyncio.Task] = set()

    async def on_order_requested(self, e: OrderRequested) -> None:
        broker = self.brokers[e.broker]
        try:
            raw = await broker.place_order(e.symbol, e.side, e.size, e.client_order_id, e.stop_loss, e.take_profit, strategy=e.strategy)
        except BrokerTimeout as exc:
            self.store.update_order(e.client_order_id, "unknown", error=str(exc))
            self.bus.publish(OrderUnknown(client_order_id=e.client_order_id, error=str(exc)))
            task = asyncio.create_task(self._look_up(broker, e.client_order_id))
            self._lookups.add(task)
            task.add_done_callback(self._lookups.discard)
        except BrokerError as exc:
            self._failed(e.client_order_id, str(exc))
        else:
            self._placed(e.client_order_id, raw)

    async def on_order_update(self, e: OrderUpdate) -> None:
        if e.client_order_id and self.store.order(e.client_order_id):
            self.store.update_order(e.client_order_id, STATUS.get(e.state, e.state or "open"), order_id=e.order_id)

    async def _look_up(self, broker: Broker, cid: str) -> None:
        for delay in self.lookup_delays:
            await asyncio.sleep(delay)
            if (self.store.order(cid) or {}).get("status") != "unknown":
                return  # resolved by the private WebSocket meanwhile
            try:
                raw = await broker.order_by_client_id(cid)
            except BrokerError as exc:
                log.warning("order_lookup_failed client_order_id=%s error=%s", cid, exc)
                continue
            if raw is None:
                self._failed(cid, "broker has no such order after the timeout")
            else:
                self._placed(cid, raw)
            return
        log.error("order_state_unresolved client_order_id=%s — symbol stays blocked until resolved", cid)

    def _placed(self, cid: str, raw: dict[str, Any]) -> None:
        status = STATUS.get(str(raw.get("state", "")), "open")
        self.store.update_order(cid, status, order_id=str(raw.get("id", "")))
        self.bus.publish(OrderPlaced(client_order_id=cid, order_id=str(raw.get("id", "")), status=status))

    def _failed(self, cid: str, error: str) -> None:
        self.store.update_order(cid, "rejected", error=error)
        self.bus.publish(OrderFailed(client_order_id=cid, error=error))
