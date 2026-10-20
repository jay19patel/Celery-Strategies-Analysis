"""The market analyst: every 5 minutes, one MarketAnalysis per symbol.

    candles, OI, funding (REST)  +  latest OptionsSnapshot and MarketStats (from the engine)
        -> insights.market_context -> insights.insights          rule-based, always, milliseconds

Rules over measured numbers, nothing else: every figure on the Market page can be recomputed from
the bars it names. It reads market data and settings; it writes nothing but events. Distributed, it
is its own process (`python -m tradebuddy analyst`) and PUSHes its results to the engine like a
Celery worker; in a single process the System runs it and it publishes onto the bus.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from tradebuddy.delta import RESOLUTION_SECONDS, DeltaClient
from tradebuddy.events import MarketAnalysis
from tradebuddy.insights import insights, lean, market_context
from tradebuddy.jobs import NULL_JOB
from tradebuddy.options import UNDERLYINGS
from tradebuddy.settings import Settings

log = logging.getLogger(__name__)

HISTORY_BARS = 700  # 7 days of 15m bars plus room: 7-day realised vol, EMA200


class Analyst:
    def __init__(
        self, client: Callable[[], DeltaClient], settings: Callable[[], Settings], publish: Callable[[Any], None],
        options: Callable[[str], dict[str, Any] | None], stats: Callable[[str], dict[str, Any] | None],
        every: float = 300.0, symbols: tuple[str, ...] = tuple(UNDERLYINGS), http: Any = None,
    ) -> None:
        self.client, self.settings, self.publish = client, settings, publish
        self.options, self.stats = options, stats
        self.every, self.symbols, self.http = every, symbols, http
        self.runs = 0
        self.last_error = ""
        self.job = NULL_JOB

    def status(self) -> dict[str, Any]:
        return {"runs": self.runs, "last_error": self.last_error}

    async def run(self) -> None:
        await asyncio.sleep(10)  # let the first options snapshot and prices arrive
        while True:
            try:
                with self.job.tick() as job:
                    found = await self.cycle()
                    job.note = f"{len(found)} symbols"
            except Exception:
                log.exception("analyst_cycle_failed")
            # Wake 20s after each 5-minute mark: the 15m bar that just closed is in the REST history by then.
            await asyncio.sleep(self.every - (time.time() % self.every) + 20)

    async def cycle(self) -> list[MarketAnalysis]:
        started = time.perf_counter()
        results = {sym: await self.analyse(sym) for sym in self.symbols}
        out = []
        for sym, r in results.items():
            r["context"]["took_ms"] = round(1000 * (time.perf_counter() - started))
            event = MarketAnalysis(symbol=sym, context=r["context"], insights=r["insights"])
            self.publish(event)
            out.append(event)
        self.runs += 1
        return out

    async def analyse(self, symbol: str) -> dict[str, Any]:
        client = self.client()
        errors: list[str] = []

        async def fetch(series: str) -> list:
            try:
                return await client.candles(series, "15m", HISTORY_BARS)
            except Exception as exc:
                errors.append(f"{series}: {exc}")
                return []

        candles, oi, funding = await asyncio.gather(fetch(symbol), fetch(f"OI:{symbol}"), fetch(f"FUNDING:{symbol}"))
        opts = self.options(symbol)
        ctx = market_context(symbol, candles, oi, funding, self.stats(symbol), opts)
        found = insights(ctx)
        ctx["lean"] = lean(found)
        ctx["errors"] = errors
        self.last_error = "; ".join(errors)[:200]
        return {"context": ctx, "insights": found}


def for_system(system: Any) -> Analyst:
    """The analyst inside a single-process System: its clients, prices and options, publishing on its bus."""
    return Analyst(
        client=lambda: system.market_client, settings=lambda: system.settings, publish=system.bus.publish,
        options=lambda sym: (system.options_latest.get(sym) or {}).get("summary"),
        stats=lambda sym: (system.prices.snapshot().get(sym) or {}).get("stats"),
    )


async def backfill(db_path: str, symbols: list[str], days: int, interval: str = "15m", client: DeltaClient | None = None) -> dict[str, int]:
    """Fill the bars table with `days` of history per symbol, so base rates and backtests do not
    have to wait for the system to have been running that long.

    Safe to re-run: the store ignores a bar it already has, so this only ever adds what is missing."""
    from tradebuddy.store import Store

    own = client is None
    client = client or DeltaClient("https://api.india.delta.exchange")  # history is public
    store = Store(db_path)
    step = RESOLUTION_SECONDS[interval]
    end = int(time.time()) // step * step  # the last boundary: never store a bar still forming
    start = end - days * 86_400
    written = {}
    try:
        for symbol in symbols:
            candles = await client.history_range(symbol, interval, start, end)
            oi = await client.history_range(f"OI:{symbol}", interval, start, end)
            funding = await client.history_range(f"FUNDING:{symbol}", interval, start, end)
            closed = [c for c in candles if c.time + step <= end]
            written[symbol] = await asyncio.to_thread(
                store.record_bars, symbol, interval, closed,
                {int(c.time): c.close for c in oi}, {int(c.time): c.close for c in funding},
            )
            log.info("backfill symbol=%s fetched=%d stored=%d", symbol, len(closed), written[symbol])
    finally:
        store.db.close()
        if own:
            await client.aclose()
    return written
