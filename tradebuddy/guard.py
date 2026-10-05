"""Watches open positions on every active broker: the daily loss limit and auto trailing.

Runs in the engine (the only writer of trading state), every few seconds:

    daily loss   equity down `daily_loss_limit_pct` from the start of the trading day ->
                 close every position on that broker, block new entries until the next day.
    trailing     price has covered `trailing_trigger_pct` of the way from entry to the target ->
                 move the target out by `trailing_extend_pct` of the first target distance and the
                 stop to lock `trailing_lock_pct` of the open profit. A stop only ever tightens; a
                 position trails at most its `max_steps` times, then closes at its target.

Brokers stay independent: one broker's loss or failure never touches another.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from tradebuddy.brokers import Account, Broker, Position
from tradebuddy.brokers.base import protection_error
from tradebuddy.delta import round_to_tick
from tradebuddy.errors import BrokerError
from tradebuddy.events import DailyLossHalt, EventBus, GuardAlert, ProtectionTrailed
from tradebuddy.settings import Settings
from tradebuddy.store import Store

log = logging.getLogger(__name__)

TickSize = Callable[[str], Awaitable[float]]


def trading_day(tz: str, now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, ZoneInfo(tz)).date().isoformat()


def position_key(p: Position) -> str:
    return f"{p.side}:{p.entry_price:.10g}"


def trail_levels(p: Position, price: float, base_target: float, s: Settings, tick: float) -> tuple[float, float] | None:
    """The next (stop_loss, take_profit), or None when this position should not trail now."""
    if not p.take_profit or not p.stop_loss or price <= 0:
        return None
    d = 1 if p.side == "long" else -1
    target = (p.take_profit - p.entry_price) * d
    profit = (price - p.entry_price) * d
    if target <= 0 or profit <= 0 or profit / target < s.trailing_trigger_pct / 100:
        return None
    step = (base_target or target) * s.trailing_extend_pct / 100
    take_profit = float(round_to_tick(p.take_profit + d * step, tick))
    locked = float(round_to_tick(p.entry_price + d * profit * s.trailing_lock_pct / 100, tick))
    stop_loss = locked if (locked - p.stop_loss) * d > 0 else p.stop_loss  # never loosen the stop
    if protection_error(p.side, price, stop_loss, take_profit):
        return None  # too close to the price to place safely; try again on the next pass
    return stop_loss, take_profit


class PositionGuard:
    RETRY_AFTER = 30.0  # seconds before retrying a trail the broker refused

    def __init__(
        self, bus: EventBus, store: Store, brokers: dict[str, Broker], settings: Callable[[], Settings],
        tick_size: TickSize, lock: asyncio.Lock,
    ) -> None:
        self.bus = bus
        self.store = store
        self.brokers = brokers
        self.settings = settings
        self.tick_size = tick_size
        self.lock = lock  # shared with manual protection edits: one SL/TP change at a time
        self.status: dict[str, dict[str, Any]] = {}
        self._failed_at: dict[tuple[str, str], float] = {}

    async def run(self, interval: float = 3.0) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("guard_pass_failed")
            await asyncio.sleep(interval)

    def halt_reason(self, broker: str) -> str:
        """Why `broker` may not open anything today, or ""."""
        row = self.store.day_risk(broker, trading_day(self.settings().day_timezone))
        return f"daily loss limit hit — {row['halt_reason']}; new entries resume tomorrow" if row and row["halted_at"] else ""

    async def check(self) -> None:
        s = self.settings()
        for name in s.active_brokers:
            broker = self.brokers[name]
            if broker.not_ready():
                self.status[name] = {"broker": name, "error": broker.not_ready()}
                continue
            try:
                account = await broker.account()
                positions = await broker.positions()
            except BrokerError as exc:
                self.status[name] = {**self.status.get(name, {}), "broker": name, "error": f"could not read the account: {exc}"}
                continue
            if await self._daily(broker, account, positions, s):
                continue
            self._sync_controls(name, positions, s)
            controls = self.store.controls(name)
            for p in positions:
                await self._trail(broker, p, controls.get(p.symbol), s)

    # -- daily loss ---------------------------------------------------------------------

    async def _daily(self, broker: Broker, account: Account, positions: list[Position], s: Settings) -> bool:
        """True when the broker is halted for the day (its positions have been closed)."""
        day = trading_day(s.day_timezone)
        row = self.store.day_risk(broker.name, day) or self.store.start_day(broker.name, day, account.equity)
        start = row["start_equity"]
        pnl = account.equity - start
        loss_pct = -pnl / start * 100 if start > 0 else 0.0
        limit = s.daily_loss_limit_pct
        self.status[broker.name] = {
            "broker": broker.name, "day": day, "start_equity": start, "equity": account.equity, "pnl": pnl,
            "pnl_pct": -loss_pct, "limit_pct": limit, "used_pct": (max(0.0, loss_pct) / limit * 100) if limit else 0.0,
            "halted": bool(row["halted_at"]), "halt_reason": row["halt_reason"] or "", "error": "", "checked_at": time.time(),
        }
        if not row["halted_at"]:
            if not limit or loss_pct < limit:
                return False
            reason = f"equity {account.equity:.2f} is {loss_pct:.2f}% below today's start {start:.2f} (limit {limit:g}%)"
            self.store.halt_day(broker.name, day, reason)
            self.status[broker.name] |= {"halted": True, "halt_reason": reason}
            log.warning("daily_loss_halt broker=%s %s", broker.name, reason)
        elif not positions:
            return True
        # Halted: flatten. Repeated on every pass while anything is still open.
        try:
            result = await broker.close_all()
        except BrokerError as exc:
            result = {"closed": [], "errors": [str(exc)]}
        if result["errors"]:
            log.warning("daily_loss_flatten_errors broker=%s errors=%s", broker.name, result["errors"])
        if not row["halted_at"] or result["closed"]:  # once on the halt, then only when something more closed
            self.bus.publish(DailyLossHalt(
                broker=broker.name, day=day, loss_pct=round(loss_pct, 4), limit_pct=limit, start_equity=start,
                equity=account.equity, closed=result["closed"], errors=result["errors"],
            ))
        return True

    # -- trailing ------------------------------------------------------------------------

    def _sync_controls(self, broker: str, positions: list[Position], s: Settings) -> None:
        controls = self.store.controls(broker)
        for p in positions:
            row = controls.get(p.symbol)
            if row is None or row["position"] != position_key(p):
                base = abs(p.take_profit - p.entry_price) if p.take_profit else 0.0
                self.store.start_control(broker, p.symbol, position_key(p), s.trailing_enabled, s.trailing_max_steps, base)
        self.store.drop_controls(broker, keep={p.symbol for p in positions})

    async def _trail(self, broker: Broker, p: Position, control: dict[str, Any] | None, s: Settings) -> None:
        if not control or not control["trailing"] or control["steps"] >= control["max_steps"]:
            return
        key = (broker.name, p.symbol)
        if time.time() - self._failed_at.get(key, 0) < self.RETRY_AFTER:
            return
        try:
            tick = await self.tick_size(p.symbol)
        except BrokerError:
            return
        levels = trail_levels(p, p.mark_price, control["base_target"], s, tick)
        if levels is None:
            return
        stop_loss, take_profit = levels
        async with self.lock:
            try:
                await broker.update_protection(p.symbol, stop_loss, take_profit)
            except BrokerError as exc:
                self._failed_at[key] = time.time()
                self.bus.publish(GuardAlert(broker=broker.name, symbol=p.symbol, message=f"trailing step failed: {exc}"))
                return
        self._failed_at.pop(key, None)
        step = control["steps"] + 1
        self.store.update_control(broker.name, p.symbol, steps=step)
        self.bus.publish(ProtectionTrailed(
            broker=broker.name, symbol=p.symbol, step=step, max_steps=control["max_steps"], price=p.mark_price,
            old_stop_loss=p.stop_loss, stop_loss=stop_loss, old_take_profit=p.take_profit, take_profit=take_profit,
        ))
