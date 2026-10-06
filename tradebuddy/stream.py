"""Delta WebSocket -> events.

One connection carries everything:
    v2/ticker          -> Tick
    candlestick_<res>  -> CandleClosed (when a bar rolls over)
    orders, positions  -> OrderUpdate, PositionUpdate (private, needs API key)

A bar is closed by whichever comes first: the stream showing the next bar, or
the clock passing the bar's end. On a quiet testnet the stream may send
nothing for a minute; the clock still closes the bar. Each bar is emitted once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import Counter
from typing import Any

from websockets.asyncio.client import connect

from tradebuddy.delta import RESOLUTION_SECONDS, Candle, sign
from tradebuddy.events import CandleClosed, EventBus, FeedStatus, MarketStats, OrderUpdate, PositionUpdate, Tick

log = logging.getLogger(__name__)


class PriceBook:
    """Latest price per symbol. A price older than `ttl` is treated as absent, never served stale."""

    def __init__(self, ttl: float = 30.0) -> None:
        self.ttl = ttl
        self._prices: dict[str, tuple[float, float]] = {}
        self._stats: dict[str, dict[str, Any]] = {}

    async def on_tick(self, event: Tick) -> None:
        self._prices[event.symbol] = (event.price, time.monotonic())

    async def on_stats(self, event: MarketStats) -> None:
        self._stats[event.symbol] = {k: v for k, v in event.to_dict().items() if k not in ("type", "symbol")}

    def price(self, symbol: str) -> float | None:
        entry = self._prices.get(symbol)
        if entry is None or time.monotonic() - entry[1] > self.ttl:
            return None
        return entry[0]

    def clear(self) -> None:
        self._prices.clear()
        self._stats.clear()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        return {
            s: {"price": p, "age_seconds": round(now - t, 1), "fresh": now - t <= self.ttl, "stats": self._stats.get(s)}
            for s, (p, t) in self._prices.items()
        }


class BarCloser:
    def __init__(self, bus: EventBus, pairs: set[tuple[str, str]], grace_seconds: float = 3.0) -> None:
        self.bus = bus
        self.pairs = pairs
        self.grace = grace_seconds
        self._forming: dict[tuple[str, str], Candle] = {}
        now = time.time()
        # Bars that closed before we started are history, not events.
        self._last_closed = {k: self._last_closed_bar(k[1], now) for k in pairs}
        self.closed_by: Counter[str] = Counter()

    @staticmethod
    def _last_closed_bar(resolution: str, now: float) -> int:
        step = RESOLUTION_SECONDS[resolution]
        return int(now // step) * step - step

    def observe(self, symbol: str, resolution: str, candle: Candle) -> None:
        """A candlestick update from the stream."""
        key = (symbol, resolution)
        if key not in self.pairs:
            return
        forming = self._forming.get(key)
        if forming is not None and candle.time > forming.time:
            self._close(key, forming.time, forming, "websocket")
        if forming is None or candle.time >= forming.time:
            self._forming[key] = candle

    def tick_clock(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        for key in self.pairs:
            bar = self._last_closed_bar(key[1], now - self.grace)
            forming = self._forming.get(key)
            self._close(key, bar, forming if forming and forming.time == bar else None, "clock")

    async def run(self) -> None:
        while True:
            self.tick_clock()
            await asyncio.sleep(1)

    def _close(self, key: tuple[str, str], bar_time: int, candle: Candle | None, source: str) -> None:
        if bar_time <= self._last_closed[key]:
            return
        self._last_closed[key] = bar_time
        self.closed_by[source] += 1
        self.bus.publish(CandleClosed(symbol=key[0], resolution=key[1], bar_time=bar_time, source=source, candle=candle))


def describe(exc: BaseException) -> str:
    """A short reason for the dashboard. An HTTP refusal of the WebSocket handshake carries its
    whole response (headers, HTML body); the status line is what matters."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is not None:
        hint = " (the exchange's CDN refuses this server's IP or region)" if status == 403 else ""
        return f"handshake refused: HTTP {status} {getattr(response, 'reason_phrase', '')}".rstrip() + hint
    return f"{type(exc).__name__}: {exc}"[:300]


def _f(value: Any) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def parse_stats(msg: dict[str, Any]) -> MarketStats:
    quotes = msg.get("quotes") or {}
    return MarketStats(
        symbol=msg["symbol"], last=_f(msg.get("close")), mark=_f(msg.get("mark_price")), index=_f(msg.get("spot_price")),
        open_24h=_f(msg.get("open")), high_24h=_f(msg.get("high")), low_24h=_f(msg.get("low")),
        change_24h_pct=_f(msg.get("ltp_change_24h")), mark_change_24h_pct=_f(msg.get("mark_change_24h")),
        volume_24h=_f(msg.get("volume")), turnover_24h_usd=_f(msg.get("turnover_usd")), oi_usd=_f(msg.get("oi_value_usd")),
        funding_rate_pct=_f(msg.get("funding_rate")), bid=_f(quotes.get("best_bid")), ask=_f(quotes.get("best_ask")),
    )


def parse_candle(msg: dict[str, Any]) -> Candle | None:
    try:
        start = int(msg["candle_start_time"])
        return Candle(
            time=start // 1_000_000 if start > 10**12 else start,  # Delta sends microseconds
            open=float(msg["open"]),
            high=float(msg["high"]),
            low=float(msg["low"]),
            close=float(msg["close"]),
            volume=float(msg.get("volume") or 0),
        )
    except (KeyError, TypeError, ValueError):
        return None


