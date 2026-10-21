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
from datetime import UTC, datetime
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




async def backfill(
    db_path: str, symbols: list[str], days: int, interval: str = "15m",
    client: DeltaClient | None = None, restart: bool = False, chunk_days: int = 7,
) -> dict[str, dict[str, Any]]:
    """Fill the bars table with `days` of history per symbol, so base rates and backtests do not
    have to wait for the system to have been running that long.

    **Resumable.** It starts from the newest bar already stored rather than from the far end of the
    window, so a run that dies halfway through a year of minute bars — 525,600 of them a symbol —
    picks up where it stopped instead of re-fetching a week of history to find the first new row.
    `restart=True` ignores what is stored and walks the whole window, which is what you want after
    a gap you suspect was filled with bad data.

    **Idempotent.** The store ignores a bar it already has (INSERT OR IGNORE, invariant: a stored
    bar is never rewritten), so running this twice adds nothing the second time and running it
    alongside the live collector cannot produce a conflict.

    **Written in chunks**, oldest first, so an interrupted run leaves a shorter history rather than
    a history with a hole in the middle — and so progress is visible on a fetch that takes an hour.

    Open interest and funding ride the 15m series only. At the base interval they are skipped: both
    are levels that barely move inside a minute, and carrying them would triple the number of
    requests for a series nobody reads them from.

    Returns, per symbol: how many bars were written, what the series now covers, and where the
    holes are. The holes are reported rather than filled — a gap is market the exchange did not
    give us, and silently interpolating it is how a base rate comes to be counted over bars that
    never existed."""
    from tradebuddy import resample
    from tradebuddy.store import BASE_INTERVAL, Store

    own = client is None
    client = client or DeltaClient("https://api.india.delta.exchange")  # history is public
    store = Store(db_path)
    step = RESOLUTION_SECONDS[interval]
    levels = interval != BASE_INTERVAL  # fetch OI and funding alongside?
    end = int(time.time()) // step * step  # the last boundary: never store a bar still forming
    window_start = end - days * 86_400
    out: dict[str, dict[str, Any]] = {}
    try:
        for symbol in symbols:
            start = window_start
            if not restart:
                last = await asyncio.to_thread(store.last_bar_time, symbol, interval)
                if last:
                    start = max(start, last + step)
            written = 0
            if start < end:
                span = max(chunk_days * 86_400, step * 2)
                for lo in range(start, end, span):
                    hi = min(lo + span, end)
                    candles = await client.history_range(symbol, interval, lo, hi)
                    oi = await client.history_range(f"OI:{symbol}", interval, lo, hi) if levels else []
                    funding = await client.history_range(f"FUNDING:{symbol}", interval, lo, hi) if levels else []
                    closed = [c for c in candles if lo <= c.time and c.time + step <= end]
                    written += await asyncio.to_thread(
                        store.record_bars, symbol, interval, closed,
                        {int(c.time): c.close for c in oi}, {int(c.time): c.close for c in funding},
                    )
                    log.info(
                        "backfill symbol=%s interval=%s through=%s fetched=%d stored=%d",
                        symbol, interval, _stamp(hi), len(closed), written,
                    )
            rows = await asyncio.to_thread(store.bars, symbol, interval, window_start, days * 86_400 // step + 10)
            report = resample.validate(rows, interval) if rows else {"bars": 0, "ok": False, "gaps": [], "missing_bars": 0}
            out[symbol] = {"written": written, "resumed_from": start if start > window_start else None} | report
            log.info(
                "backfill_done symbol=%s interval=%s written=%d have=%d missing=%d",
                symbol, interval, written, report["bars"], report.get("missing_bars", 0),
            )
    finally:
        store.db.close()
        if own:
            await client.aclose()
    return out


def _stamp(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M")
