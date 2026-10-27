"""The directional strategies, run on every closed bar.

Every few seconds the runner folds the stored 1m base into each strategy's timeframe — a fold emits
a bar only once the minutes after it exist, so a forming bar is never handed over — and evaluates
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
from app.core.models import SignalLog
from app.errors import BrokerError, Refused
from app.market.options import PERPETUALS
from app.settings import Settings
from app.strategies import REGISTRY
from app.strategies.base import Context, Decision, net_rr
from app.trading.service import TradingService

log = logging.getLogger(__name__)

BASE_READ = 3_400  # minutes: enough for the longest lookback (200 x 15m) plus slack for holes


class StrategyRunner:
    def __init__(self, trading: TradingService, market: MarketDesk, settings: Any, hub: EventHub) -> None:
        self.trading = trading
        self.market = market
        self.settings: Any = settings
        self.hub = hub
        self.evaluated: dict[tuple[str, str], int] = {}  # (strategy, symbol) -> last bar decided on
        self.latest: dict[tuple[str, str], dict[str, Any]] = {}  # the live readout for the dashboard

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
        for name in s.directional_strategies:
            if not s.strategy_on(name, underlying):
                continue  # enabled, but not for this underlying
            strategy = REGISTRY[name]
            bars = frames.setdefault(strategy.interval, resample.fold(base, "1m", strategy.interval))
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
            self._readout(name, symbol, bar_time, decision, decision.reason)
            if decision.side:
                fired.append((name, decision, bar_time))
        if not fired:
            return f"{symbol}: no signal"
        return await self._trade(underlying, symbol, fired, s)

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
            cost_pct=2 * rate.taker * 100 + spread,
        )

    async def _trade(self, underlying: str, symbol: str, fired: list[tuple[str, Decision, int]], s: Settings) -> str:
        """The best signal on this symbol gets the slot; every other one is refused by name."""
        cost = 2 * self.trading.desk.cached_fee(symbol).taker * 100

        def edge(d: Decision) -> float:
            entry = d.entry or 1.0
            return net_rr(100 * abs((d.target or entry) - entry) / entry, 100 * abs(entry - (d.stop or entry)) / entry, cost)

        fired.sort(key=lambda f: edge(f[1]), reverse=True)
        best_name, best, bar_time = fired[0]
        for name, decision, bt in fired[1:]:
            self._log(name, symbol, REGISTRY[name].interval, bt, decision, "skip",
                      note=f"{REGISTRY[best_name].label} had the better trade ({edge(best):.2f}:1 against {edge(decision):.2f}:1)")

        brokers = [b for b in s.active_brokers if s.auto_on(b)]
        orders: dict[str, Any] = {}
        for broker in brokers:
            try:
                account = await self.trading.brokers[broker].account()
                cv = await self.trading.desk.contract_value(symbol)
                rate = self.trading.desk.cached_fee(symbol).taker
                qty = self.trading.perp_size(account.equity, account.available, best.entry or 0.0, best.stop or 0.0, cv, rate)
                if qty < 1:
                    raise Refused(f"{s.risk_per_trade_pct:g}% of equity {account.equity:,.2f} does not cover one contract's loss at the stop")
                view = await self.trading.open_directional(
                    broker, underlying, best.side or "", qty, best.stop or 0.0, best.target or 0.0,
                    best_name, "auto", f"{best_name}:{symbol}:{bar_time}", best.reason,
                )
                orders[broker] = {"structure_id": view["id"], "status": view["status"], "qty": qty, "error": view["error"]}
            except (Refused, BrokerError) as exc:
                orders[broker] = {"refused": str(exc)}
        if not brokers:
            orders["-"] = {"refused": "auto-trading is off on every active broker"}
        opened = any(o.get("status") == "open" for o in orders.values())
        self._log(best_name, symbol, REGISTRY[best_name].interval, bar_time, best, "open" if opened else "skip", orders=orders)
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
