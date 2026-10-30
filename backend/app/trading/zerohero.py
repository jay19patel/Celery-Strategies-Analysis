"""The zero-hero cycle: scan the expiring chain, size against a daily budget, and either open or
record what it would have opened.

SHADOW MODE IS THE DEFAULT AND THAT IS THE POINT
------------------------------------------------
Whether this strategy makes money depends on one number nobody has yet: how often a candidate that
passes all five dimensions actually reaches its target. The sizing arithmetic is only as good as
that hit rate — at 10% and a 10x payoff the edge is exactly zero, and every cost below that makes
it negative. So `zh_shadow` starts on: the scan runs, the candidate is chosen, the size is
computed and the whole thing is written to a `Decision` row, and no order is sent. After a few
weeks of those rows the hit rate is a measurement rather than a hope.

Every cycle is a `Decision` with `strategy="zero_hero"` — the same row the options auto-trader
writes, so the journal, the event log and the retention job already handle it, and a scan that
refused to trade says which of the five dimensions refused it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlmodel import col, select

from app.analysis import fibonacci, orderflow
from app.analysis import pulse as pulse_mod
from app.analysis import structure as smc_mod
from app.core.db import session
from app.core.events import EventHub
from app.core.models import Decision, Structure
from app.errors import AppError, BrokerError, Refused
from app.market import options as chain
from app.market.delta import Candle
from app.market.indicators import fold
from app.options import zerohero as scanner
from app.options.strategies import ZERO_HERO, single
from app.settings import Settings
from app.trading.desk import Desk
from app.trading.risk import day_start, trading_day
from app.trading.service import TradingService
from app.trading.timing import get_zerohero_window

log = logging.getLogger(__name__)

# The scan reads 5m bars: 15m is four bars an hour, which cannot see a break inside a two-hour
# window, and 1m is mostly noise at this ATR. They are folded from the stored 1m series.
BAR_SECONDS = 300
BASE_BARS = 2400  # 1m bars to fold: ~400 five-minute bars, enough for the swing lookback
MIN_BARS = 60


def _candles(rows: list[dict[str, Any]]) -> list[Candle]:
    return [Candle(int(r["time"]), r["open"], r["high"], r["low"], r["close"], r.get("volume", 0.0)) for r in rows]


@dataclass(frozen=True)
class Reading:
    """One bar's worth of everything the scan reads off the perpetual."""

    bars: int
    bar_time: int
    price: float
    smc: smc_mod.Structure
    pulse: pulse_mod.Pulse
    flow: orderflow.Flow
    fib: fibonacci.Fib


