"""CandleClosed -> evaluation jobs -> StrategyEvaluated -> SignalGenerated.

Evaluation is pluggable: inline on the event loop (single process) or on
Celery workers (distributed). Either way the result comes back as a
StrategyEvaluated event, so everything after it is the same code.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from tradebuddy.delta import RESOLUTION_SECONDS, Candle, DeltaClient, OptionQuote
from tradebuddy.events import CandleClosed, EventBus, SignalGenerated, StrategyError, StrategyEvaluated
from tradebuddy.store import Store
from tradebuddy.strategies import Context, Strategy

log = logging.getLogger(__name__)


def strategy_key(name: str) -> str:
    return f"strategy:{name}"


def pair_key(name: str, symbol: str) -> str:
    return f"pair:{name}:{symbol}"


class Prices(Protocol):
    def price(self, symbol: str) -> float | None: ...


class MarketData:
    """What a strategy can read: live prices, history and options from REST."""

    def __init__(self, delta: DeltaClient, prices: Prices) -> None:
        self.delta = delta
        self.prices = prices

    def price(self, symbol: str) -> float | None:
        return self.prices.price(symbol)

    async def candles(self, symbol: str, resolution: str, count: int) -> list[Candle]:
        return await self.delta.candles(symbol, resolution, count)

    async def option_chain(self, underlying: str) -> list[OptionQuote]:
        return await self.delta.option_chain(underlying)


def make_job(strategy: Strategy, event: CandleClosed, prices: dict[str, float], data_env: str) -> dict[str, Any]:
    """Everything a worker needs, as plain JSON."""
    return {
        "strategy": strategy.name,
        "version": strategy.version,
        "symbol": event.symbol,
        "resolution": event.resolution,
        "bar_time": event.bar_time,
        "candle": asdict(event.candle) if event.candle else None,
        "data_env": data_env,
        "prices": prices,
        "dispatched_at": time.time(),
    }


async def evaluate(strategy: Strategy, job: dict[str, Any], market: Any, worker: str = "inline", timeout: float = 20.0) -> StrategyEvaluated:
    """Run one strategy on one closed bar. Never raises: failures come back in `error`."""
    started = time.perf_counter()
    symbol, resolution, bar_time = job["symbol"], job["resolution"], job["bar_time"]

    def result(**fields: Any) -> StrategyEvaluated:
        return StrategyEvaluated(
            strategy=strategy.name, version=strategy.version, symbol=symbol, resolution=resolution, bar_time=bar_time,
            ms=round(1000 * (time.perf_counter() - started), 1), worker=worker, dispatched_at=job.get("dispatched_at", 0.0), **fields,
        )

    if job.get("version", strategy.version) != strategy.version:
        return result(error=f"version mismatch: dispatched v{job['version']}, worker has v{strategy.version} — redeploy workers")

    candles: list[Candle] = []
    if strategy.lookback:
        try:
            bars = [b for b in await market.candles(symbol, resolution, strategy.lookback) if b.time <= bar_time]
        except Exception as exc:
            return result(error=f"candle history unavailable: {exc}")
        if (not bars or bars[-1].time < bar_time) and job.get("candle"):
            bars.append(Candle(**job["candle"]))  # REST has not caught up with the stream yet
        candles = bars[-strategy.lookback:]
        if not candles or candles[-1].time != bar_time:
            return result(error="the bar that just closed is not in the history yet")

    try:
        signal = await asyncio.wait_for(strategy.on_candle(Context(symbol, resolution, bar_time, candles, market)), timeout)
    except Exception as exc:
        return result(error=repr(exc))
    if signal is None:
        return result()
    if signal.side not in ("buy", "sell"):
        return result(error=f"invalid side {signal.side!r}")
    return result(side=signal.side, reason=signal.reason, stop_loss_pct=signal.stop_loss_pct, take_profit_pct=signal.take_profit_pct)


class Evaluator(Protocol):
    name: str

    async def submit(self, strategy: Strategy, job: dict[str, Any]) -> None:
        """Start evaluating; the result arrives later as a StrategyEvaluated event."""
        ...

    def stats(self) -> dict[str, Any]: ...


class InlineEvaluator:
    """Evaluates on this process's event loop. No Celery, no Redis."""

    name = "inline"

    def __init__(self, bus: EventBus, market: MarketData) -> None:
        self.bus = bus
        self.market = market
        self.submitted = 0
        self._tasks: set[asyncio.Task] = set()

    async def submit(self, strategy: Strategy, job: dict[str, Any]) -> None:
        self.submitted += 1
        task = asyncio.create_task(self._run(strategy, job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, strategy: Strategy, job: dict[str, Any]) -> None:
        self.bus.publish(await evaluate(strategy, job, self.market))

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks))

    def stats(self) -> dict[str, Any]:
        return {"mode": self.name, "submitted": self.submitted, "in_flight": len(self._tasks)}


