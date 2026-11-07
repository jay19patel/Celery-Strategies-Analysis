"""The directional strategies, run on every closed bar.

Every few seconds the runner folds the stored 1m base into each strategy's timeframe — a fold emits
a bar only once the minutes after it exist, so a forming bar is never handed over; a strategy whose
lookback reaches further back than the base is folded from the stored 15m series instead — and evaluates
each enabled strategy once per new closed bar. Late is stale: a bar that closed more than one bar
ago (the engine was down) is skipped, not traded.

There is one position per symbol per broker, so strategies firing on the same bar compete for one
slot. The best reward-to-risk after costs takes it and the rest are refused by name: first-come
would hand the slot to whichever strategy runs on the faster bar, not the one with the better trade.

Each strategy sets its own stop and target. The size comes from the stop: the position whose loss
there is `risk_per_trade_pct` of that broker's equity (`TradingService.perp_size`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlmodel import col, select

from app.analysis import resample
from app.analysis.service import MarketDesk, candles
from app.core.db import session
from app.core.events import EventHub
from app.core.models import SignalLog, Structure
from app.errors import BrokerError, Refused
from app.market.options import PERPETUALS
from app.settings import Settings
from app.strategies import REGISTRY
from app.strategies.base import Context, Decision, net_rr
from app.trading.risk import day_start, trading_day
from app.trading.service import TradingService

log = logging.getLogger(__name__)

BASE_READ = 3_400  # minutes: enough for the longest lookback (200 x 15m) plus slack for holes
FLOW_MINUTES = 5  # the tape is read in 5-minute bars, the timeframe the flow strategies trade
BIAS_GATE = 0.5  # a day's bias this strong refuses a signal against it


class StrategyRunner:
    def __init__(self, trading: TradingService, market: MarketDesk, settings: Any, hub: EventHub) -> None:
        self.trading = trading
        self.market = market
        self.settings: Any = settings
        self.hub = hub
        self.evaluated: dict[tuple[str, str], int] = {}  # (strategy, symbol) -> last bar decided on
        self.latest: dict[tuple[str, str], dict[str, Any]] = {}  # the live readout for the dashboard
        self.history: dict[tuple[str, str], tuple[int, list[dict[str, Any]]]] = {}  # (symbol, interval) -> (newest 15m bar, folded)

    async def run(self) -> str:
        s: Settings = self.settings()
        if not s.directional_strategies:
            return "no directional strategy is enabled"
        out = []
        for underlying in s.underlyings:
            out.append(await self._symbol(underlying, s))
        return "; ".join(out)

    async def _symbol(self, underlying: str, s: Settings) -> str:
        symbol = PERPETUALS[underlying]
        base = await asyncio.to_thread(self.trading.feed.bars, symbol, BASE_READ, "1m")
        if not base:
            return f"{symbol}: no 1m bars yet"
        now = time.time()
        frames: dict[str, list[dict[str, Any]]] = {}
        fired: list[tuple[str, Decision, int]] = []
        stopped = self._stopped_today(underlying, day_start(s.day_timezone, trading_day(s.day_timezone)))
        for name in s.directional_strategies:
            if not s.strategy_on(name, underlying):
                continue  # enabled, but not for this underlying
            strategy = REGISTRY[name]
            if strategy.interval not in frames:
                frames[strategy.interval] = await self._bars(symbol, strategy, base)
            bars = frames[strategy.interval]
            if not bars:
                continue
            bar_time = int(bars[-1]["time"])
            if self.evaluated.get((name, symbol)) == bar_time:
                continue
            self.evaluated[(name, symbol)] = bar_time
            span = resample.seconds(strategy.interval)
            if now - (bar_time + span) > span:
                self._readout(name, symbol, bar_time, None, "late: the bar closed more than one bar ago — not traded")
                continue
            try:
                decision = strategy.decide(await self._context(strategy, underlying, symbol, bars))
            except Exception as exc:
                log.exception("strategy_failed strategy=%s symbol=%s", name, symbol)
                self._readout(name, symbol, bar_time, None, f"error: {type(exc).__name__}: {exc}")
                self._log(name, symbol, strategy.interval, bar_time, Decision(None, str(exc)[:500]), "error")
                continue
            if decision.side and name in stopped:
                self._readout(name, symbol, bar_time, decision, f"{decision.reason} — not taken: stopped out on {symbol} earlier today")
                continue
            if decision.side and (against := await self._against_bias(underlying, decision.side)):
                self._readout(name, symbol, bar_time, decision, f"{decision.reason} — {against}")
                continue
            self._readout(name, symbol, bar_time, decision, decision.reason)
            if decision.side:
                fired.append((name, decision, bar_time))
        if not fired:
            return f"{symbol}: no signal"
        return await self._trade(underlying, symbol, fired, s)

    async def _bars(self, symbol: str, strategy: Any, base: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The strategy's closed bars: from the 1m base when it reaches back far enough, else from the
        stored 15m series — read again only when a newer 15m bar has been stored."""
        span = resample.seconds(strategy.interval)
        if strategy.lookback * span <= BASE_READ * 60:
            return resample.fold(base, "1m", strategy.interval)
        newest = self.trading.feed.latest_bar_time(symbol, "15m")
        cached = self.history.get((symbol, strategy.interval))
        if cached is None or cached[0] != newest:
            rows = await asyncio.to_thread(self.trading.feed.bars, symbol, (strategy.lookback + 2) * span // 900, "15m")
            cached = self.history[(symbol, strategy.interval)] = (newest, resample.fold(rows, "15m", strategy.interval))
        return cached[1]

    async def _context(self, strategy: Any, underlying: str, symbol: str, bars: list[dict[str, Any]]) -> Context:
        rate = await self.trading.desk.fee_rate(symbol)
        perp = self.trading.feed.perp(underlying)
        spread = 0.0
        if perp and perp.stats.get("bid") and perp.stats.get("ask"):
            spread = 100 * (perp.stats["ask"] - perp.stats["bid"]) / perp.mark
        return Context(
            symbol=symbol, underlying=underlying, interval=strategy.interval,
            candles=candles(bars[-strategy.lookback:]),
            outlook=await self.market.outlook(underlying) if strategy.wants_outlook else {},
            structure=await self.market.structure(underlying) if strategy.wants_structure else {},
            options_history=self.market.options_history(underlying, 8) if strategy.wants_options_history else [],
            flow=await self.market.flow(underlying, FLOW_MINUTES) if strategy.wants_flow else {},
            bias=await self.market.bias(underlying) if strategy.wants_bias else {},
            cost_pct=2 * rate.taker * 100 + spread + self._funding_pct(underlying),
        )

    @staticmethod
    def _stopped_today(underlying: str, since: float) -> set[str]:
        """The strategies stopped out on this underlying's perpetual since the day began. The same
        signal a bar later buys the same idea back into the same market: the 9/15 cross on BTC was
        stopped at 21:40 on 4 Nov and re-entered fifteen minutes later, into a second stop."""
        with session() as db:
            rows = db.exec(select(Structure.strategy, Structure.close_reason).where(
                Structure.underlying == underlying, Structure.source == "auto", col(Structure.stop_price).is_not(None),
                col(Structure.closed_at) >= since,
            )).all()
        return {strategy for strategy, reason in rows if reason.startswith("stop loss")}

    async def _against_bias(self, underlying: str, side: str) -> str:
        """Why a signal is refused for fighting a strong day bias, or "". Every strategy passes this,
        the ones written before the bias existed included: a short into a day bid this hard is a
        trade against the auction, whatever its own indicator said."""
        b = await self.market.bias(underlying)
        score = float(b.get("score") or 0.0) if b.get("ready") else 0.0
        if (side == "sell" and score >= BIAS_GATE) or (side == "buy" and score <= -BIAS_GATE):
            return f"not taken: today's bias is {b['label']} ({score:+.2f}), and this trade is against it"
        return ""

    def _funding_pct(self, underlying: str) -> float:
        """One funding payment at the current rate, as % of notional. A position held for hours pays
        it at least once, and which side pays is not known before the side is, so it is charged as a cost."""
        perp = self.trading.feed.perp(underlying)
        return abs((perp.stats.get("funding_rate_pct") if perp else None) or 0.0)

    async def _trade(self, underlying: str, symbol: str, fired: list[tuple[str, Decision, int]], s: Settings) -> str:
        """The best signal on this symbol gets the slot; every other one is refused by name."""
        cost = 2 * self.trading.desk.cached_fee(symbol).taker * 100 + self._funding_pct(underlying)

        def edge(d: Decision) -> float:
            entry = d.entry or 1.0
            return net_rr(100 * abs((d.target or entry) - entry) / entry, 100 * abs(entry - (d.stop or entry)) / entry, cost)

        fired.sort(key=lambda f: edge(f[1]), reverse=True)
        best_name, best, bar_time = fired[0]
        # A signal that does not trade is shown live and not stored: only opens and errors are kept.
        for name, decision, bt in fired[1:]:
            self._readout(name, symbol, bt, decision,
                          f"{decision.reason} — {REGISTRY[best_name].label} had the better trade ({edge(best):.2f}:1 against {edge(decision):.2f}:1)")

        brokers = [b for b in s.active_brokers if s.auto_on(b)]
        orders: dict[str, Any] = {}
        for broker in brokers:
            try:
                account = await self.trading.brokers[broker].account()
                x, sized = self.trading.risk_scale(broker, best_name, account.equity)
                if x <= 0:
                    raise Refused(sized)
                cv = await self.trading.desk.contract_value(symbol)
                rate = self.trading.desk.cached_fee(symbol).taker
                qty = self.trading.perp_size(account.equity * x, account.available, best.entry or 0.0, best.stop or 0.0, cv, rate)
                if qty < 1:
                    raise Refused(f"{x * s.risk_per_trade_pct:g}% of equity {account.equity:,.2f} does not cover one contract's loss at the stop")
                view = await self.trading.open_directional(
                    broker, underlying, best.side or "", qty, best.stop or 0.0, best.target or 0.0,
                    best_name, "auto", f"{best_name}:{symbol}:{bar_time}", best.reason, REGISTRY[best_name].max_hold_hours,
                )
                orders[broker] = {"structure_id": view["id"], "status": view["status"], "qty": qty, "error": view["error"], "sized": sized}
            except (Refused, BrokerError) as exc:
                orders[broker] = {"refused": str(exc)}
        if not brokers:
            orders["-"] = {"refused": "auto-trading is off on every active broker"}
        opened = any(o.get("status") == "open" for o in orders.values())
        if opened:
            self._log(best_name, symbol, REGISTRY[best_name].interval, bar_time, best, "open", orders=orders)
        else:
            refusals = "; ".join(f"{b}: {o.get('refused') or o.get('error') or o.get('status')}" for b, o in orders.items())
            self._readout(best_name, symbol, bar_time, best, f"{best.reason} — not opened: {refusals}")
        return f"{symbol}: {best_name} {best.side} -> {'opened' if opened else 'not opened'}"

    def _readout(self, name: str, symbol: str, bar_time: int, decision: Decision | None, reason: str) -> None:
        self.latest[(name, symbol)] = {
            "strategy": name, "symbol": symbol, "bar_time": bar_time, "at": time.time(), "reason": reason,
            "decision": decision.to_dict() if decision and decision.side else None,
        }

    def _log(self, name: str, symbol: str, interval: str, bar_time: int, d: Decision, action: str,
             note: str = "", orders: dict[str, Any] | None = None) -> None:
        refusals = "; ".join(f"{b}: {o['refused']}" for b, o in (orders or {}).items() if o.get("refused"))
        reason = " — ".join(x for x in (d.reason, note, refusals) if x)
        with session() as s:
            row = SignalLog(
                ts=time.time(), strategy=name, symbol=symbol, interval=interval, bar_time=bar_time, side=d.side or "",
                action=action, reason=reason[:1000], entry=d.entry, stop=d.stop, target=d.target, rr=d.rr, orders=orders,
            )
            s.add(row)
            s.commit()
        level = "error" if action == "error" else "info"
        self.hub.publish(
            "Signal", f"{REGISTRY[name].label} {d.side or ''} {symbol}: {action} — {reason[:300]}", level,
            strategy=name, symbol=symbol, action=action, side=d.side,
        )

    def status(self) -> list[dict[str, Any]]:
        s: Settings = self.settings()
        out = []
        for name, strategy in REGISTRY.items():
            out.append(strategy.describe() | {
                "enabled": name in s.directional_strategies,
                "symbols": s.strategy_underlyings(name),  # the enabled underlyings it runs on
                "latest": [v for (n, _sym), v in self.latest.items() if n == name],
            })
        return out

    @staticmethod
    def signals(strategy: str = "", action: str = "", limit: int = 100) -> list[dict[str, Any]]:
        with session() as s:
            q = select(SignalLog)
            if strategy:
                q = q.where(SignalLog.strategy == strategy)
            if action:
                q = q.where(SignalLog.action == action)
            return [r.model_dump() for r in s.exec(q.order_by(col(SignalLog.ts).desc()).limit(min(max(limit, 1), 1000))).all()]
