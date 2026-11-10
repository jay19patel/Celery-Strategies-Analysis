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
import math
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import col, delete, select, update
from websockets.asyncio.client import connect

from app.core.db import session
from app.core.models import Bar
from app.market.delta import Candle, DeltaClient
from app.market.options import PERPETUALS, UNDERLYING_OF, OptionQuote, parse_symbol, parse_ticker
from app.market.tape import Tape

log = logging.getLogger(__name__)

PRICE_TTL = 30.0
QUOTE_TTL = 120.0
OPTIONS_REFRESH = 30.0
# The two series stored: the 15m one the regime, outlook and pulse count over (a year, so a
# "bars like this one" base rate has a sample), and the 1m base every shorter frame and the volume
# profile are folded from. Everything else is folded, never fetched separately (`analysis.resample`).
SERIES = {"15m": 365, "1m": 30}  # interval -> days kept
SERIES_SECONDS = {"15m": 900, "1m": 60}
MAX_JUMP = 0.25  # a bar whose body is 25% from the last good close is a bad print, not a market
MAX_WICK = {"1m": 0.05, "15m": 0.10}  # a wick this far beyond its own body is trimmed to it
# A traded price this far outside the same bar's mark-price range is a print on an empty book: real
# BTC and ETH bars stay within 1% of their mark range (0.95% at worst over 120 days), the ETH bad
# prints Delta later revises out of its history sit 2-7% beyond it.
MARK_BAND = 0.015
PERP_PULLED = 0.02  # a perpetual bid or ask this far from the mark is no price (`parse_perp`)
BOOK_EVERY = 5.0  # seconds between reads of one symbol's order book


