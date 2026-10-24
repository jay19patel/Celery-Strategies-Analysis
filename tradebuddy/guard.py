"""Watches open positions on every active broker: the daily loss limit, auto trailing, early exits.

Runs in the engine (the only writer of trading state), every few seconds:

    daily loss   equity down `daily_loss_limit_pct` from the start of the trading day ->
                 close every position on that broker, block new entries until the next day.
    trailing     price has covered `trailing_trigger_pct` of the way from entry to the target ->
                 move the target out by `trailing_extend_pct` of the first target distance and the
                 stop to lock `trailing_lock_pct` of the open profit. A stop only ever tightens; a
                 position trails at most its `max_steps` times, then closes at its target.
    early exit   `exits.decide` says the trade's reason is gone, it has spent its risk without
                 making ground, its horizon is up, or the carry is eating it -> close it now
                 rather than wait for a barrier. Off until `early_exit_enabled`.

The order matters. The daily limit flattens everything and returns, so nothing else runs on a
halted broker. The early exit is asked BEFORE trailing: trailing a stop on a position that is
about to be closed would publish a protection change nobody acts on, and the pass that moved the
stop would be the last thing in the log before the exit, which reads as the guard contradicting
itself.

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

from tradebuddy import exits
from tradebuddy.brokers import Account, Broker, Position
from tradebuddy.brokers.base import protection_error
from tradebuddy.delta import round_to_tick
from tradebuddy.errors import BrokerError
from tradebuddy.events import DailyLossHalt, EventBus, GuardAlert, PositionExitEarly, ProtectionTrailed
from tradebuddy.jobs import NULL_JOB
from tradebuddy.settings import Settings
from tradebuddy.store import Store

log = logging.getLogger(__name__)

TickSize = Callable[[str], Awaitable[float]]
# symbol -> the engine's directional view, or {} when it has none yet.
ViewOf = Callable[[str], Awaitable[dict[str, Any]]]
# symbol -> annualised realised volatility as a fraction (0.27 = 27%), or None when unknown.
VolOf = Callable[[str], float | None]
# symbol -> the latest funding rate as a % per 8h, or None.
FundingOf = Callable[[str], float | None]


def exit_params(s: Settings) -> exits.Params:
    """Settings -> the pure module's parameters. One place, so a renamed setting fails loudly."""
    return exits.Params(
        horizon_hours=s.exit_horizon_hours,
        stalled_adverse=s.exit_stalled_adverse,
        stalled_progress=s.exit_stalled_progress,
        stalled_min_hours=s.exit_stalled_min_hours,
        timeout_min_resolve=s.exit_timeout_min_resolve,
        carry_max_share=s.exit_carry_max_share,
    )


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
        view_of: ViewOf | None = None, vol_of: VolOf | None = None, funding_of: FundingOf | None = None,
    ) -> None:
        self.bus = bus
        self.store = store
        self.brokers = brokers
        self.settings = settings
        self.tick_size = tick_size
        self.lock = lock  # shared with manual protection edits: one SL/TP change at a time
        # The readings the exit rules need, handed in rather than computed: the guard must look at
        # the same outlook the strategies and the dashboard do (one reading, never two), and it has
        # no business scanning a year of bars on a three-second loop.
        self.view_of = view_of
        self.vol_of = vol_of
        self.funding_of = funding_of
        self.status: dict[str, dict[str, Any]] = {}
        self.exits: dict[tuple[str, str], dict[str, Any]] = {}  # (broker, symbol) -> the last reading, for the page
        self._failed_at: dict[tuple[str, str], float] = {}

    job = NULL_JOB  # set by the System, for the dashboard

    async def run(self, interval: float = 3.0) -> None:
        while True:
            try:
                with self.job.tick():
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
            self.exits = {k: v for k, v in self.exits.items() if k[0] != name or any(p.symbol == k[1] for p in positions)}
            for p in positions:
                # Asked before trailing: a stop moved on a position that is about to close is a
                # protection change nobody acts on, logged immediately before the exit that
                # overrode it.
                if await self._early_exit(broker, p, s):
                    continue
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

    # -- early exit ----------------------------------------------------------------------

    async def _reading(self, broker: Broker, p: Position, s: Settings) -> exits.Decision | None:
        """Price this position and say what should happen to it. None when it cannot be priced.

        Runs whether or not `early_exit_enabled`, because the odds are worth showing even when
        nothing acts on them: a 1:3 ticket that is really a 75% chance of being stopped is the
        number this system was not showing anyone.
        """
        if not p.opened_at or p.mark_price <= 0 or p.entry_price <= 0:
            return None
        rv = self.vol_of(p.symbol) if self.vol_of else None
        if not rv or rv <= 0:
            return None  # no measured volatility: nothing here can price the horizon (invariant 8)
        view = await self.view_of(p.symbol) if self.view_of else {}
        funding = self.funding_of(p.symbol) if self.funding_of else None
        return exits.decide(
            side=p.side,
            entry_price=p.entry_price,
            price=p.mark_price,
            stop_loss=p.stop_loss,
            take_profit=p.take_profit,
            held_hours=max(0.0, (time.time() - p.opened_at) / exits.HOUR),
            rv_annual=rv,
            cost_pct=broker.round_trip_cost_pct(),
            mae_pct=p.mae_pct,
            mfe_pct=p.mfe_pct,
            funding_rate_pct=funding,
            invalidation=exits.outlook_invalidation(p.side, view),
            params=exit_params(s),
        )

    async def _early_exit(self, broker: Broker, p: Position, s: Settings) -> bool:
        """True when this position was closed. Every exit is published with the odds it fired on."""
        key = (broker.name, p.symbol)
        try:
            decision = await self._reading(broker, p, s)
        except Exception:  # a reading must never be able to stop the daily limit or the trailing
            log.exception("exit_reading_failed broker=%s symbol=%s", broker.name, p.symbol)
            return False
        if decision is None:
            self.exits.pop(key, None)
            return False

        held_hours = max(0.0, (time.time() - (p.opened_at or 0.0)) / exits.HOUR)
        self.exits[key] = decision.to_dict() | {
            "broker": broker.name, "symbol": p.symbol, "side": p.side, "strategy": p.strategy or "",
            "held_hours": round(held_hours, 3), "armed": s.early_exit_enabled, "checked_at": time.time(),
        }
        if not decision.closing or not s.early_exit_enabled:
            return False

        # The same lock the trailing and the manual edits take: one change to a position at a time.
        async with self.lock:
            try:
                await broker.close_position(p.symbol, reason=f"guard: {decision.rule}")
            except BrokerError as exc:
                self._failed_at[key] = time.time()
                self.bus.publish(GuardAlert(
                    broker=broker.name, symbol=p.symbol, message=f"early exit ({decision.rule}) failed: {exc}",
                ))
                return False
        self._failed_at.pop(key, None)
        self.exits.pop(key, None)
        self.bus.publish(PositionExitEarly(
            broker=broker.name, strategy=p.strategy or "", symbol=p.symbol, side=p.side,
            rule=decision.rule, reason=decision.reason, price=p.mark_price,
            stop_loss=p.stop_loss, take_profit=p.take_profit, held_hours=round(held_hours, 3),
            pricing=decision.pricing.to_dict() if decision.pricing else {},
        ))
        log.info("early_exit broker=%s symbol=%s rule=%s %s", broker.name, p.symbol, decision.rule, decision.reason)
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
