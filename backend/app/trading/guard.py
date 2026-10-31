"""The guard: an engine job over every open structure, and one over every broker's day.

A position on the perpetual leaves when the mark reaches its strategy's stop or target, or after
`perp_max_hold_hours`. Per options structure, strongest reason first:
  1. past expiry and still open  -> settle at intrinsic on the index
  2. inside `exit_before_expiry_minutes` -> close (settlement and pin risk are not worth the last hour),
     or `zh_exit_minutes` on a zero-hero buy, whichever of the two comes first
  3. a zero-hero buy whose break has been undone -> close on the premise, not on the P&L
  4. P&L at the take-profit or the stop-loss -> close

The P&L read is what closing would actually book (`TradingService.valuation`): bids for longs, asks
for shorts, every fee. Without a fresh quote on every leg nothing is decided — absence over
staleness — except the expiry rules, which depend only on the clock.

A structure left in `error` — a close that did not finish, an entry whose unwind did not — is
retried here too, with a backoff, until it is flat: its intent was always to be out, and left alone
it would sit outside every stop and past its expiry. Past expiry it is settled instead. One
structure failing never stops the others from being checked.

Per broker: equity down `daily_loss_limit_pct` from the start of the trading day closes that
broker's structures and blocks its entries until the next day. Brokers are independent: one
halting never touches another.
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
from app.settings import Settings
from app.trading.risk import set_halt, start_day
from app.trading.service import TradingService, exit_thresholds, is_directional, is_zerohero

log = logging.getLogger(__name__)
RETRY_FIRST, RETRY_MAX = 30.0, 600.0  # seconds between retries of a structure in error: doubling, capped


class Guard:
    def __init__(self, trading: TradingService, settings: Any, hub: EventHub, zerohero: Any = None) -> None:
        self.trading = trading
        self.settings: Any = settings
        self.hub = hub
        self.zerohero = zerohero  # reads the break a zero-hero buy was opened on; None in tests
        self._retry: dict[str, tuple[float, float]] = {}  # structure id -> (next attempt, current wait)

    async def check_structures(self) -> str:
        s: Settings = self.settings()
        with session() as db:
            rows = list(db.exec(select(Structure).where(col(Structure.status).in_(("open", "error")))).all())
            ids = [r.id for r in rows]
            legs = list(db.exec(select(Leg).where(col(Leg.structure_id).in_(ids))).all()) if ids else []
        acted = failed = 0
        now = time.time()
        self._retry = {sid: v for sid, v in self._retry.items() if sid in ids}
        for st in rows:
            if st.status == "error":
                reason = self._error_reason(st, now)
            else:
                reason = self.exit_reason(st, [leg for leg in legs if leg.structure_id == st.id], s, now)
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
        # A zero-hero buy leaves at whichever time stop comes FIRST. In the last hour theta goes
        # vertical on a contract that is all time value, so holding one to find out is not a
        # decision, it is a donation with a countdown.
        floor = s.zh_exit_minutes if is_zerohero(st) else s.exit_before_expiry_minutes
        if floor and minutes_left <= floor:
            return f"time exit: {minutes_left:.0f} min to expiry"
        # The premise, re-read. This is checked BEFORE the P&L rules on purpose: a position whose
        # reason for existing has gone is closed on that fact, not on whether it is green yet.
        if is_zerohero(st) and self.zerohero is not None and legs and (
            why := self.zerohero.invalidated(st.underlying, legs[0].kind)
        ):
            return why
        value = self.trading.valuation(st, legs)
        pnl = value["pnl"]
        if pnl is None:
            return None
        self._mark(st, pnl)
        tp, sl = exit_thresholds(st)
        if tp is not None and pnl >= tp:
            return f"take profit: {pnl:+,.2f} reached {st.take_profit_pct:g}% target {tp:+,.2f}"
        if sl is not None and pnl <= sl:
            return f"stop loss: {pnl:+,.2f} reached {st.stop_loss_pct:g}% stop {sl:+,.2f}"
        return None

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
        s: Settings = self.settings()
        if s.daily_loss_limit_pct <= 0:
            return "off"
        out = []
        for name in s.active_brokers:
            b = self.trading.brokers[name]
            if b.not_ready():
                continue
            try:
                account = await b.account()
            except BrokerError:
                continue  # an unreadable account is not a loss; the trade gate refuses entries on it
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
        return ", ".join(out) or "no broker to check"