@dataclass
class _Stats:
    runs: int = 0
    signals: int = 0
    errors: int = 0
    in_flight: int = 0
    last_run_at: float = 0.0
    last_ms: float = 0.0
    last_latency_ms: float = 0.0
    last_worker: str = ""
    last_result: str = ""


class StrategyRunner:
    def __init__(
        self, bus: EventBus, store: Store, strategies: list[Strategy], evaluator: Evaluator,
        prices: Callable[[], dict[str, float]], data_env: Callable[[], str],
    ) -> None:
        self.bus = bus
        self.store = store
        self.strategies = strategies
        self.by_name = {s.name: s for s in strategies}
        self.evaluator = evaluator
        self.prices = prices
        self.data_env = data_env
        self.stats: dict[str, _Stats] = {s.name: _Stats() for s in strategies}

    def is_on(self, strategy: Strategy, symbol: str) -> bool:
        return self.store.enabled(strategy_key(strategy.name)) and self.store.enabled(pair_key(strategy.name, symbol))

    async def on_candle_closed(self, event: CandleClosed) -> None:
        for s in self.strategies:
            if s.interval == event.resolution and event.symbol in s.symbols and self.is_on(s, event.symbol):
                stats = self.stats[s.name]
                stats.in_flight += 1
                try:
                    await self.evaluator.submit(s, make_job(s, event, self.prices(), self.data_env()))
                except Exception as exc:
                    stats.in_flight -= 1
                    stats.errors += 1
                    stats.last_result = f"{event.symbol}: error — {exc}"
                    self.bus.publish(StrategyError(strategy=s.name, symbol=event.symbol, error=f"not dispatched: {exc}"))

    async def on_evaluated(self, e: StrategyEvaluated) -> None:
        stats = self.stats.get(e.strategy)
        if stats is None:
            log.warning("result_for_unknown_strategy strategy=%s", e.strategy)
            return
        stats.in_flight = max(0, stats.in_flight - 1)
        stats.runs += 1
        stats.last_run_at = e.ts
        stats.last_ms = e.ms
        stats.last_worker = e.worker
        stats.last_latency_ms = round(1000 * (e.ts - e.dispatched_at), 1) if e.dispatched_at else e.ms

        # A result that arrives more than a bar late would trade on an old market.
        step = RESOLUTION_SECONDS.get(e.resolution, 60)
        late = time.time() - (e.bar_time + step)
        if not e.error and e.side and late > step:
            self._fail(stats, e, f"stale result dropped: finished {late:.0f}s after the bar closed")
            return
        if e.error:
            self._fail(stats, e, e.error)
            return
        if not e.side:
            stats.last_result = f"{e.symbol}: no signal"
            return

        stats.signals += 1
        stats.last_result = f"{e.symbol}: {e.side.upper()} — {e.reason}"
        strategy = self.by_name[e.strategy]
        self.bus.publish(
            SignalGenerated(
                strategy=e.strategy, version=e.version, symbol=e.symbol, side=e.side, reason=e.reason, bar_time=e.bar_time,
                size=strategy.size, stop_loss_pct=e.stop_loss_pct, take_profit_pct=e.take_profit_pct,
            )
        )

    def _fail(self, stats: _Stats, e: StrategyEvaluated, error: str) -> None:
        stats.errors += 1
        stats.last_result = f"{e.symbol}: error — {error}"
        log.warning("strategy_failed strategy=%s symbol=%s worker=%s error=%s", e.strategy, e.symbol, e.worker, error)
        self.bus.publish(StrategyError(strategy=e.strategy, symbol=e.symbol, error=error))