class DeltaStream:
    SILENCE_TIMEOUT = 60.0  # no message for this long means the socket is dead

    def __init__(
        self, url: str, bus: EventBus, closer: BarCloser, api_key: str = "", api_secret: str = ""
    ) -> None:
        self.url = url
        self.bus = bus
        self.closer = closer
        self.api_key = api_key
        self.api_secret = api_secret
        self.connected = False
        self.authenticated = False
        self.reconnects = 0
        self.connected_since = 0.0
        self.last_message_at = 0.0
        self.last_error = ""
        self.down_since: float | None = time.time()  # no data until the first connect
        self.messages: Counter[str] = Counter()
        self._ws: Any = None
        self._published: tuple[bool, bool] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "connected": self.connected,
            "authenticated": self.authenticated,
            "reconnects": self.reconnects,
            "connected_since": self.connected_since,
            "seconds_since_message": round(time.time() - self.last_message_at, 1) if self.last_message_at else None,
            "messages": dict(self.messages),
            "bars_closed_by": dict(self.closer.closed_by),
            "last_error": self.last_error,
            "down_since": self.down_since,
        }

    def subscribe_payload(self) -> dict[str, Any]:
        by_res: dict[str, list[str]] = {}
        for symbol, res in sorted(self.closer.pairs):
            by_res.setdefault(res, []).append(symbol)
        symbols = sorted({s for s, _ in self.closer.pairs})
        channels = [{"name": "v2/ticker", "symbols": symbols}]
        channels += [{"name": f"candlestick_{res}", "symbols": syms} for res, syms in by_res.items()]
        return {"type": "subscribe", "payload": {"channels": channels}}

    def auth_payload(self) -> dict[str, Any]:
        ts = str(int(time.time()))
        return {
            "type": "key-auth",
            "payload": {"api-key": self.api_key, "signature": sign(self.api_secret, "GET" + ts + "/live"), "timestamp": ts},
        }

    async def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with connect(self.url, ping_interval=20, ping_timeout=20, open_timeout=15, user_agent_header="tradebuddy") as ws:
                    self._ws = ws
                    self.down_since = None
                    self._set_status(connected=True)
                    self.connected_since = time.time()
                    backoff = 1.0
                    await ws.send(json.dumps(self.subscribe_payload()))
                    if self.api_key and self.api_secret:
                        await ws.send(json.dumps(self.auth_payload()))
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=self.SILENCE_TIMEOUT)
                        await self.handle(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # any failure means reconnect
                self.last_error = describe(exc)
                log.warning("stream_disconnected attempt=%d error=%s", self.reconnects + 1, self.last_error)
            self._ws = None
            if self.down_since is None:
                self.down_since = time.time()
            self._set_status(connected=False, authenticated=False)
            self.reconnects += 1
            await asyncio.sleep(backoff + random.uniform(0, backoff))  # noqa: S311 - jitter
            backoff = min(backoff * 2, 60.0)

    def _set_status(self, connected: bool | None = None, authenticated: bool | None = None) -> None:
        if connected is not None:
            self.connected = connected
        if authenticated is not None:
            self.authenticated = authenticated
        # Once per change of state: a socket refused for a day is one event, not one per retry.
        if (self.connected, self.authenticated) != self._published:
            self._published = (self.connected, self.authenticated)
            self.bus.publish(FeedStatus(connected=self.connected, authenticated=self.authenticated, error=self.last_error))

    async def handle(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        kind = str(msg.get("type", ""))
        self.messages[kind] += 1
        self.last_message_at = time.time()

        if kind == "v2/ticker":
            price = msg.get("mark_price") or msg.get("close")
            if msg.get("symbol") and price:
                self.bus.publish(Tick(symbol=msg["symbol"], price=float(price)))
                self.bus.publish(parse_stats(msg))
        elif kind.startswith("candlestick_"):
            candle = parse_candle(msg)
            if candle and msg.get("symbol"):
                self.closer.observe(msg["symbol"], kind.removeprefix("candlestick_"), candle)
        elif kind in ("key-auth", "auth"):
            await self._on_auth(msg)
        elif kind == "orders":
            for row in msg.get("result") or [msg]:
                if row.get("id"):
                    self.bus.publish(
                        OrderUpdate(
                            client_order_id=row.get("client_order_id"),
                            order_id=str(row["id"]),
                            symbol=str(row.get("product_symbol") or row.get("symbol") or ""),
                            state=str(row.get("state") or ""),
                        )
                    )
        elif kind == "positions":
            for row in msg.get("result") or [msg]:
                symbol = row.get("product_symbol") or row.get("symbol")
                if symbol:
                    self.bus.publish(
                        PositionUpdate(broker="delta", symbol=symbol, size=float(row.get("size") or 0), entry_price=float(row.get("entry_price") or 0))
                    )
        elif kind == "error":
            self.last_error = str(msg)[:300]
            log.warning("stream_error message=%s", msg)

    async def _on_auth(self, msg: dict[str, Any]) -> None:
        if msg.get("success"):
            self._set_status(authenticated=True)
            if self._ws is not None:
                channels = [{"name": "orders", "symbols": ["all"]}, {"name": "positions", "symbols": ["all"]}]
                await self._ws.send(json.dumps({"type": "subscribe", "payload": {"channels": channels}}))
        else:
            self.last_error = f"websocket auth failed: {msg}"[:300]
            log.error("stream_auth_failed message=%s", msg)
            self._set_status(authenticated=False)
