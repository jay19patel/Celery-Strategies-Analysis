"""SignalGenerated | manual order -> gate -> OrderRequested -> broker -> OrderPlaced | OrderFailed | OrderUnknown."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from tradebuddy import structures
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
from tradebuddy.jobs import NULL_JOB
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
STRUCTURE_BROKERS = ("paper",)  # brokers that can hold option structures


def manual_order_id(broker: str, request_id: str) -> str:
    """The dashboard sends one request_id per order ticket, so a retried or double-clicked submit is one order."""
    digest = hashlib.sha256(f"{broker}|{MANUAL}|{request_id}".encode()).hexdigest()
    return f"tbm{digest[:29]}"


leg_order_id = structures.leg_order_id


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

    async def manual_structure(
        self, broker: str, symbol: str, kind: str, qty: int, request_id: str, sl_pct: float = structures.DEFAULT_SL_PCT,
        tp_pct: float | None = None, expiry: float | None = None, legs: list[str] | None = None,
        picks: list[dict[str, Any]] | None = None, strategy: str = MANUAL,
    ) -> dict[str, Any]:
        """An option structure from the dashboard (straddle, strangle, iron condor, spreads) on the options of
        `symbol`'s underlying. Same gate as a manual order, plus a fresh chain; the legs are rebuilt here from
        the live chain, and when they differ from `legs` (what the person reviewed) nothing is placed.
        Paper only for now. Raises TradeRefused."""

        def refuse(reason: str) -> TradeRefused:
            self.bus.publish(TradeSkipped(strategy=strategy, symbol=symbol, side=kind, broker=broker, reason=reason))
            return TradeRefused(reason)

        if broker not in STRUCTURE_BROKERS:
            raise refuse(f"option structures run on {', '.join(STRUCTURE_BROKERS)} only for now")
        if broker not in self.settings().active_brokers:
            raise refuse(f"{broker} is not an active broker")
        if kind not in structures.KINDS:
            raise refuse(f"unknown structure {kind!r}")
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= 1000:
            raise refuse("quantity must be a whole number of contracts per leg, 1 to 1000")
        if not sl_pct or not 5 <= sl_pct <= 100:
            raise refuse("a stop loss of 5-100% of the max loss is required: entries are always protected")
        if tp_pct is not None and not 5 <= tp_pct <= 500:
            raise refuse("take profit must be 5-500% of the premium")
        if not request_id:
            raise refuse("request_id is required")

        cid = manual_order_id(broker, request_id)
        b = self.brokers[broker]
        book = b.options
        if book.by_client_id(cid) or self.store.order(leg_order_id(cid, 0)):
            return self._structure_result(book, cid)  # the same ticket submitted again
        async with self._lock:
            if not self.store.enabled(trading_key(broker), default=TRADING_DEFAULT[broker]):
                raise refuse("trading is switched off")
            if reason := self.halt_reason(broker) or b.not_ready():
                raise refuse(reason)
            summary = book.snapshot(symbol)
            if not structures.fresh(summary, time.time()):
                raise refuse(f"no fresh options chain for {symbol} (none in the last {structures.FRESH_SECONDS:.0f}s)")
            if open_one := book.open_on(symbol):
                raise refuse(f"an option structure on {symbol} is already open: {open_one['label']}")
            s = self.settings()
            try:
                built = structures.build(kind, summary, qty, expiry=expiry, slippage_pct=s.paper_slippage_pct, picks=picks)
                spec = await b.specs(built["legs"][0]["symbol"])
                cv = float(spec.get("contract_value") or 0)
                if cv <= 0:
                    raise refuse(f"no contract value for {built['legs'][0]['symbol']}")
                built = structures.build(kind, summary, qty, cv, expiry=expiry, slippage_pct=s.paper_slippage_pct, picks=picks)
            except structures.StructureError as exc:
                raise refuse(str(exc)) from exc
            except BrokerError as exc:
                raise refuse(f"could not read the option contract: {exc}") from exc
            if legs is not None and [leg["symbol"] for leg in built["legs"]] != list(legs):
                raise refuse("the chain moved since the preview: review the new legs and place again")
            if tp_pct is None:
                tp_pct = structures.DEFAULT_TP_PCT[built["type"]]
            try:
                account = await b.account()
            except BrokerError as exc:
                raise refuse(f"could not read the account: {exc}") from exc

            # One order per leg, as an exchange takes them: buys before sells, so no short leg is ever uncovered.
            orders = []
            for i in structures.execution_order(built["legs"]):
                leg = built["legs"][i]
                leg["client_order_id"] = leg_order_id(cid, i)
                orders.append({
                    "client_order_id": leg["client_order_id"], "broker": broker, "strategy": strategy, "symbol": leg["symbol"],
                    "side": leg["action"], "size": qty, "price": leg["price"], "stop_loss": None, "take_profit": None,
                })
            if not self.store.reserve_order(**orders[0]):
                return self._structure_result(book, cid)  # a double click that got past the first check
            for order in orders[1:]:
                self.store.reserve_order(**order)
            try:
                row = book.open(cid, symbol, built, float(sl_pct), float(tp_pct), strategy, account.available)
            except BrokerError as exc:
                for order in orders:
                    self.store.update_order(order["client_order_id"], "rejected", error=str(exc))
                    self.bus.publish(OrderFailed(client_order_id=order["client_order_id"], error=str(exc)))
                raise refuse(str(exc)) from exc
            for n, order in enumerate(orders, 1):
                order_id = f"paper-opt-{row['id']}-{n}"
                self.store.update_order(order["client_order_id"], "filled", order_id=order_id)
                self.bus.publish(OrderPlaced(client_order_id=order["client_order_id"], order_id=order_id, status="filled"))
        return self._structure_result(book, cid)

    def _structure_result(self, book: Any, cid: str) -> dict[str, Any]:
        row = book.by_client_id(cid)
        legs = row["legs"] if row else []
        return {
            "client_order_id": cid, "structure_id": row["id"] if row else None, "label": row["label"] if row else "",
            "status": "filled" if row else "closed",
            "orders": [self.store.order(legs[i]["client_order_id"]) for i in structures.execution_order(legs) if legs[i].get("client_order_id")],
        }

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


AUTO_OPTIONS = "auto_options"  # the strategy name auto-traded structures are recorded under
AUTO_EVERY = 60.0
AUTO_COOLDOWN = 30 * 60  # after an entry, a close or a refusal on a symbol
AUTO_MAX_PER_DAY = 3  # entries per symbol per trading day


class AutoStructures:
    """Options auto-trading on paper: every minute, for each underlying with a fresh chain and nothing open,
    the structure `structures.suggest` picks from the options market (IV rank, OI, term structure) is
    opened through the same gate as the dashboard ticket, with the small SL/TP from Settings.

    Rules only: no AI and no analyst output decides here (analysis never trades). One structure per
    underlying, a cooldown after each entry, close or refusal, and at most AUTO_MAX_PER_DAY entries a day.
    The request id is the symbol, the day and the entry's number that day, so a repeated pass can never
    open the same entry twice. Off until options_auto_enabled; the kill switch and the daily loss limit stop it."""

    def __init__(
        self, trader: Trader, store: Store, settings: Callable[[], Settings],
        snapshots: Callable[[], dict[str, dict[str, Any]]], history: Callable[[str, float], list[dict[str, Any]]],
    ) -> None:
        self.trader = trader
        self.store = store
        self.settings = settings
        self.snapshots = snapshots  # perpetual symbol -> latest options summary
        self.history = history  # (underlying, since) -> stored options rows
        self.job = NULL_JOB
        self.status: dict[str, str] = {}  # symbol -> what the last pass did, for the dashboard
        self._quiet_until: dict[str, float] = {}

    async def run(self) -> None:
        while True:
            try:
                with self.job.tick() as job:
                    job.note = await self.check()
            except Exception:
                log.exception("auto_options_failed")
            await asyncio.sleep(AUTO_EVERY)

    async def check(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        s = self.settings()
        why = ""
        if not s.options_auto_enabled:
            why = "off"
        elif "paper" not in s.active_brokers:
            why = "paper is not active"
        elif not self.store.enabled(trading_key("paper"), default=TRADING_DEFAULT["paper"]):
            why = "paper trading is switched off"
        elif halted := self.trader.halt_reason("paper"):
            why = f"halted: {halted}"
        if why:
            self.status = {}
            return why
        self.status = {symbol: await self._one(symbol, summary, s, now) for symbol, summary in sorted(self.snapshots().items())}
        return "; ".join(f"{k}: {v}" for k, v in self.status.items()) or "no options data yet"

    async def _one(self, symbol: str, summary: dict[str, Any], s: Settings, now: float) -> str:
        book = self.trader.brokers["paper"].options
        if held := book.open_on(symbol):
            return f"holding {held['label']}"
        day_start = datetime.fromtimestamp(now, ZoneInfo(s.day_timezone)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        entries, last_close = book.entries(symbol, summary.get("underlying", ""), AUTO_OPTIONS, day_start)
        quiet = max(self._quiet_until.get(symbol, 0.0), (last_close or 0.0) + AUTO_COOLDOWN)
        if now < quiet:
            return f"cooling down until {datetime.fromtimestamp(quiet, ZoneInfo(s.day_timezone)):%H:%M}"
        if entries >= AUTO_MAX_PER_DAY:
            return f"{entries} entries today, the daily maximum"
        if not structures.fresh(summary, now):
            return "options chain is stale"
        history = await asyncio.to_thread(self.history, summary["underlying"], now - 7 * 86_400)
        pick = structures.suggest(summary, None, history)
        if not pick.get("kind"):
            return f"no edge: {pick['reason'][-120:]}"
        day = datetime.fromtimestamp(now, ZoneInfo(s.day_timezone)).date().isoformat()
        request_id = f"auto|{symbol}|{day}|{entries + 1}"  # the day's nth entry: a repeated pass is the same order
        self._quiet_until[symbol] = now + AUTO_COOLDOWN
        try:
            result = await self.trader.manual_structure(
                "paper", symbol, pick["kind"], s.options_auto_qty, request_id, s.options_auto_sl_pct, s.options_auto_tp_pct,
                strategy=AUTO_OPTIONS,
            )
        except TradeRefused as exc:
            return f"refused: {exc}"
        log.info("auto_options_opened symbol=%s structure=%s reason=%s", symbol, result["label"], pick["reason"])
        return f"opened {result['label']}" if result["status"] == "filled" else "already placed this entry"
