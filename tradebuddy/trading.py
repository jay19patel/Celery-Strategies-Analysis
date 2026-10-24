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

from tradebuddy.brokers import Account, Broker
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
from tradebuddy.settings import RANGES, Settings
from tradebuddy.store import ACTIVE_STATUSES, Store
from tradebuddy.strategies.base import net_rr
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

# A market context older than this says nothing about the volatility now, so the fixed percentages
# apply instead (invariant 8: absence over staleness).
ATR_MAX_AGE = 1800.0


def atr_pct_from(analysis: dict[str, Any] | None, now: float, max_age: float = ATR_MAX_AGE) -> float | None:
    """The ATR%, as a % of price, in a recent enough MarketAnalysis; None when there is none."""
    if not analysis or now - float(analysis.get("ts") or 0.0) > max_age:
        return None
    atr = (analysis.get("context") or {}).get("atr_pct")
    return float(atr) if isinstance(atr, int | float) and not isinstance(atr, bool) and atr > 0 else None


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
        volatility: Callable[[str], float | None] = lambda _symbol: None,
        margins: dict[str, float] | None = None,
    ) -> None:
        self.bus = bus
        self.halt_reason = halt_reason
        self.store = store
        self.brokers = brokers
        self.prices = prices
        self.settings = settings
        self.volatility = volatility  # symbol -> ATR as a % of price, or None when none is fresh
        # strategy name -> its own margin cap, for the few that declare one. Read from the
        # registry at startup rather than imported here: the Trader must not depend on the
        # strategy package, or every strategy becomes a thing that can break order routing.
        self.margins = margins or {}
        # Gate -> reserve must not interleave between signals and manual orders, or both could
        # pass the "no open position" check for the same symbol.
        self._lock = asyncio.Lock()
        # Signals waiting to be compared against each other, per symbol. See `_settle`.
        self._pending: dict[str, list[SignalGenerated]] = {}
        self._settling: set[str] = set()
        self._settle_tasks: set[asyncio.Task] = set()

    async def on_signal(self, e: SignalGenerated) -> None:
        s = self.settings()
        if not self.store.enabled(strategy_key(e.strategy)):
            self._skip(e, "", "strategy is switched off")
            return
        if not self.store.enabled(pair_key(e.strategy, e.symbol)):
            self._skip(e, "", f"{e.symbol} is switched off for this strategy")
            return
        if s.signal_settle_seconds <= 0:
            await self._send(e, s)
            return
        # Hold the symbol open for a moment so competing signals can be compared. Returning
        # immediately rather than sleeping here on purpose: this handler has the bus's queue
        # behind it, and a handler that waits stops every other symbol's signals too.
        self._pending.setdefault(e.symbol, []).append(e)
        if e.symbol not in self._settling:
            self._settling.add(e.symbol)
            task = asyncio.create_task(self._settle(e.symbol, s.signal_settle_seconds))
            self._settle_tasks.add(task)
            task.add_done_callback(self._settle_tasks.discard)

    async def _settle(self, symbol: str, wait: float) -> None:
        """Route the best signal on `symbol`, and say what each of the others lost to.

        One position per symbol means the strategies are competing for one slot, and until now the
        winner was whichever happened to publish first. That is a race, and it is biased: a
        strategy on a 5-minute bar gets three times the chances of one on a 15-minute bar, so the
        FASTEST strategy wins the slot rather than the one with the best trade. On the old
        deployment one strategy that fired on literally every bar took 78% of all signals that way.

        Ranked on reward-to-risk after costs, which is the only number every strategy produces and
        the only one that is comparable across them."""
        try:
            await asyncio.sleep(wait)
            group = self._pending.pop(symbol, [])
            if not group:
                return
            s = self.settings()
            if len(group) == 1:
                await self._send(group[0], s)
                return
            ranked = sorted(group, key=lambda e: self._rank(e, s), reverse=True)
            best = ranked[0]
            for loser in ranked[1:]:
                self._skip(
                    loser, "",
                    f"{best.strategy} had the better trade on {symbol} this bar "
                    f"({self._rank(best, s):.2f}:1 after costs against {self._rank(loser, s):.2f}:1)",
                )
            await self._send(best, s)
        finally:
            self._settling.discard(symbol)

    def _rank(self, e: SignalGenerated, s: Settings) -> float:
        """Reward over risk after the round trip, on the levels this signal would actually get.

        `levels` is used rather than the signal's own fields because a strategy that leaves them to
        the Trader still has levels — the ATR's or Settings' — and they have to be comparable with
        a strategy that set its own, or the one that names its stop is penalised for doing so."""
        sl_pct, tp_pct, _basis = self.levels(e.symbol, e.stop_loss_pct, e.take_profit_pct, s)
        cost = max((b.round_trip_cost_pct() for b in self.brokers.values()), default=0.0)
        return net_rr(tp_pct, sl_pct, cost)

    async def _send(self, e: SignalGenerated, s: Settings) -> None:
        for name in s.active_brokers:
            async with self._lock:
                await self._route(e, self.brokers[name], s)

    async def flush(self) -> None:
        """Settle every waiting symbol now. For shutdown and for tests, which must not sleep."""
        for task in list(self._settle_tasks):
            task.cancel()
        self._settle_tasks.clear()
        for symbol in list(self._pending):
            self._settling.discard(symbol)
            self._pending.setdefault(symbol, [])
            await self._settle(symbol, 0.0)

    async def _route(self, e: SignalGenerated, broker: Broker, s: Settings) -> None:
        reason = await self._blocked(broker, e.symbol) or self.strategy_halt(broker.name, e.strategy, s)
        if reason:
            self._skip(e, broker.name, reason)
            return

        price = self.prices.price(e.symbol)
        assert price is not None  # checked in _blocked
        sl_pct, tp_pct, _basis = self.levels(e.symbol, e.stop_loss_pct, e.take_profit_pct, s)
        if reason := self.edge_error(broker, tp_pct, s):
            self._skip(e, broker.name, reason)
            return
        direction = 1 if e.side == "buy" else -1
        try:
            account = await broker.account()
            size = await self.size_for_risk(broker, e.symbol, price, sl_pct, account, s, self.margin_for(e.strategy))
        except BrokerError as exc:
            self._skip(e, broker.name, f"could not size the order: {exc}")
            return
        if size < 1:
            self._skip(
                e, broker.name,
                f"risking {s.risk_per_trade_pct:g}% of {account.equity:.2f} behind a {sl_pct:.2f}% stop buys no whole contract",
            )
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

    def margin_for(self, strategy: str) -> float | None:
        """The margin cap this strategy asked for, or None to use Settings'."""
        return self.margins.get(strategy)

    # -- how far the stop is, how big the position, and whether the trade is worth taking ----------

    def levels(self, symbol: str, signal_sl: float | None, signal_tp: float | None, s: Settings) -> tuple[float, float, str]:
        """(stop %, target %, where they came from). A strategy that sets its own levels wins; then a
        fresh ATR, so the stop sits outside the noise in a fast market and tightens in a quiet one;
        then Settings' fixed percentages. ATR levels are clamped to the Settings ranges."""
        if signal_sl and signal_tp:
            return signal_sl, signal_tp, "the strategy's own levels"
        atr = self.volatility(symbol) if s.atr_protection else None
        if not atr:
            return s.stop_loss_pct, s.take_profit_pct, "Settings"

        def clamp(value: float, field: str) -> float:
            low, high = RANGES[field]
            return min(max(value, low), high)

        return (
            clamp(atr * s.atr_stop_mult, "stop_loss_pct"),
            clamp(atr * s.atr_target_mult, "take_profit_pct"),
            f"ATR {atr:.3f}%",
        )

    def strategy_halt(self, broker: str, strategy: str, s: Settings, now: float | None = None) -> str:
        """Why this strategy may not open anything on this broker today, or "". The broker-wide daily
        loss limit only fires once the whole account is down, by which time one strategy that keeps
        stopping out has already spent the day's budget for all of them; this stops it at its own
        share. Read from the PositionClosed log, so it counts what was actually booked."""
        if s.strategy_loss_limit_pct <= 0:
            return ""
        now = time.time() if now is None else now
        midnight = datetime.fromtimestamp(now, ZoneInfo(s.day_timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
        start_equity = float((self.store.day_risk(broker, midnight.date().isoformat()) or {}).get("start_equity") or 0.0)
        if start_equity <= 0:
            return ""  # the guard has not opened the day on this broker yet, so nothing has closed on it
        lost = -self.store.realized_since(broker, midnight.timestamp()).get(strategy, 0.0)
        limit = start_equity * s.strategy_loss_limit_pct / 100
        if lost < limit:
            return ""
        return (
            f"{strategy} has lost {lost:.2f} on {broker} today, its own {s.strategy_loss_limit_pct:g}% daily "
            f"limit ({limit:.2f} of {start_equity:.2f}); this strategy resumes tomorrow"
        )

    def edge_error(self, broker: Broker, tp_pct: float, s: Settings) -> str:
        """Why this target cannot pay for the round trip on this broker, or "". A target inside a few
        times the fee and slippage is a losing trade whatever the signal says."""
        if s.min_edge_multiple <= 0:
            return ""
        cost = broker.round_trip_cost_pct()
        needed = cost * s.min_edge_multiple
        if tp_pct >= needed:
            return ""
        return (
            f"the {tp_pct:.2f}% target is under {s.min_edge_multiple:g}x the {cost:.3f}% round trip on "
            f"{broker.name} ({needed:.2f}% needed): the trade cannot pay for itself"
        )

    async def size_for_risk(
        self, broker: Broker, symbol: str, price: float, sl_pct: float, account: Account, s: Settings,
        margin_pct: float | None = None,
    ) -> int:
        """The position whose stop costs risk_per_trade_pct of equity, capped at trade_margin_pct of
        the available margin. The stop decides the size, so every trade risks the same amount and a
        wider stop buys a smaller position instead of a bigger loss.

        `margin_pct` raises only the CAP, for a strategy that declared one. The risk rule is still
        the other half of the `min`, so a looser cap can never risk more than
        `risk_per_trade_pct` — it only stops the cap from deciding the size before the risk rule
        has had its say, which is what happens to a scalper whose stop is a tenth of a percent."""
        risk = account.equity * s.risk_per_trade_pct / 100.0
        cap = s.trade_margin_pct if margin_pct is None else margin_pct
        by_risk = await broker.size_for_notional(symbol, price, risk / (sl_pct / 100.0))
        by_margin = await broker.size_for_margin(symbol, price, account.available * cap / 100.0)
        return min(by_risk, by_margin)

    async def manual(
        self, broker: str, symbol: str, side: str, size: int | None, stop_loss: float | None, take_profit: float | None,
        request_id: str, margin_pct: float | None = None, strategy: str = MANUAL,
    ) -> dict[str, Any]:
        """An order from the dashboard. It passes the same gate as a signal (trading switch, daily limit,
        fresh price, nothing in flight, no open position read from the broker) and is sent by the Executor,
        so it is recorded, idempotent on request_id, and a timeout is looked up, never resent.

        Sized either in contracts (`size`) or as `margin_pct` of this broker's available margin — the
        latter is how one ticket opens the same-sized position, in % of capital, on paper and Delta.
        Raises TradeRefused."""

        def refuse(reason: str) -> TradeRefused:
            self.bus.publish(TradeSkipped(strategy=strategy, symbol=symbol, side=side, broker=broker, reason=reason))
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
        is_option = symbol.startswith(("C-", "P-"))
        if not stop_loss and not is_option:
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
                "client_order_id": cid, "broker": broker, "strategy": strategy, "symbol": symbol, "side": side,
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
        except Exception as exc:
            log.exception("unexpected_place_order_error client_order_id=%s", e.client_order_id)
            self._failed(e.client_order_id, f"Execution error: {exc}")
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