def _f(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def clean_candles(candles: list[Candle], reference: float | None = None, max_wick: float = 0.10) -> list[Candle]:
    """Candles that could have traded, in time order: what an empty book printed is not a market.

    Delta's ETH perpetual has printed lows of 0.05 and closes of 1.0 in a 2,000 market, sometimes for
    hours. One such bar is a 99.99% move to every indicator, profile and backtest that reads it. Each
    bar is judged against the last good close (`reference`, else the median of the first closes):
    a body more than `MAX_JUMP` away is dropped (a hole, which every reader already tolerates). A
    wick reaching more than `max_wick` beyond its own body is trimmed back to it, keeping the bar's
    open, close and volume: BTC's real 15m crash wicks reach about 4%, the bad prints 14-28%.
    """
    ordered = sorted(candles, key=lambda c: c.time)
    if not ordered:
        return []
    good = reference or statistics.median(c.close for c in ordered[:50])
    out = []
    for c in ordered:
        if not all(math.isfinite(x) and x > 0 for x in (c.open, c.high, c.low, c.close)) or good <= 0:
            continue
        if abs(c.open / good - 1) > MAX_JUMP or abs(c.close / good - 1) > MAX_JUMP:
            continue
        top, bottom = max(c.open, c.close), min(c.open, c.close)
        high = c.high if c.high <= top * (1 + max_wick) else top
        low = c.low if c.low >= bottom * (1 - max_wick) else bottom
        out.append(Candle(c.time, c.open, max(high, top), min(low, bottom), c.close, c.volume))
        good = c.close
    return out


def fit_to_mark(candles: list[Candle], marks: list[Candle], band: float = MARK_BAND) -> list[Candle]:
    """Each traded bar held within `band` of the same bar's mark-price range.

    The mark is Delta's index plus basis, so no single print moves it. Delta's ETH perpetual prints
    2,400 in a 2,245 market for a trade or two and revises the bar later; stored before that, one
    such high reads as 250% realised volatility. A bar with no mark bar is left as it is."""
    by_time = {m.time: m for m in marks}
    out = []
    for c in candles:
        m = by_time.get(c.time)
        if m is None:
            out.append(c)
            continue
        lo, hi = m.low * (1 - band), m.high * (1 + band)
        o, h, low, close = (min(max(v, lo), hi) for v in (c.open, c.high, c.low, c.close))
        out.append(Candle(c.time, o, h, low, close, c.volume))
    return out


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
    # A perpetual's real touch sits hundredths of a percent from the mark; one `PERP_PULLED` away is
    # an emptied book (ETH's has traded at 0.05), and a close priced off it would sell at that.
    near = lambda v: v if v is not None and abs(v / mark - 1) <= PERP_PULLED else None  # noqa: E731
    return PerpQuote(
        symbol=msg["symbol"], mark=mark, index=_f(msg.get("spot_price")), at=now,
        stats={
            "open_24h": _f(msg.get("open")), "high_24h": _f(msg.get("high")), "low_24h": _f(msg.get("low")),
            "change_24h_pct": _f(msg.get("mark_change_24h")) or _f(msg.get("ltp_change_24h")),
            "volume_24h": _f(msg.get("volume")), "turnover_24h_usd": _f(msg.get("turnover_usd")),
            "oi_usd": _f(msg.get("oi_value_usd")), "funding_rate_pct": _f(msg.get("funding_rate")),
            "bid": near(_f(quotes.get("best_bid"))), "ask": near(_f(quotes.get("best_ask"))),
        },
    )


class MarketFeed:
    SILENCE_TIMEOUT = 60.0

    def __init__(self, rest_url: str, ws_url: str, underlyings: list[str], record: bool = True) -> None:
        self.rest = DeltaClient(rest_url)
        # Store bars, tape and chains? Only from the live exchange: the history is keyed by symbol,
        # and testnet prints stored under BTCUSD would be read as the real market for a year.
        self.record = record
        self.ws_url = ws_url
        self.underlyings = list(underlyings)
        self.perps: dict[str, PerpQuote] = {}
        self.book: dict[str, OptionQuote] = {}
        self._task: asyncio.Task[None] | None = None
        self.ws = {"connected": False, "since": None, "last_message": None, "reconnects": 0, "messages": 0, "error": ""}
        self.rest_status = {"last_refresh": None, "error": ""}
        self._latest_bar_times: dict[tuple[str, str], int] = {}
        self.tape = Tape()
        self._book_at: dict[str, float] = {}  # symbol -> when its book was last read

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

    async def reconfigure(self, rest_url: str, ws_url: str, underlyings: list[str], record: bool = True) -> None:
        """Another exchange environment or set of underlyings: every price and print from the old one is dropped."""
        running = self._task is not None
        await self.stop()
        self.rest = DeltaClient(rest_url)
        self.ws_url, self.underlyings, self.record = ws_url, list(underlyings), record
        self.perps.clear()
        self.book.clear()
        self.tape = Tape()
        self._latest_bar_times.clear()
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
        if not self.record:
            return "not stored: the feed is on the demo exchange"
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
        if candles:
            candles = fit_to_mark(candles, await self.rest.candles(f"MARK:{symbol}", interval, start, end))
        rows = [
            {"symbol": symbol, "interval": interval, "time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
            for c in clean_candles([c for c in candles if start <= c.time <= end], self._close_before(symbol, interval, start), MAX_WICK[interval])
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
    def _close_before(symbol: str, interval: str, start: int) -> float | None:
        """The last stored close before `start`: what the next bars are judged against."""
        with session() as s:
            row = s.exec(
                select(Bar.close).where(Bar.symbol == symbol, Bar.interval == interval, col(Bar.time) < start)
                .order_by(col(Bar.time).desc()).limit(1)
            ).first()
        return float(row) if row else None

    @staticmethod
    def clean_stored() -> dict[str, int]:
        """`clean_candles` over everything already stored, once at start-up: bars kept from before the
        rule existed are dropped or trimmed like new ones. One series at a time, as plain tuples."""
        dropped = trimmed = 0
        with session() as s:
            series = s.exec(select(Bar.symbol, Bar.interval).distinct()).all()
        for symbol, interval in series:
            with session() as s:
                rows = s.exec(
                    select(Bar.time, Bar.open, Bar.high, Bar.low, Bar.close, Bar.volume)
                    .where(Bar.symbol == symbol, Bar.interval == interval).order_by(col(Bar.time))
                ).all()
                kept = {c.time: c for c in clean_candles([Candle(*r) for r in rows], max_wick=MAX_WICK.get(interval, 0.10))}
                gone = [r[0] for r in rows if r[0] not in kept]
                fixed = [kept[r[0]] for r in rows if r[0] in kept and (kept[r[0]].high, kept[r[0]].low) != (r[2], r[3])]
                for i in range(0, len(gone), 500):
                    s.exec(delete(Bar).where(Bar.symbol == symbol, Bar.interval == interval, col(Bar.time).in_(gone[i : i + 500])))
                for c in fixed:
                    s.exec(update(Bar).where(Bar.symbol == symbol, Bar.interval == interval, Bar.time == c.time).values(high=c.high, low=c.low))
                s.commit()
            dropped, trimmed = dropped + len(gone), trimmed + len(fixed)
        return {"dropped": dropped, "trimmed": trimmed}

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
        perps = [PERPETUALS[u] for u in self.underlyings]
        symbols = [*perps, "call_options", "put_options"]
        return {"type": "subscribe", "payload": {"channels": [
            {"name": "v2/ticker", "symbols": symbols},
            {"name": "all_trades", "symbols": symbols},  # every print, with the side that crossed the spread
            {"name": "l2_orderbook", "symbols": perps},
        ]}}

    def wanted(self, raw: str) -> bool:
        """Whether a raw message is worth parsing. The book arrives as ~60 KB of JSON a few times a
        second per symbol; one look every `BOOK_EVERY` seconds is all the minute's mean needs, and
        skipping the rest before `json.loads` keeps a small server's CPU for the trading."""
        if '"type":"l2_orderbook"' not in raw:
            return True
        at = raw.find('"symbol":"')
        symbol = raw[at + 10 : raw.find('"', at + 10)] if at >= 0 else ""
        now = time.time()
        if now - self._book_at.get(symbol, 0.0) < BOOK_EVERY:
            return False
        self._book_at[symbol] = now
        return True

    def handle(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "all_trades":
            self._trade(msg)
            return
        if kind == "l2_orderbook":
            levels = lambda side: [(float(x["limit_price"]), float(x["size"])) for x in msg.get(side) or []]  # noqa: E731
            self.tape.book(str(msg.get("symbol", "")), levels("buy"), levels("sell"))
            return
        if kind != "v2/ticker":
            return  # including `all_trades_snapshot`: replayed on every reconnect, it would count twice
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

    def _trade(self, msg: dict[str, Any]) -> None:
        symbol = str(msg.get("symbol", ""))
        price, size = _f(msg.get("price")) or 0.0, _f(msg.get("size")) or 0.0
        taker_buy = msg.get("buyer_role") == "taker"
        ts = (_f(msg.get("timestamp")) or time.time() * 1e6) / 1e6
        if symbol[:2] in ("C-", "P-"):
            parsed = parse_symbol(symbol)
            if parsed and parsed[1] in self.underlyings:
                quote = self.book.get(symbol)
                cv = quote.contract_value if quote else (0.01 if parsed[1] == "ETH" else 0.001)
                self.tape.option_trade(parsed[1], parsed[0], price, size * cv, taker_buy)
        elif symbol in UNDERLYING_OF:
            perp = self.perp(UNDERLYING_OF[symbol])
            self.tape.trade(symbol, price, size, taker_buy, ts, perp.mark if perp else None)

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
                        if self.wanted(raw):
                            try:
                                self.handle(json.loads(raw))
                            except (ValueError, KeyError, TypeError) as exc:  # one bad message is not a dead socket
                                self.ws["bad_messages"] = self.ws.get("bad_messages", 0) + 1
                                log.debug("websocket_bad_message error=%r", exc)
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
