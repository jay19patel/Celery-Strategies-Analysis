"""SignalGenerated | manual order -> gate -> OrderRequested -> broker -> OrderPlaced | OrderFailed | OrderUnknown."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from typing import Any

from tradebuddy.brokers import Broker
from tradebuddy.brokers.base import protection_error
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
from tradebuddy.store import ACTIVE_STATUSES, Store
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


MANUAL = "manual"  # the strategy name orders placed from the dashboard are recorded under


def manual_order_id(broker: str, request_id: str) -> str:
    """The dashboard sends one request_id per order ticket, so a retried or double-clicked submit is one order."""
    digest = hashlib.sha256(f"{broker}|{MANUAL}|{request_id}".encode()).hexdigest()
    return f"tbm{digest[:29]}"


class TradeRefused(Exception):
    """A manual order the gate turned down. Also published as TradeSkipped."""


class Trader:
    """Fans each signal out to every active broker and decides, per broker, whether it becomes an order.
    Every refusal is published with its reason."""

    def __init__(
        self, bus: EventBus, store: Store, brokers: dict[str, Broker], prices: PriceBook, settings: Callable[[], Settings],
        halt_reason: Callable[[str], str] = lambda _broker: "",
    ) -> None:
        self.bus = bus
        self.halt_reason = halt_reason
        self.store = store
        self.brokers = brokers
        self.prices = prices
        self.settings = settings
        # Gate -> reserve must not interleave between signals and manual orders, or both could
        # pass the "no open position" check for the same symbol.
        self._lock = asyncio.Lock()

    async def on_signal(self, e: SignalGenerated) -> None:
        s = self.settings()
        if not self.store.enabled(strategy_key(e.strategy)):
            self._skip(e, "", "strategy is switched off")
            return
        if not self.store.enabled(pair_key(e.strategy, e.symbol)):
            self._skip(e, "", f"{e.symbol} is switched off for this strategy")
            return
        for name in s.active_brokers:
            async with self._lock:
                await self._route(e, self.brokers[name], s)

    async def _route(self, e: SignalGenerated, broker: Broker, s: Settings) -> None:
        reason = await self._blocked(broker, e.symbol)
        if reason:
            self._skip(e, broker.name, reason)
            return

        price = self.prices.price(e.symbol)
        assert price is not None  # checked in _blocked
        sl_pct = e.stop_loss_pct or s.stop_loss_pct
        tp_pct = e.take_profit_pct or s.take_profit_pct
        direction = 1 if e.side == "buy" else -1
        try:
            account = await broker.account()
            target_margin = account.available * s.trade_margin_pct / 100.0
            size = await broker.size_for_margin(e.symbol, price, target_margin)
        except BrokerError as exc:
            self._skip(e, broker.name, f"could not size the order: {exc}")
            return
        if size < 1:
            self._skip(e, broker.name, f"{s.trade_margin_pct:g}% of available margin ({target_margin:.2f}) buys no whole contract")
            return

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

    async def manual(
        self, broker: str, symbol: str, side: str, size: int | None, stop_loss: float | None, take_profit: float | None,
        request_id: str, margin_pct: float | None = None,
    ) -> dict[str, Any]:
        """An order from the dashboard. It passes the same gate as a signal (trading switch, daily limit,
        fresh price, nothing in flight, no open position read from the broker) and is sent by the Executor,
        so it is recorded, idempotent on request_id, and a timeout is looked up, never resent.

        Sized either in contracts (`size`) or as `margin_pct` of this broker's available margin — the
        latter is how one ticket opens the same-sized position, in % of capital, on paper and Delta.
        Raises TradeRefused."""

        def refuse(reason: str) -> TradeRefused:
            self.bus.publish(TradeSkipped(strategy=MANUAL, symbol=symbol, side=side, broker=broker, reason=reason))
            return TradeRefused(reason)

        if broker not in self.settings().active_brokers:
            raise refuse(f"{broker} is not an active broker")
        if side not in ("buy", "sell"):
            raise refuse(f"side must be buy or sell, not {side!r}")
        if (size is None) == (margin_pct is None):
            raise refuse("give either size (contracts) or margin_pct, not both")
        if margin_pct is not None and not 0 < margin_pct <= 100:
            raise refuse(f"margin_pct must be above 0 and at most 100 (got {margin_pct!r})")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int | float) or size != int(size) or size < 1):
            raise refuse(f"size must be a whole number of contracts, at least 1 (got {size!r})")
        if not stop_loss:
            raise refuse("a stop loss is required: entries are always protected")
        if not request_id:
            raise refuse("request_id is required")

        cid = manual_order_id(broker, request_id)
        if existing := self.store.order(cid):
            return existing  # the same ticket submitted again
        async with self._lock:
            b = self.brokers[broker]
            reason = await self._blocked(b, symbol)
            price = self.prices.price(symbol)
            if not reason and price is not None:
                reason = protection_error(side, price, stop_loss, take_profit)
            if reason:
                raise refuse(reason)
            if margin_pct is not None:
                try:
                    account = await b.account()
                    size = await b.size_for_margin(symbol, price, account.available * margin_pct / 100)
                except BrokerError as exc:
                    raise refuse(f"could not size the order: {exc}") from exc
                if size < 1:
                    raise refuse(f"{margin_pct:g}% of available margin ({account.available:.2f}) buys no whole contract")
            order = {
                "client_order_id": cid, "broker": broker, "strategy": MANUAL, "symbol": symbol, "side": side,
                "size": int(size), "price": price, "stop_loss": stop_loss, "take_profit": take_profit,
            }
            if self.store.reserve_order(**order):
                self.bus.publish(OrderRequested(**order))
        return self.store.order(cid) or order

    async def _blocked(self, broker: Broker, symbol: str) -> str:
        if not self.store.enabled(trading_key(broker.name), default=TRADING_DEFAULT[broker.name]):
            return "trading is switched off"
        if reason := self.halt_reason(broker.name):
            return reason
        if reason := broker.not_ready():
            return reason
        if self.prices.price(symbol) is None:
            return "no fresh live price from the WebSocket"
        if active := self.store.active_order_for(broker.name, symbol):
            return f"order {active['client_order_id']} on {symbol} is still {active['status']}"
        try:
            positions = await broker.positions()
        except BrokerError as exc:
            return f"could not read positions: {exc}"
        if any(p.symbol == symbol for p in positions):
            return f"a {symbol} position is already open"
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
            self._schedule_lookup(broker, e.client_order_id)
        except BrokerError as exc:
            self._failed(e.client_order_id, str(exc))
        else:
            self._placed(e.client_order_id, raw)

    async def on_order_update(self, e: OrderUpdate) -> None:
        if e.client_order_id and self.store.order(e.client_order_id):
            self.store.update_order(e.client_order_id, STATUS.get(e.state, e.state or "open"), order_id=e.order_id)

    async def reconcile(self, before: float) -> None:
        """At start: orders a previous run left pending, unknown or open are asked of their broker.
        Only orders reserved before `before`, so nothing this run is sending is touched."""
        for row in self.store.orders_in(ACTIVE_STATUSES, before=before):
            cid, broker = row["client_order_id"], self.brokers.get(row["broker"])
            if broker is None:
                continue
            try:
                raw = await broker.order_by_client_id(cid)
            except BrokerError as exc:
                log.warning("reconcile_lookup_failed client_order_id=%s error=%s", cid, exc)
                self._schedule_lookup(broker, cid)
                continue
            if raw is None:
                self._failed(cid, f"{row['status']} before a restart and unknown to the broker: never placed")
            else:
                self._placed(cid, raw)

    def _schedule_lookup(self, broker: Broker, cid: str) -> None:
        task = asyncio.create_task(self._look_up(broker, cid))
        self._lookups.add(task)
        task.add_done_callback(self._lookups.discard)

    async def _look_up(self, broker: Broker, cid: str) -> None:
        for delay in self.lookup_delays:
            await asyncio.sleep(delay)
            if (self.store.order(cid) or {}).get("status") not in ("unknown", "pending"):
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