class ZeroHero:
    def __init__(self, desk: Desk, trading: TradingService, settings: Any, hub: EventHub) -> None:
        self.desk = desk
        self.trading = trading
        self.settings: Any = settings
        self.hub = hub
        self._readings: dict[str, tuple[int, Reading]] = {}

    # -- market readings -------------------------------------------------------------------

    def bars(self, underlying: str) -> list[Candle]:
        rows = self.desk.feed.bars(chain.PERPETUALS[underlying], BASE_BARS, "1m")
        return _candles(fold(rows, BAR_SECONDS))

    def reading(self, underlying: str) -> Reading | None:
        """The four bar readings the scan and the guard both need, computed once per bar.

        The guard runs every five seconds and five-minute bars change every five minutes, so
        without this the same `find_breaks` over the same 400 bars would run sixty times for one
        new piece of information — and pull 2400 rows out of SQLite each time to do it.
        """
        bars = self.bars(underlying)
        if len(bars) < MIN_BARS:
            return None
        last = bars[-1].time
        cached = self._readings.get(underlying)
        if cached and cached[0] == last:
            return cached[1]
        out = Reading(
            bars=len(bars), bar_time=last, price=bars[-1].close,
            smc=smc_mod.read(bars), pulse=pulse_mod.read(chain.PERPETUALS[underlying], bars),
            flow=orderflow.read(bars), fib=fibonacci.read(bars),
        )
        self._readings[underlying] = (last, out)
        return out

    def expiry(self, underlying: str) -> float | None:
        """The nearest expiry that has not settled: on a daily chain, today's."""
        return next(iter(chain.by_expiry(self.desk.feed.quotes(underlying))), None)

    def scan(self, underlying: str) -> scanner.Scan:
        """Everything the page shows and the cycle decides on. Read-only: no orders, no rows."""
        s: Settings = self.settings()
        now = time.time()
        spot = self.desk.spot(underlying)
        expiry = self.expiry(underlying)
        if expiry is None:
            return scanner.Scan(underlying, now, spot, None, None, False, f"no live {underlying} options")
        quotes = self.desk.feed.quotes(underlying, expiry)
        view = self.reading(underlying)
        if view is None:
            return scanner.Scan(underlying, now, spot, expiry, round((expiry - now) / 3600, 3), False,
                                f"needs {MIN_BARS} five-minute bars of {chain.PERPETUALS[underlying]}")
        summary = chain.expiry_summary(quotes, spot, now) if quotes else {}
        return scanner.scan(
            underlying, quotes, spot, now, summary, view.smc, view.pulse, view.flow, view.fib,
            self.desk.realised(underlying)["reference"], s,
        )

    def invalidated(self, underlying: str, kind: str) -> str:
        """Has the break this position was bought on been undone? "" while it still stands.

        Read from the same `find_breaks` the entry read, not from a level copied onto the position:
        a call bought on an up-break is finished the moment structure says the most recent break
        went the other way. The premium left is worth more sold than hoped over.
        """
        view = self.reading(underlying)
        last = view.smc.last_break if view else None
        if last is None:
            return ""
        against = "down" if kind == "call" else "up"
        if last.direction != against or (view.smc.bars - 1 - last.index) > scanner.MAX_BREAK_AGE:
            return ""
        return f"momentum invalidated: structure broke {last.direction} through {last.level:,.0f}"

    # -- the day's budget ------------------------------------------------------------------

    def spent_today(self, broker: str) -> tuple[float, int]:
        """What this broker has already paid in zero-hero premium today, and how many attempts it
        took. A failed or closed attempt still counts: the budget is for shots taken, not for shots
        that worked, or a losing day would keep buying its way back."""
        s: Settings = self.settings()
        since = day_start(s.day_timezone, trading_day(s.day_timezone))
        with session() as db:
            rows = db.exec(
                select(Structure).where(
                    Structure.broker == broker, Structure.strategy == ZERO_HERO, Structure.opened_at >= since,
                )
            ).all()
        # A debit is negative entry cash; what it cost is its absolute value plus the fees paid.
        return sum(max(0.0, -st.entry_cash) + st.fees for st in rows), len(rows)

    def brokers(self) -> list[str]:
        s: Settings = self.settings()
    def get_window(self, now_ts: float | None = None) -> tuple[float, float, str]:
        """Compute the active zero-hero scan window against the nearest listed expiry."""
        now = time.time() if now_ts is None else now_ts
        s: Settings = self.settings()
        expiry: float | None = None
        for u in s.underlyings:
            e = self.expiry(u)
            if e and (expiry is None or e < expiry):
                expiry = e
        return get_zerohero_window(now, s, expiry)

    # -- the cycle -------------------------------------------------------------------------

    async def cycle(self) -> str:
        s: Settings = self.settings()
        if not s.zh_enabled:
            return "zero-hero is off"
        out = []
        for underlying in s.underlyings:
            d = await self.decide(underlying)
            out.append(f"{underlying}: {d['action']} — {d['reason']}")
        return "; ".join(out)

    async def decide(self, underlying: str) -> dict[str, Any]:
        dec = Decision(ts=time.time(), underlying=underlying, action="skip", strategy=ZERO_HERO)
        try:
            await self._decide(dec, underlying)
        except AppError as exc:
            dec.reason = str(exc)
        except Exception as exc:  # a bug must not stop the scheduler; it is recorded and shown
            log.exception("zerohero_decision_failed underlying=%s", underlying)
            dec.action, dec.reason = "error", f"{type(exc).__name__}: {exc}"
        with session() as db:
            db.add(dec)
            db.commit()
            db.refresh(dec)
        # Only a scan that got as far as a candidate is worth an event; a closed window every
        # minute for twenty hours a day is noise, and noise is how a log stops being read.
        if dec.action != "skip" or (dec.candidates or []):
            self.hub.publish(
                "ZeroHeroDecision", f"{underlying}: {dec.action} — {dec.reason}",
                "error" if dec.action == "error" else "info",
                decision_id=dec.id, underlying=underlying, action=dec.action, strategy=ZERO_HERO,
            )
        return dec.model_dump()

    async def _decide(self, dec: Decision, underlying: str) -> None:
        s: Settings = self.settings()
        result = self.scan(underlying)
        dec.spot, dec.expiry = result.spot, result.expiry
        dec.regime = {
            "hours_left": result.hours_left, "hold_minutes": result.hold_minutes,
            "realised_vol": result.realised_vol, "window_open": result.window_open,
            "trigger": result.trigger, "positioning": result.positioning, "gates": result.gates,
            "shadow": s.zh_shadow,
        }
        dec.candidates = result.candidates[:5]
        if not result.ok or result.best is None:
            dec.reason = result.note if not result.window_open else (
                next((g["detail"] for g in result.gates if not g["ok"]), result.note)
            )
            return

        best = result.best
        quote = self.desk.feed.quote(best["symbol"])
        if quote is None:
            dec.reason = f"no fresh quote for {best['symbol']} by the time it was chosen"
            return
        plan = single(quote, "buy", result.spot, ZERO_HERO)
        if plan is None:
            dec.reason = f"{best['symbol']} has no price to buy at"
            return

        why = (
            f"{best['kind']} {best['strike']:,.0f} at {best['paid']:,.2f} — {best['multiple']:.1f}x at "
            f"{best['target_pct']:+.2f}% in {best['hold_minutes']:.0f} min, p={100 * best['p_touch']:.0f}%, "
            f"EV {best['ev']:+.2f}/unit"
        )
        brokers = self.brokers()
        if not brokers:
            dec.reason = f"{why} — auto-trading is off on every active broker"
            return
        await self._open(dec, plan, best, brokers, why, s)

    async def _open(self, dec: Decision, plan: Any, best: dict[str, Any], brokers: list[str], why: str, s: Settings) -> None:
        with session() as db:  # the id makes the request: a re-run of this decision opens nothing twice
            db.add(dec)
            db.commit()
            db.refresh(dec)
        orders: dict[str, Any] = {}
        for broker in brokers:
            try:
                account = await self.trading.brokers[broker].account()
                spent, attempts = self.spent_today(broker)
                qty, sizing = scanner.lots_for_budget(
                    account.equity, spent, attempts, best["paid"], best["contract_value"],
                    best["capacity_lots"], s,
                )
                if qty < 1:
                    raise Refused(sizing)
                if s.zh_shadow:
                    orders[broker] = {
                        "shadow": True, "qty": qty, "sizing": sizing,
                        "premium": round(qty * best["paid"] * best["contract_value"], 4),
                        "equity": round(account.equity, 2),
                    }
                    continue
                view = await self.trading.open(broker, plan, qty, "zerohero", f"zh-{dec.id}", dec.id)
                orders[broker] = {"structure_id": view["id"], "status": view["status"], "qty": qty,
                                  "sizing": sizing, "error": view["error"]}
            except (Refused, BrokerError) as exc:
                orders[broker] = {"refused": str(exc)}
        dec.orders = orders
        opened = [b for b, o in orders.items() if o.get("status") == "open"]
        shadowed = [b for b, o in orders.items() if o.get("shadow")]
        refusals = "; ".join(f"{b}: {o['refused']}" for b, o in orders.items() if o.get("refused"))
        if opened:
            dec.action = "open"
        elif shadowed:
            dec.action = "shadow"
        dec.reason = why + (
            f" — opened on {', '.join(opened)}" if opened
            else f" — SHADOW: would have bought {orders[shadowed[0]]['qty']:,} lots on {', '.join(shadowed)}" if shadowed
            else ""
        ) + (f" — {refusals}" if refusals else "")

    # -- read side -------------------------------------------------------------------------

    def status(self, underlying: str) -> dict[str, Any]:
        s: Settings = self.settings()
        now_ts = time.time()
        w_start, w_end, desc = self.get_window(now_ts)
        budget = []
        for name in s.active_brokers:
            spent, attempts = self.spent_today(name)
            budget.append({"broker": name, "spent": round(spent, 4), "attempts": attempts,
                           "max_attempts": s.zh_max_attempts_day, "auto": s.auto_on(name)})
        seconds_to_window = max(0.0, w_start - now_ts) if now_ts < w_start else 0.0
        return {
            "enabled": s.zh_enabled,
            "shadow": s.zh_shadow,
            "underlying": underlying,
            "budget": budget,
            "window_schedule": {
                "window_open": w_start <= now_ts < w_end,
                "window_start": w_start,
                "window_end": w_end,
                "seconds_to_window": seconds_to_window,
                "hours_to_window": round(seconds_to_window / 3600, 2),
                "description": desc,
            },
            "config": {k: getattr(s, k) for k in (
                "zh_window_start_hours", "zh_window_end_hours", "zh_scan_seconds", "zh_daily_budget_pct",
                "zh_max_attempts_day", "zh_max_lots", "zh_capacity_share_pct", "zh_min_oi", "zh_min_multiple",
                "zh_min_ev", "zh_min_distance_pct", "zh_max_distance_pct", "zh_max_spread_pct",
                "zh_displacement_atr", "zh_max_pain_buffer_pct", "zh_take_profit_pct", "zh_stop_loss_pct",
                "zh_time_stop_minutes",
            )} | {"exit_minutes": s.zh_exit_minutes},
        }

    @staticmethod
    def decisions(underlying: str = "", action: str = "", limit: int = 50) -> list[dict[str, Any]]:
        with session() as db:
            q = select(Decision).where(Decision.strategy == ZERO_HERO)
            if underlying:
                q = q.where(Decision.underlying == underlying)
            if action:
                q = q.where(Decision.action == action)
            return [d.model_dump() for d in db.exec(q.order_by(col(Decision.ts).desc()).limit(min(max(limit, 1), 500))).all()]

    @staticmethod
    def record(days: float = 30) -> dict[str, Any]:
        """The measurement shadow mode exists to produce: how many attempts the scan proposed, and
        of the ones that were really opened, how many reached their target.

        Shadow rows are counted separately and never mixed in. A candidate that was never bought
        has no outcome — guessing one from where price went afterwards would be marking our own
        homework with hindsight, and it is exactly the number this strategy must not fake.
        """
        since = time.time() - days * 86_400
        with session() as db:
            decisions = db.exec(
                select(Decision).where(Decision.strategy == ZERO_HERO, Decision.ts >= since)
            ).all()
            trades = db.exec(
                select(Structure).where(Structure.strategy == ZERO_HERO, Structure.opened_at >= since)
            ).all()
        closed = [st for st in trades if st.status == "closed" and st.pnl is not None]
        wins = [st for st in closed if (st.pnl or 0) > 0]
        paid = sum(max(0.0, -st.entry_cash) + st.fees for st in trades)
        return {
            "days": days,
            "scans": len(decisions),
            "proposed": sum(1 for d in decisions if d.action in ("open", "shadow")),
            "shadowed": sum(1 for d in decisions if d.action == "shadow"),
            "opened": len(trades),
            "closed": len(closed),
            "wins": len(wins),
            "hit_rate_pct": round(100 * len(wins) / len(closed), 1) if closed else None,
            "premium_paid": round(paid, 4),
            "pnl": round(sum(st.pnl or 0 for st in closed), 4) if closed else None,
            "note": "hit rate is over CLOSED trades only; shadow proposals have no outcome and are not counted",
        }
