"""Delta Exchange REST client: market data, options chain, orders, positions."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from tradebuddy.errors import BrokerError, BrokerTimeout

log = logging.getLogger(__name__)

PROTECTIVE_TYPES = ("stop_loss_order", "take_profit_order")
RESOLUTION_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}


class DeltaError(BrokerError):
    """The exchange answered and said no."""


class DeltaTimeout(DeltaError, BrokerTimeout):
    """No clear answer: an order may or may not exist. Never resend — look it up by client_order_id."""


@dataclass(frozen=True)
class Candle:
    time: int  # bar open time, unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class OptionQuote:
    kind: str  # "call" | "put"
    strike: float
    expiry: date
    oi: float


def sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def round_to_tick(price: float, tick: float) -> str:
    step = Decimal(str(tick))
    return str((Decimal(str(price)) / step).quantize(Decimal(1)) * step)


class DeltaClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "", http: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.http = http or httpx.AsyncClient(timeout=10)
        self._products: dict[str, dict[str, Any]] = {}
        self.calls: dict[str, dict[str, Any]] = {}  # "GET /v2/tickers" -> counters, for the dashboard

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    async def request(
        self, method: str, path: str, params: dict | None = None, body: dict | None = None, auth: bool = False
    ) -> Any:
        query = "?" + urlencode(params) if params else ""
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"User-Agent": "tradebuddy", "Content-Type": "application/json"}
        if auth:
            if not self.has_credentials:
                raise DeltaError("DELTA_API_KEY / DELTA_API_SECRET are not set")
            ts = str(int(time.time()))
            headers |= {
                "api-key": self.api_key,
                "timestamp": ts,
                "signature": sign(self.api_secret, method + ts + path + query + payload),
            }

        stat = self.calls.setdefault(f"{method} {path}", {"count": 0, "errors": 0, "last_ms": 0.0, "last_at": 0.0, "last_error": ""})
        stat["count"] += 1
        started = time.perf_counter()
        try:
            return self._parse(method, path, await self._send(method, path, query, payload, headers))
        except DeltaError as exc:
            stat["errors"] += 1
            stat["last_error"] = str(exc)[:200]
            raise
        finally:
            stat["last_ms"] = round(1000 * (time.perf_counter() - started), 1)
            stat["last_at"] = time.time()

    # A dropped TLS handshake or reset connection (e.g. "[SSL: RECORD_LAYER_FAILURE]") is a network blip, not an
    # answer. Reads are retried a couple of times; anything that could place, change or cancel an order is never
    # resent: its outcome is looked up by client_order_id instead (timeout is not rejection).
    GET_RETRY_DELAYS = (0.5, 1.0)

    async def _send(self, method: str, path: str, query: str, payload: str, headers: dict[str, str]) -> httpx.Response:
        delays = self.GET_RETRY_DELAYS if method == "GET" else ()
        for attempt in range(len(delays) + 1):
            try:
                return await self.http.request(method, self.base_url + path + query, content=payload or None, headers=headers)
            except httpx.TransportError as exc:
                if attempt == len(delays):
                    raise DeltaTimeout(f"{method} {path}: {exc!r}") from exc
                stat = self.calls[f"{method} {path}"]
                stat["retries"] = stat.get("retries", 0) + 1
                log.info("delta_retry %s %s attempt=%d error=%r", method, path, attempt + 1, exc)
                await asyncio.sleep(delays[attempt])
        raise AssertionError("unreachable")

    @staticmethod
    def _parse(method: str, path: str, resp: httpx.Response) -> Any:
        if resp.status_code >= 500:
            raise DeltaTimeout(f"{method} {path}: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise DeltaError(f"{method} {path}: HTTP {resp.status_code} non-JSON body") from exc
        if resp.status_code >= 400 or not data.get("success", False):
            raise DeltaError(f"{method} {path}: HTTP {resp.status_code} {data.get('error')}")
        return data.get("result")

    # -- market data --------------------------------------------------------

    async def candles(self, symbol: str, resolution: str, count: int) -> list[Candle]:
        """The last `count` CLOSED candles, oldest first. The forming bar is dropped."""
        step = RESOLUTION_SECONDS[resolution]
        now = int(time.time())
        params = {"resolution": resolution, "symbol": symbol, "start": now - (count + 2) * step, "end": now}
        rows = await self.request("GET", "/v2/history/candles", params=params) or []
        bars = sorted(
            (
                Candle(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r.get("volume") or 0))
                for r in rows
            ),
            key=lambda c: c.time,
        )
        return [c for c in bars if c.time + step <= now][-count:]

    async def option_tickers(self, underlying: str) -> list[Any]:
        """Every live option on `underlying` with quotes, IVs, greeks, OI and volume (options.OptionQuote)."""
        from tradebuddy.options import parse_ticker

        params = {"contract_types": "call_options,put_options", "underlying_asset_symbols": underlying}
        now = time.time()
        rows = await self.request("GET", "/v2/tickers", params=params) or []
        return [q for q in (parse_ticker(r, now) for r in rows) if q is not None]

    async def history_range(self, symbol: str, resolution: str, start: int, end: int, page: int = 1900) -> list[Candle]:
        """Closed candles between two times, any length, fetched a page at a time (for training).
        Works for every history series: BTCUSD, OI:BTCUSD, FUNDING:BTCUSD, MARK:BTCUSD."""
        step = RESOLUTION_SECONDS[resolution]
        bars: dict[int, Candle] = {}
        hi = end
        while hi > start:
            lo = max(start, hi - page * step)
            rows = await self.request("GET", "/v2/history/candles", params={"resolution": resolution, "symbol": symbol, "start": lo, "end": hi}) or []
            for r in rows:
                c = Candle(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r.get("volume") or 0))
                bars[c.time] = c
            if not rows:
                break
            hi = lo
        now = int(time.time())
        return [bars[k] for k in sorted(bars) if k + step <= now]

    async def option_chain(self, underlying: str) -> list[OptionQuote]:
        params = {"contract_types": "call_options,put_options", "underlying_asset_symbols": underlying}
        quotes = []
        for t in await self.request("GET", "/v2/tickers", params=params) or []:
            # C-BTC-110000-290926 = type-underlying-strike-DDMMYY
            parts = str(t.get("symbol", "")).split("-")
            if len(parts) != 4 or parts[0] not in ("C", "P"):
                continue
            quotes.append(
                OptionQuote(
                    kind="call" if parts[0] == "C" else "put",
                    strike=float(parts[2]),
                    expiry=datetime.strptime(parts[3], "%d%m%y").date(),  # noqa: DTZ007 - a date, no time part
                    oi=float(t.get("oi") or 0),
                )
            )
        return quotes

    async def product(self, symbol: str) -> dict[str, Any]:
        if symbol not in self._products:
            self._products[symbol] = await self.request("GET", f"/v2/products/{symbol}")
        return self._products[symbol]

    # -- account ------------------------------------------------------------

    async def positions(self) -> list[dict[str, Any]]:
        """Open positions only (non-zero size)."""
        rows = await self.request("GET", "/v2/positions/margined", auth=True) or []
        return [p for p in rows if float(p.get("size") or 0)]

    async def position(self, symbol: str) -> dict[str, Any] | None:
        return next((p for p in await self.positions() if (p.get("product_symbol") or (p.get("product") or {}).get("symbol")) == symbol), None)

    async def balances(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/v2/wallet/balances", auth=True) or []

    async def open_orders(self, product_id: Any = None) -> list[dict[str, Any]]:
        """Resting orders, including the SL/TP legs of brackets."""
        params = {"states": "open,pending"} | ({"product_ids": str(product_id)} if product_id is not None else {})
        rows = await self.request("GET", "/v2/orders", params=params, auth=True) or []
        return [o for o in rows if product_id is None or o.get("product_id") in (None, product_id)]

    # -- orders -------------------------------------------------------------

    async def place_order(
        self,
        symbol: str,
        side: str,
        size: int,
        client_order_id: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        """Market order. SL/TP ride on the same request as a bracket, so the exchange protects the position."""
        product = await self.product(symbol)
        tick = float(product.get("tick_size") or 0.5)
        body: dict[str, Any] = {
            "product_id": product["id"],
            "size": int(size),
            "side": side,
            "order_type": "market_order",
            "client_order_id": client_order_id,
            "reduce_only": "true" if reduce_only else "false",
        }
        if stop_loss:
            body["bracket_stop_loss_price"] = round_to_tick(stop_loss, tick)
        if take_profit:
            body["bracket_take_profit_price"] = round_to_tick(take_profit, tick)
        if stop_loss or take_profit:
            body["bracket_stop_trigger_method"] = "mark_price"
        return await self.request("POST", "/v2/orders", body=body, auth=True)

    async def update_position_protection(self, symbol: str, stop_loss: float, take_profit: float | None = None) -> dict[str, Any]:
        """Replace the position's SL/TP without ever leaving it unprotected: the new legs are placed
        first, the old ones cancelled after. If a new leg fails, the legs already placed are withdrawn
        and the old protection is left as it was."""
        position = await self.position(symbol)
        if position is None:
            raise DeltaError(f"no open {symbol} position")
        size = float(position["size"])
        product = await self.product(symbol)
        tick = float(product.get("tick_size") or 0.5)
        old = [o for o in await self.open_orders(product["id"]) if o.get("stop_order_type") in PROTECTIVE_TYPES]

        placed: list[dict[str, Any]] = []
        legs = [("stop_loss_order", stop_loss), ("take_profit_order", take_profit)]
        try:
            for kind, level in legs:
                if level:
                    placed.append(await self.request("POST", "/v2/orders", body={
                        "product_id": product["id"],
                        "size": int(abs(size)),
                        "side": "sell" if size > 0 else "buy",
                        "order_type": "market_order",
                        "stop_order_type": kind,
                        "stop_price": round_to_tick(level, tick),
                        "stop_trigger_method": "mark_price",
                        "reduce_only": "true",
                        "client_order_id": f"tbp{uuid.uuid4().hex[:29]}",
                    }, auth=True))
        except DeltaError as exc:
            leftover = []
            for leg in placed:
                try:
                    await self.cancel_order(leg["id"], product["id"])
                except DeltaError:
                    leftover.append(str(leg["id"]))
            note = f"; new leg(s) {', '.join(leftover)} could not be withdrawn" if leftover else ""
            raise DeltaError(f"protection unchanged — {exc}{note}") from exc

        errors = []
        for o in old:
            try:
                await self.cancel_order(o["id"], product["id"])
            except DeltaError as exc:
                errors.append(f"old {o.get('stop_order_type')} {o['id']} still open: {exc}")
        return {"placed": [str(leg.get("id")) for leg in placed], "errors": errors}

    async def cancel_order(self, order_id: Any, product_id: Any) -> dict[str, Any]:
        return await self.request("DELETE", "/v2/orders", body={"id": order_id, "product_id": product_id}, auth=True)

    async def order_leverage(self, product_id: Any) -> float:
        """The leverage this account trades the product at (set per product on Delta)."""
        result = await self.request("GET", f"/v2/products/{product_id}/orders/leverage", auth=True) or {}
        leverage = float(result.get("leverage") or 0)
        if leverage <= 0:
            raise DeltaError(f"no leverage set for product {product_id}")
        return leverage

    async def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        """The order, or None when the exchange has never seen this id."""
        try:
            return await self.request("GET", f"/v2/orders/client_order_id/{client_order_id}", auth=True)
        except DeltaTimeout:
            raise
        except DeltaError as exc:
            if "HTTP 404" in str(exc) or "not_found" in str(exc).lower():
                return None
            raise

    async def close_all(self) -> dict[str, Any]:
        """Flatten every position with reduce-only market orders, then cancel every open order.
        Positions are read first: if that fails nothing has been touched and every stop is still in place."""
        positions = await self.positions()
        closed, errors = [], []
        for p in positions:
            size = int(float(p["size"]))
            symbol = p.get("product_symbol") or (p.get("product") or {}).get("symbol")
            try:
                await self.place_order(symbol, "sell" if size > 0 else "buy", abs(size), f"close-{uuid.uuid4().hex[:24]}", reduce_only=True)
                closed.append(symbol)
            except DeltaError as exc:
                errors.append(f"{symbol}: {exc}")
        try:
            await self.request("DELETE", "/v2/orders/all", body={"cancel_limit_orders": True, "cancel_stop_orders": True}, auth=True)
        except DeltaError as exc:
            errors.append(f"cancel open orders: {exc}")
        return {"closed": closed, "errors": errors}

    async def aclose(self) -> None:
        await self.http.aclose()
