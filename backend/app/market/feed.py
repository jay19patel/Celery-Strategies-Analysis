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
from sqlmodel import select
from websockets.asyncio.client import connect

from app.core.db import session
from app.core.models import Bar
from app.market.delta import DeltaClient
from app.market.options import PERPETUALS, OptionQuote, parse_ticker

log = logging.getLogger(__name__)

PRICE_TTL = 30.0
QUOTE_TTL = 120.0
OPTIONS_REFRESH = 30.0
BAR_INTERVAL = "15m"
BAR_SECONDS = 900
BAR_HISTORY_DAYS = 30


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

    async def collect_bars(self) -> str:
        now = int(time.time())
        added = 0
        for underlying in self.underlyings:
            symbol = PERPETUALS[underlying]
            with session() as s:
                last = s.exec(select(func.max(Bar.time)).where(Bar.symbol == symbol, Bar.interval == BAR_INTERVAL)).one()
            start = (last + BAR_SECONDS) if last else now - BAR_HISTORY_DAYS * 86_400
            if now - start < BAR_SECONDS:
                continue
            candles = await self.rest.candles(symbol, BAR_INTERVAL, start, now)
            with session() as s:
                for c in candles:
                    if c.time >= start:  # a stored bar is never rewritten
                        s.merge(Bar(symbol=symbol, interval=BAR_INTERVAL, time=c.time, open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume))
                        added += 1
                s.commit()
        return f"{added} bars stored"

    @staticmethod
    def bars(symbol: str, limit: int = 3000) -> list[dict[str, Any]]:
        with session() as s:
            rows = s.exec(select(Bar).where(Bar.symbol == symbol, Bar.interval == BAR_INTERVAL).order_by(Bar.time.desc()).limit(limit)).all()
        return [r.model_dump(exclude={"symbol", "interval"}) for r in reversed(rows)]

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
