"""CandleClosed -> strategies -> SignalGenerated."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from tradebuddy.delta import Candle, DeltaClient, OptionQuote
from tradebuddy.events import CandleClosed, EventBus, SignalGenerated, StrategyError
from tradebuddy.store import Store
from tradebuddy.strategies import Context, Strategy
from tradebuddy.stream import PriceBook

log = logging.getLogger(__name__)


def strategy_key(name: str) -> str:
    return f"strategy:{name}"


def pair_key(name: str, symbol: str) -> str:
    return f"pair:{name}:{symbol}"


class MarketData:
    """What a strategy can read: live prices from the stream, history and options from REST."""

    def __init__(self, delta: DeltaClient, prices: PriceBook) -> None:
        self.delta = delta
        self.prices = prices

    def price(self, symbol: str) -> float | None:
        return self.prices.price(symbol)

    async def candles(self, symbol: str, resolution: str, count: int) -> list[Candle]:
        return await self.delta.candles(symbol, resolution, count)

    async def option_chain(self, underlying: str) -> list[OptionQuote]:
        return await self.delta.option_chain(underlying)


@dataclass
class _Stats:
    runs: int = 0
    signals: int = 0
    errors: int = 0
    last_run_at: float = 0.0
    last_ms: float = 0.0
    last_result: str = ""


class StrategyRunner:
    def __init__(self, bus: EventBus, store: Store, market: Any, strategies: list[Strategy], timeout: float = 20.0) -> None:
        self.bus = bus
        self.store = store
        self.market = market
        self.strategies = strategies
        self.timeout = timeout
        self.stats: dict[str, _Stats] = {s.name: _Stats() for s in strategies}

    def is_on(self, strategy: Strategy, symbol: str) -> bool:
        return self.store.enabled(strategy_key(strategy.name)) and self.store.enabled(pair_key(strategy.name, symbol))

    async def on_candle_closed(self, event: CandleClosed) -> None:
        due = [
            s for s in self.strategies
            if s.interval == event.resolution and event.symbol in s.symbols and self.is_on(s, event.symbol)
        ]
        if not due:
            return

        history: list[Candle] = []
        need = max(s.lookback for s in due)
        if need:
            try:
                history = await self._history(event, need)
            except Exception as exc:
                for s in due:
                    self._fail(s, event.symbol, f"candle history unavailable: {exc}")
                return

        await asyncio.gather(*(self._evaluate(s, event, history) for s in due))

    async def _history(self, event: CandleClosed, count: int) -> list[Candle]:
        bars = [b for b in await self.market.candles(event.symbol, event.resolution, count) if b.time <= event.bar_time]
        if (not bars or bars[-1].time < event.bar_time) and event.candle is not None:
            bars.append(event.candle)  # REST has not caught up with the stream yet
        return bars[-count:]

    async def _evaluate(self, strategy: Strategy, event: CandleClosed, history: list[Candle]) -> None:
        stats = self.stats[strategy.name]
        candles = history[-strategy.lookback:] if strategy.lookback else []
        if strategy.lookback and (not candles or candles[-1].time != event.bar_time):
            self._fail(strategy, event.symbol, "the bar that just closed is not in the history yet")
            return

        started = time.perf_counter()
        stats.runs += 1
        stats.last_run_at = time.time()
        try:
            ctx = Context(event.symbol, event.resolution, event.bar_time, candles, self.market)
            signal = await asyncio.wait_for(strategy.on_candle(ctx), self.timeout)
        except Exception as exc:
            self._fail(strategy, event.symbol, repr(exc))
            return
        finally:
            stats.last_ms = round(1000 * (time.perf_counter() - started), 1)

        if signal is None:
            stats.last_result = f"{event.symbol}: no signal"
            return
        if signal.side not in ("buy", "sell"):
            self._fail(strategy, event.symbol, f"invalid side {signal.side!r}")
            return

        stats.signals += 1
        stats.last_result = f"{event.symbol}: {signal.side.upper()} — {signal.reason}"
        self.bus.publish(
            SignalGenerated(
                strategy=strategy.name,
                version=strategy.version,
                symbol=event.symbol,
                side=signal.side,
                reason=signal.reason,
                bar_time=event.bar_time,
                size=strategy.size,
                stop_loss_pct=signal.stop_loss_pct,
                take_profit_pct=signal.take_profit_pct,
            )
        )

    def _fail(self, strategy: Strategy, symbol: str, error: str) -> None:
        stats = self.stats[strategy.name]
        stats.errors += 1
        stats.last_result = f"{symbol}: error — {error}"
        log.warning("strategy_failed strategy=%s symbol=%s error=%s", strategy.name, symbol, error)
        self.bus.publish(StrategyError(strategy=strategy.name, symbol=symbol, error=error))
