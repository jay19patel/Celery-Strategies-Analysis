"""CandleClosed -> evaluation jobs -> StrategyEvaluated -> SignalGenerated.

Evaluation is pluggable: inline on the event loop (single process) or on
Celery workers (distributed). Either way the result comes back as a
StrategyEvaluated event, so everything after it is the same code.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from tradebuddy.delta import RESOLUTION_SECONDS, Candle, DeltaClient, OptionQuote
from tradebuddy.events import CandleClosed, EventBus, SignalGenerated, StrategyError, StrategyEvaluated, TradeSkipped
from tradebuddy.settings import RANGES
from tradebuddy.store import Store
from tradebuddy.strategies import Context, Strategy

log = logging.getLogger(__name__)


PAUSED = "no fresh live price from the WebSocket — strategy paused until prices return"


def strategy_key(name: str) -> str:
    return f"strategy:{name}"


def pair_key(name: str, symbol: str) -> str:
    return f"pair:{name}:{symbol}"


class Prices(Protocol):
    def price(self, symbol: str) -> float | None: ...


OPTIONS_HISTORY_STEP = 900  # the 15-minute rows options_history keeps for good


class MarketData:
    """What a strategy can read: live prices, history and options from REST, plus the options numbers
    the engine has recorded. `options` is the engine's store (a callable) or, on a worker, the rows the
    engine sent with the job — a worker has no database, and strategy state cannot live in a worker."""

    def __init__(self, delta: DeltaClient, prices: Prices, options: Callable[[str, float], list[dict[str, Any]]] | None = None) -> None:
        self.delta = delta
        self.prices = prices
        self.options = options

    async def option_history(self, underlying: str, hours: float) -> list[dict[str, Any]]:
        if self.options is None or hours <= 0:
            return []
        return await asyncio.to_thread(self.options, underlying, time.time() - hours * 3600)

    def price(self, symbol: str) -> float | None:
        return self.prices.price(symbol)

    async def candles(self, symbol: str, resolution: str, count: int) -> list[Candle]:
        return await self.delta.candles(symbol, resolution, count)

    async def option_chain(self, underlying: str) -> list[OptionQuote]:
        return await self.delta.option_chain(underlying)

    async def option_summary(self, underlying: str) -> dict[str, Any] | None:
        """The whole options book for `underlying` (options.summarize): ATM IV, skew, put/call, walls."""
        from tradebuddy.options import summarize

        return summarize(underlying, await self.delta.option_tickers(underlying), None)


def make_job(
    strategy: Strategy, event: CandleClosed, prices: dict[str, float], data_env: str,
    options_history: Callable[[str, float], list[dict[str, Any]]] | None = None,
    outlook: dict[str, Any] | None = None,
    candles: list[Candle] | None = None,
    structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything a worker needs, as plain JSON, including the bars.

    The bars used to be fetched by the evaluation itself, once per strategy per symbol: five
    strategies on the 15-minute close meant ten REST calls for one series the engine already had
    on disk. They are read from the store once per symbol per bar now and sent with the job, so a
    worker makes no market request at all — the same reasoning as `options_history` and `outlook`,
    which were already carried this way."""
    job = {
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
    if strategy.options_history_hours and options_history is not None:
        job["options_history"] = options_history(event.symbol, strategy.options_history_hours)
    if strategy.wants_outlook and outlook is not None:
        job["outlook"] = outlook
    if strategy.wants_structure and structure is not None:
        job["structure"] = structure
    # Only when there is actually something to send. An empty read — a fresh database, a symbol
    # the collector has not reached, a hole — must leave the key absent so the evaluation falls
    # back to REST. Attaching `[]` instead would hand every strategy zero bars and fail them all
    # with "the bar that just closed is not in the history yet", which is true and useless.
    if candles and strategy.lookback:
        job["candles"] = [asdict(c) for c in candles[-strategy.lookback:]]
    return job


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
        sent = job.get("candles")
        if sent is not None:
            bars = [Candle(**c) for c in sent]
        else:
            # No bars in the job: an evaluator that predates them, or a store with a hole in it.
            # Falling back to REST keeps the strategy running rather than failing it.
            try:
                bars = [b for b in await market.candles(symbol, resolution, strategy.lookback) if b.time <= bar_time]
            except Exception as exc:
                return result(error=f"candle history unavailable: {exc}")
        if (not bars or bars[-1].time < bar_time) and job.get("candle"):
            bars.append(Candle(**job["candle"]))  # the store has not caught up with the stream yet
        candles = bars[-strategy.lookback:]
        if not candles or candles[-1].time != bar_time:
            return result(error="the bar that just closed is not in the history yet")

    try:
        ctx = Context(symbol, resolution, bar_time, candles, market, job.get("outlook") or {}, job.get("structure") or {})
        signal = await asyncio.wait_for(strategy.on_candle(ctx), timeout)
    except Exception as exc:
        return result(error=repr(exc))
    if signal is None:
        return result()
    if signal.side not in ("buy", "sell"):
        return result(error=f"invalid side {signal.side!r}")
    if strategy.is_default_sl_tp:
        return result(side=signal.side, reason=signal.reason)  # Settings' stop loss and take profit apply
    if error := levels_error(signal.stop_loss_pct, signal.take_profit_pct):
        return result(error=f"is_default_sl_tp is False, so the signal must set its own levels: {error}")
    return result(side=signal.side, reason=signal.reason, stop_loss_pct=signal.stop_loss_pct, take_profit_pct=signal.take_profit_pct)


def levels_error(stop_loss_pct: Any, take_profit_pct: Any) -> str:
    """A strategy's own stop loss and take profit, as % from entry, within the ranges Settings allows."""
    for name, value, (lo, hi) in (("stop_loss_pct", stop_loss_pct, RANGES["stop_loss_pct"]), ("take_profit_pct", take_profit_pct, RANGES["take_profit_pct"])):
        if isinstance(value, bool) or not isinstance(value, int | float) or not lo <= value <= hi:
            return f"{name} must be a number from {lo:g} to {hi:g} (got {value!r})"
    return ""


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
    paused: int = 0  # bars not evaluated because the symbol had no fresh live price
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
        options_history: Callable[[str, float], list[dict[str, Any]]] | None = None,
        outlook: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        structure: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        bars: Callable[[str, str, int], list[Candle]] | None = None,
    ) -> None:
        self.bus = bus
        self.store = store
        self.strategies = strategies
        self.by_name = {s.name: s for s in strategies}
        self.evaluator = evaluator
        self.prices = prices
        self.data_env = data_env
        self.options_history = options_history  # (symbol, hours) -> stored options rows, for the job
        self.outlook = outlook  # symbol -> the engine's directional view, for the job
        self.structure = structure  # symbol -> the engine's market-structure read, for the job
        # (symbol, interval, count) -> stored bars. Read once per symbol per close and shared by
        # every strategy on that bar: they all want the same series, only different depths of it.
        self.bars = bars
        self.stats: dict[str, _Stats] = {s.name: _Stats() for s in strategies}
        self._paused: set[tuple[str, str]] = set()  # (strategy, symbol) waiting for prices to return

    def is_on(self, strategy: Strategy, symbol: str) -> bool:
        """Both switches on. A strategy with `default_on = False` is off until someone says
        otherwise — a toggle row that does not exist yet means "never asked", not "yes"."""
        default = strategy.default_on
        return (self.store.enabled(strategy_key(strategy.name), default)
                and self.store.enabled(pair_key(strategy.name, symbol), default))

    async def on_candle_closed(self, event: CandleClosed) -> None:
        prices = self.prices()
        due = [s for s in self.strategies
               if s.interval == event.resolution and event.symbol in s.symbols and self.is_on(s, event.symbol)]
        # One read, deepest lookback, shared. Each job then carries its own slice of it.
        series: list[Candle] | None = None
        if self.bars is not None and (deepest := max((s.lookback for s in due), default=0)):
            series = await asyncio.to_thread(self.bars, event.symbol, event.resolution, deepest)
        for s in due:
            stats = self.stats[s.name]
            if event.symbol not in prices:
                self._pause(s, event.symbol, stats)
                continue
            if (s.name, event.symbol) in self._paused:
                self._paused.discard((s.name, event.symbol))
                log.info("strategy_resumed strategy=%s symbol=%s", s.name, event.symbol)
            stats.in_flight += 1
            try:
                view = await self.outlook(event.symbol) if (s.wants_outlook and self.outlook) else None
                shape = await self.structure(event.symbol) if (s.wants_structure and self.structure) else None
                await self.evaluator.submit(
                    s, make_job(s, event, prices, self.data_env(), self.options_history, view, series, shape))
            except Exception as exc:
                stats.in_flight -= 1
                stats.errors += 1
                stats.last_result = f"{event.symbol}: error — {exc}"
                self.bus.publish(StrategyError(strategy=s.name, symbol=event.symbol, error=f"not dispatched: {exc}"))

    def _pause(self, s: Strategy, symbol: str, stats: _Stats) -> None:
        """No live price, no evaluation: a signal could not be traded anyway. Said once per outage,
        not once per bar, so a feed that is down for a day does not flood the event log."""
        stats.paused += 1
        stats.last_result = f"{symbol}: paused — no fresh live price from the WebSocket"
        if (s.name, symbol) in self._paused:
            return
        self._paused.add((s.name, symbol))
        log.warning("strategy_paused strategy=%s symbol=%s reason=no_fresh_price", s.name, symbol)
        self.bus.publish(TradeSkipped(strategy=s.name, symbol=symbol, side="", broker="", reason=PAUSED))

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
