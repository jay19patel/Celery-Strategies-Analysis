"""The guard: an engine job over every open structure, and one over every broker's day.

A position on the perpetual leaves when the mark reaches its strategy's stop or target, or after
its strategy's hold time. On an exchange that holds stops, the stop is also left there at entry; when
that one fills first, the close books its fill. Per options structure, strongest reason first:
  1. past expiry and still open  -> settle at intrinsic on the index
  2. inside `exit_before_expiry_minutes` -> close (settlement and pin risk are not worth the last hour);
     a bought structure with a bounded loss has no pin risk and settles at intrinsic instead
  3. P&L at the take-profit or the stop-loss -> close. A stop must hold `STOP_CONFIRM_CHECKS` checks
     in a row: a thin wing's ask can jump for a few seconds with the underlying standing still, and
     that is the book's spread, not the trade's loss. On a credit structure whose expiry payoff at
     the current spot is already a loss — the spot is past a breakeven — the move is real and the
     stop closes at once.

The P&L read is what closing would actually book (`TradingService.valuation`): bids for longs, asks
for shorts, every fee. Without a fresh quote on every leg nothing is decided — absence over
staleness — except the expiry rules, which depend only on the clock.

A structure left in `error` — a close that did not finish, an entry whose unwind did not — is
retried here too, with a backoff, until it is flat: its intent was always to be out, and left alone
it would sit outside every stop and past its expiry. Past expiry it is settled instead. One
structure failing never stops the others from being checked.

Per broker: equity down `daily_loss_limit_pct` from the start of the trading day closes that
broker's structures and blocks its entries until the next day. Brokers are independent: one
halting never touches another. Equity `max_drawdown_pct` below its peak on any broker stops every
entry everywhere (the kill switch) and closes every position, until it is resumed by hand.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlmodel import col, select

from app.core.db import session
from app.core.events import EventHub
from app.core.models import Leg, Structure
from app.errors import BrokerError
from app.options.pricing import LegSpec, payoff
from app.settings import Settings
from app.trading.risk import kill_state, set_halt, start_day
from app.trading.service import TradingService, exit_thresholds, held_to_settlement, is_directional

log = logging.getLogger(__name__)
RETRY_FIRST, RETRY_MAX = 30.0, 600.0  # seconds between retries of a structure in error: doubling, capped
# A stop read on the book must hold this many checks in a row (5s apart) before it closes: one wide
# ask on a thin wing is not a loss. With the spot through a breakeven it closes at once.
STOP_CONFIRM_CHECKS = 3


class Guard:
    def __init__(self, trading: TradingService, settings: Any, hub: EventHub) -> None:
        self.trading = trading
        self.settings: Any = settings
        self.hub = hub
        self._retry: dict[str, tuple[float, float]] = {}  # structure id -> (next attempt, current wait)
        self._breached: dict[str, int] = {}  # structure id -> guard checks in a row at or past its stop

    async def check_structures(self) -> str:
        s: Settings = self.settings()
        with session() as db:
            rows = list(db.exec(select(Structure).where(col(Structure.status).in_(("open", "error")))).all())
            ids = [r.id for r in rows]
            legs = list(db.exec(select(Leg).where(col(Leg.structure_id).in_(ids))).all()) if ids else []
        acted = failed = 0
        now = time.time()
        self._retry = {sid: v for sid, v in self._retry.items() if sid in ids}
        self._breached = {sid: n for sid, n in self._breached.items() if sid in ids}
        for st in rows:
            if st.status == "error":
                reason = self._error_reason(st, now)
            elif await self.trading.stop_filled(st):
                reason = "the exchange stop filled"  # the close books that fill; it sends nothing
            else:
                mine = [leg for leg in legs if leg.structure_id == st.id]
                if is_directional(st):
                    await self.trading.trail(st, mine)
                reason = self.exit_reason(st, mine, s, now)
            if reason is None:
                continue
            acted += 1
            try:
                if reason == "expired":
                    await self.trading.settle_expired(st.id)
                else:
                    await self.trading.close(st.id, reason)
            except Exception:  # one structure must not keep the guard from every other one
                failed += 1
                log.exception("guard_exit_failed structure=%s reason=%s", st.id, reason)
        open_ = sum(st.status == "open" for st in rows)
        return f"{open_} open, {len(rows) - open_} in error, {acted} exited" + (f", {failed} failed" if failed else "")

    def _error_reason(self, st: Structure, now: float) -> str | None:
        """When a structure in error is due another attempt at getting flat, and how."""
        next_at, wait = self._retry.get(st.id, (0.0, RETRY_FIRST / 2))
        if now < next_at:
            return None
        wait = min(wait * 2, RETRY_MAX)
        self._retry[st.id] = (now + wait, wait)
        if not is_directional(st) and now >= st.expiry:
            return "expired"
        return f"retrying the exit after an error: {st.error[:200]}"

    def exit_reason(self, st: Structure, legs: list[Leg], s: Settings, now: float) -> str | None:
        if is_directional(st):
            return self._directional_exit(st, legs, now)
        if now >= st.expiry:
            return "expired"
        minutes_left = (st.expiry - now) / 60
        # A defined-risk credit — a spread, condor or butterfly, the expiry-decay spread — is HELD TO
        # SETTLEMENT: no stop, no take profit, no time exit. Its worst case is capped by its wings and
        # it was sized on that worst case. Replayed on the recorded chains, every early exit paid the
        # wide ask to buy back on noise: a 1x-credit stop cut win rates from about 80% to 14-50% and
        # turned bull puts and condors that settled +2.5% to +5.5% into losses. A manual close still works.
        settle = held_to_settlement(st)
        bought = st.entry_cash < 0 and st.max_loss is not None  # a long option: settles at intrinsic, nothing to pin
        floor = 0 if settle or bought else s.exit_before_expiry_minutes
        if floor and minutes_left <= floor:
            return f"time exit: {minutes_left:.0f} min to expiry"
        value = self.trading.valuation(st, legs)
        pnl = value["pnl"]
        if pnl is None:
            return None
        self._mark(st, pnl)
        if settle:
            return None
        tp, sl = exit_thresholds(st)
        if tp is not None and pnl >= tp:
            return f"take profit: {pnl:+,.2f} reached {st.take_profit_pct:g}% target {tp:+,.2f}"
        if sl is None or pnl > sl:
            self._breached.pop(st.id, None)
            return None
        n = self._breached[st.id] = self._breached.get(st.id, 0) + 1
        past = st.entry_cash > 0 and self._past_breakeven(st, legs)
        if not past and n < STOP_CONFIRM_CHECKS:
            return None
        why = "the spot is past a breakeven" if past else f"held {n} checks"
        return f"stop loss: {pnl:+,.2f} reached {st.stop_loss_pct:g}% stop {sl:+,.2f} ({why})"

    def _past_breakeven(self, st: Structure, legs: list[Leg]) -> bool:
        """Would the structure lose at expiry if the underlying settled where it is now?"""
        spot = self.trading.feed.spot(st.underlying)
        held = [leg for leg in legs if leg.entry_price is not None and leg.exit_price is None]
        if not spot or not held:
            return False
        specs = [LegSpec(leg.kind, leg.strike, leg.side, leg.size, leg.entry_price or 0.0, leg.contract_value) for leg in held]
        return payoff(specs, spot) < 0

    def _directional_exit(self, st: Structure, legs: list[Leg], now: float) -> str | None:
        """A position on the perpetual leaves at its strategy's levels, read on the mark — the price the
        exchange triggers stops on — or when it has been held as long as it may be."""
        if now >= st.expiry:
            return f"max hold reached ({(now - st.opened_at) / 3600:.1f}h)"
        quote = self.trading.book(legs[0].symbol) if legs else None
        if quote is None:
            return None
        if (pnl := self.trading.valuation(st, legs)["pnl"]) is not None:
            self._mark(st, pnl)
        mark, long = quote[2], legs[0].side == "buy"
        stop, target = st.stop_price or 0.0, st.target_price or 0.0
        if (mark <= stop) if long else (mark >= stop):
            if st.initial_stop and stop != st.initial_stop:
                return f"trailing stop: mark {mark:,.2f} reached the stop trailed to {stop:,.2f} (from {st.initial_stop:,.2f})"
            return f"stop loss: mark {mark:,.2f} reached the strategy's stop {stop:,.2f}"
        if target and ((mark >= target) if long else (mark <= target)):
            return f"target: mark {mark:,.2f} reached {target:,.2f}"
        return None

    @staticmethod
    def _mark(st: Structure, pnl: float) -> None:
        with session() as db:
            row = db.get(Structure, st.id)
            if row is not None and row.status == "open":
                row.mark_pnl, row.marked_at = pnl, time.time()
                db.add(row)
                db.commit()

    async def check_daily_loss(self) -> str:
        """The daily loss limit per broker, and the drawdown limit across all of them."""
        s: Settings = self.settings()
        out = []
        for name in s.active_brokers:
            b = self.trading.brokers[name]
            if b.not_ready():
                continue
            try:
                account = await b.account()
            except BrokerError:
                continue  # an unreadable account is not a loss; the trade gate refuses entries on it
            if (why := self.trading.drawdown_breach(name, account.equity)) and kill_state() is None:
                self.trading.engage_kill(why)
                for broker in s.active_brokers:
                    await self.trading.close_all(f"drawdown limit: {why}", broker=broker)
                out.append(f"{name}: {why}")
                continue
            if s.daily_loss_limit_pct <= 0:
                continue
            day = start_day(name, s.day_timezone, account.equity)
            if day.halted or day.start_equity <= 0:
                out.append(f"{name}: halted" if day.halted else f"{name}: no equity")
                continue
            down = 100 * (day.start_equity - account.equity) / day.start_equity
            out.append(f"{name} {down:+.2f}%")
            if down >= s.daily_loss_limit_pct:
                reason = f"equity down {down:.2f}% today (limit {s.daily_loss_limit_pct:g}%)"
                set_halt(name, s.day_timezone, reason)
                self.hub.publish("RiskHalt", f"{name} halted: {reason}; closing its structures", "warning", broker=name, down_pct=round(down, 3))
                await self.trading.close_all(f"daily loss limit: {reason}", broker=name)
        return ", ".join(out) or ("daily limit off" if s.daily_loss_limit_pct <= 0 else "no broker to check")
