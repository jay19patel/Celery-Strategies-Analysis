"""Live market data: perpetual prices, the option book, and the stored 15m bars.

Two sources, one book. The Delta WebSocket (the only code here that talks to it) pushes every
perpetual and option ticker as it changes. A REST refresh replaces each underlying's whole chain
every `OPTIONS_REFRESH` seconds and re-reads the perpetuals when the socket has gone quiet, so the
book is complete (the socket only sends contracts that changed) and keeps working when the socket is
refused — the testnet socket rejects connections from outside India.

Absence over staleness: a perpetual price older than `PRICE_TTL` or an option quote older than
`QUOTE_TTL` is reported as missing, never served.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import col, delete, select
from websockets.asyncio.client import connect

from app.core.db import session
from app.core.models import Bar
from app.market.delta import DeltaClient
from app.market.options import PERPETUALS, OptionQuote, parse_ticker

log = logging.getLogger(__name__)

PRICE_TTL = 30.0
QUOTE_TTL = 120.0
OPTIONS_REFRESH = 30.0
# The two series stored: the 15m one the regime, outlook and pulse count over (a year, so a
# "bars like this one" base rate has a sample), and the 1m base every shorter frame and the volume
# profile are folded from. Everything else is folded, never fetched separately (`analysis.resample`).
SERIES = {"15m": 365, "1m": 30}  # interval -> days kept
SERIES_SECONDS = {"15m": 900, "1m": 60}


def _f(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class PerpQuote:
    symbol: str
    mark: float
    index: float | None
    at: float
    stats: dict[str, Any] = field(default_factory=dict)


def parse_perp(msg: dict[str, Any], now: float) -> PerpQuote | None:
    mark = _f(msg.get("mark_price")) or _f(msg.get("close"))
    if not msg.get("symbol") or not mark:
        return None
    quotes = msg.get("quotes") or {}
    return PerpQuote(
        symbol=msg["symbol"], mark=mark, index=_f(msg.get("spot_price")), at=now,
        stats={
            "open_24h": _f(msg.get("open")), "high_24h": _f(msg.get("high")), "low_24h": _f(msg.get("low")),
            "change_24h_pct": _f(msg.get("mark_change_24h")) or _f(msg.get("ltp_change_24h")),
            "volume_24h": _f(msg.get("volume")), "turnover_24h_usd": _f(msg.get("turnover_usd")),
            "oi_usd": _f(msg.get("oi_value_usd")), "funding_rate_pct": _f(msg.get("funding_rate")),
            "bid": _f(quotes.get("best_bid")), "ask": _f(quotes.get("best_ask")),
        },
    )


class MarketFeed:
    SILENCE_TIMEOUT = 60.0

    def __init__(self, rest_url: str, ws_url: str, underlyings: list[str]) -> None:
        self.rest = DeltaClient(rest_url)
        self.ws_url = ws_url
        self.underlyings = list(underlyings)
        self.perps: dict[str, PerpQuote] = {}
        self.book: dict[str, OptionQuote] = {}
        self._task: asyncio.Task[None] | None = None
        self.ws = {"connected": False, "since": None, "last_message": None, "reconnects": 0, "messages": 0, "error": ""}
        self.rest_status = {"last_refresh": None, "error": ""}
        self._latest_bar_times: dict[tuple[str, str], int] = {}

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_ws(), name="delta-websocket")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.rest.aclose()

    async def reconfigure(self, rest_url: str, ws_url: str, underlyings: list[str]) -> None:
        """Another exchange environment or set of underlyings: every price from the old one is dropped."""
        running = self._task is not None
        await self.stop()
        self.rest = DeltaClient(rest_url)
        self.ws_url, self.underlyings = ws_url, list(underlyings)
        self.perps.clear()
        self.book.clear()
        if running:
            self.start()

    # -- reads -------------------------------------------------------------------------------

    def perp(self, underlying: str) -> PerpQuote | None:
        q = self.perps.get(PERPETUALS.get(underlying, ""))
        return q if q and time.time() - q.at <= PRICE_TTL else None

    def spot(self, underlying: str) -> float | None:
        """The index the options settle on, from the perpetual's ticker, else the option chain's own."""
        q = self.perp(underlying)
        if q:
            return q.index or q.mark
        fresh = [o for o in self.quotes(underlying) if o.spot]
        return max(fresh, key=lambda o: o.at).spot if fresh else None

    def quotes(self, underlying: str, expiry: float | None = None) -> list[OptionQuote]:
        cutoff = time.time() - QUOTE_TTL
        return [
            q for q in self.book.values()
            if q.underlying == underlying and q.at >= cutoff and (expiry is None or q.expiry == expiry)
        ]

    def quote(self, symbol: str) -> OptionQuote | None:
        q = self.book.get(symbol)
        return q if q and time.time() - q.at <= QUOTE_TTL else None

    def status(self) -> dict[str, Any]:
        now = time.time()
        return {
            "ws_url": self.ws_url,
            "rest_url": self.rest.base_url,
            "websocket": self.ws,
            "rest": self.rest_status,
            "prices": {
                s: {"price": q.mark, "index": q.index, "age_seconds": round(now - q.at, 1), "fresh": now - q.at <= PRICE_TTL}
                for s, q in self.perps.items()
            },
            "options": {u: len(self.quotes(u)) for u in self.underlyings},
            "rest_calls": self.rest.calls,
        }

    # -- REST refresh (a scheduler job) ----------------------------------------------------

    async def refresh(self) -> str:
        now = time.time()
        errors: list[str] = []
        for underlying in self.underlyings:
            perp = PERPETUALS[underlying]
            q = self.perps.get(perp)
            if q is None or now - q.at > PRICE_TTL / 2:
                try:
                    parsed = parse_perp(await self.rest.ticker(perp), time.time())
                    if parsed:
                        self.perps[perp] = parsed
                except Exception as exc:
                    errors.append(f"{perp}: {exc}")
            try:
                rows = await self.rest.option_tickers(underlying)
                fresh = {q.symbol: q for q in (parse_ticker(r, time.time()) for r in rows) if q}
                for symbol in [s for s, q in self.book.items() if q.underlying == underlying and s not in fresh]:
                    del self.book[symbol]  # expired or delisted
                for symbol, quote in fresh.items():
                    current = self.book.get(symbol)
                    if current is None or current.at < quote.at:
                        self.book[symbol] = quote
            except Exception as exc:
                errors.append(f"{underlying} options: {exc}")
        self.rest_status = {"last_refresh": time.time(), "error": "; ".join(errors)[:400]}
        if errors:
            raise RuntimeError(self.rest_status["error"])
        return f"{sum(len(self.quotes(u)) for u in self.underlyings)} option quotes"

    # -- stored bars (a scheduler job) -----------------------------------------------------

    async def collect_bars(self, interval: str = "15m") -> str:
        """Every closed bar the store is missing at either end of the series' history: newer than
        the newest stored, and older than the oldest back to the history's start. Resumable and
        idempotent — a stored bar is never rewritten, because a silently revised past is worse for
        a base rate than a missing one. Holes in the middle are left as holes."""
        step, days = SERIES_SECONDS[interval], SERIES[interval]
        now = int(time.time())
        horizon = now - days * 86_400
        added = 0
        for underlying in self.underlyings:
            symbol = PERPETUALS[underlying]
            with session() as s:
                first, last = s.exec(
                    select(func.min(Bar.time), func.max(Bar.time)).where(Bar.symbol == symbol, Bar.interval == interval)
                ).one()
            spans = [(horizon, now)] if last is None else [(last + step, now), (horizon, first - step)]
            for start, end in spans:
                if end - start >= step:
                    added += await self._store(symbol, interval, start, end)
        return f"{added} {interval} bars stored"

    async def _store(self, symbol: str, interval: str, start: int, end: int) -> int:
        candles = await self.rest.candles(symbol, interval, start, end)
        rows = [
            {"symbol": symbol, "interval": interval, "time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
            for c in candles if start <= c.time <= end
        ]
        if rows:
            max_t = max(r["time"] for r in rows)
            curr = self._latest_bar_times.get((symbol, interval), 0)
            if max_t > curr:
                self._latest_bar_times[(symbol, interval)] = max_t
        with session() as s:
            for i in range(0, len(rows), 1000):  # SQLite caps the variables in one statement
                s.exec(sqlite_insert(Bar).values(rows[i : i + 1000]).on_conflict_do_nothing())
            s.commit()
        return len(rows)

    def latest_bar_time(self, symbol: str, interval: str = "15m") -> int:
        """Return the timestamp of the latest closed bar for this symbol and interval."""
        cached = self._latest_bar_times.get((symbol, interval))
        if cached is not None:
            return cached
        with session() as s:
            val = s.exec(select(func.max(Bar.time)).where(Bar.symbol == symbol, Bar.interval == interval)).one()
            latest = int(val) if val else 0
            self._latest_bar_times[(symbol, interval)] = latest
            return latest

    @staticmethod
    def prune_bars() -> int:
        """Each series keeps its own history: a year of 15m bars, a month of 1m bars."""
        removed = 0
        with session() as s:
            for interval, days in SERIES.items():
                removed += s.exec(delete(Bar).where(Bar.interval == interval, col(Bar.time) < time.time() - days * 86_400)).rowcount or 0
            s.commit()
        return removed

    @staticmethod
    def bars(symbol: str, limit: int = 3000, interval: str = "15m", since: float = 0.0) -> list[dict[str, Any]]:
        """Stored closed bars, oldest first."""
        with session() as s:
            stmt = (
                select(Bar.time, Bar.open, Bar.high, Bar.low, Bar.close, Bar.volume)
                .where(Bar.symbol == symbol, Bar.interval == interval, col(Bar.time) >= since)
                .order_by(col(Bar.time).desc())
                .limit(limit)
            )
            rows = s.exec(stmt).all()
        return [{"time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in reversed(rows)]

    # -- WebSocket ---------------------------------------------------------------------------

    def subscribe_payload(self) -> dict[str, Any]:
        symbols = [PERPETUALS[u] for u in self.underlyings] + ["call_options", "put_options"]
        return {"type": "subscribe", "payload": {"channels": [{"name": "v2/ticker", "symbols": symbols}]}}

    def handle(self, msg: Any) -> None:
        if not isinstance(msg, dict) or msg.get("type") != "v2/ticker":
            return
        now = time.time()
        self.ws["last_message"] = now
        self.ws["messages"] += 1
        symbol = str(msg.get("symbol", ""))
        if symbol[:2] in ("C-", "P-"):
            quote = parse_ticker(msg, now)
            if quote and quote.underlying in self.underlyings:
                self.book[symbol] = quote
        elif (perp := parse_perp(msg, now)) is not None:
            self.perps[symbol] = perp

    async def _run_ws(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with connect(self.ws_url, ping_interval=20, ping_timeout=20, open_timeout=15, user_agent_header="tradebuddy") as ws:
                    self.ws.update(connected=True, since=time.time(), error="")
                    backoff = 1.0
                    await ws.send(json.dumps(self.subscribe_payload()))
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=self.SILENCE_TIMEOUT)
                        self.handle(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                self.ws["error"] = f"handshake refused: HTTP {status}" if status else f"{type(exc).__name__}: {exc}"[:300]
                log.warning("websocket_down error=%s", self.ws["error"])
            self.ws.update(connected=False)
            self.ws["reconnects"] += 1
            await asyncio.sleep(backoff + random.uniform(0, backoff))  # noqa: S311 - jitter
            backoff = min(backoff * 2, 60.0)
