"""Delta Exchange India REST client — the only code that talks to Delta REST.

Reads are retried on a network blip. Anything that places or cancels an order is never resent:
its outcome is looked up by client_order_id instead (timeout is not rejection).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from app.errors import BrokerError, BrokerTimeout

log = logging.getLogger(__name__)

RESOLUTION_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14_400, "1d": 86_400}


class DeltaError(BrokerError):
    """The exchange answered and said no."""


class DeltaTimeout(DeltaError, BrokerTimeout):
    """No clear answer: an order may or may not exist."""


@dataclass(frozen=True)
class Candle:
    time: int  # bar open, unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def _endpoint(path: str) -> str:
    """One counter per endpoint, not per contract: /v2/products/C-BTC-... -> /v2/products/{symbol}."""
    for prefix in ("/v2/products/", "/v2/tickers/", "/v2/orders/client_order_id/"):
        if path.startswith(prefix) and len(path) > len(prefix):
            return prefix + "{id}"
    return path


def sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def round_to_tick(price: float, tick: float) -> str:
    step = Decimal(str(tick))
    return str((Decimal(str(price)) / step).quantize(Decimal(1)) * step)


class DeltaClient:
    GET_RETRY_DELAYS = (0.5, 1.0)

    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "", http: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.http = http or httpx.AsyncClient(timeout=10)
        self._products: dict[str, dict[str, Any]] = {}
        self.calls: dict[str, dict[str, Any]] = {}  # "GET /v2/tickers" -> counters, for the System page

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    async def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None, auth: bool = False) -> Any:
        query = "?" + urlencode(params) if params else ""
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"User-Agent": "tradebuddy", "Content-Type": "application/json"}
        if auth:
            if not self.has_credentials:
                raise DeltaError("the Delta API key and secret are not set")
            ts = str(int(time.time()))
            headers |= {"api-key": self.api_key, "timestamp": ts, "signature": sign(self.api_secret, method + ts + path + query + payload)}

        stat = self.calls.setdefault(f"{method} {_endpoint(path)}", {"count": 0, "errors": 0, "last_ms": 0.0, "last_error": ""})
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

    async def _send(self, method: str, path: str, query: str, payload: str, headers: dict[str, str]) -> httpx.Response:
        delays = self.GET_RETRY_DELAYS if method == "GET" else ()
        for attempt in range(len(delays) + 1):
            try:
                return await self.http.request(method, self.base_url + path + query, content=payload or None, headers=headers)
            except httpx.TransportError as exc:
                if attempt == len(delays):
                    raise DeltaTimeout(f"{method} {path}: {exc!r}") from exc
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

    # -- market data ---------------------------------------------------------------------

    async def candles(self, symbol: str, resolution: str, start: int, end: int) -> list[Candle]:
        """Closed candles between two times, oldest first, fetched a page at a time."""
        step = RESOLUTION_SECONDS[resolution]
        bars: dict[int, Candle] = {}
        hi = end
        while hi > start:
            lo = max(start, hi - 1900 * step)
            rows = await self.request("GET", "/v2/history/candles", params={"resolution": resolution, "symbol": symbol, "start": lo, "end": hi}) or []
            for r in rows:
                c = Candle(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r.get("volume") or 0))
                bars[c.time] = c
            if not rows:
                break
            hi = lo
        now = int(time.time())
        return [bars[k] for k in sorted(bars) if k + step <= now]

    async def ticker(self, symbol: str) -> dict[str, Any]:
        return await self.request("GET", f"/v2/tickers/{symbol}")

    async def option_tickers(self, underlying: str) -> list[dict[str, Any]]:
        params = {"contract_types": "call_options,put_options", "underlying_asset_symbols": underlying}
        return await self.request("GET", "/v2/tickers", params=params) or []

    async def product(self, symbol: str) -> dict[str, Any]:
        if symbol not in self._products:
            self._products[symbol] = await self.request("GET", f"/v2/products/{symbol}")
        return self._products[symbol]

    # -- account -------------------------------------------------------------------------

    async def balances(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/v2/wallet/balances", auth=True) or []

    async def positions(self) -> list[dict[str, Any]]:
        rows = await self.request("GET", "/v2/positions/margined", auth=True) or []
        return [p for p in rows if float(p.get("size") or 0)]

    # -- orders --------------------------------------------------------------------------

    async def place_order(
        self, symbol: str, side: str, size: int, client_order_id: str, limit_price: float, reduce_only: bool = False,
    ) -> dict[str, Any]:
        """An immediate-or-cancel limit order: it fills now at `limit_price` or better, or not at all.
        Option books are thin, so a market order can fill far from the mark; the limit caps that."""
        product = await self.product(symbol)
        tick = float(product.get("tick_size") or 0.1)
        body = {
            "product_id": product["id"],
            "size": int(size),
            "side": side,
            "order_type": "limit_order",
            "limit_price": round_to_tick(limit_price, tick),
            "time_in_force": "ioc",
            "client_order_id": client_order_id,
            "reduce_only": "true" if reduce_only else "false",
        }
        return await self.request("POST", "/v2/orders", body=body, auth=True)

    async def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        try:
            return await self.request("GET", f"/v2/orders/client_order_id/{client_order_id}", auth=True)
        except DeltaTimeout:
            raise
        except DeltaError as exc:
            if "HTTP 404" in str(exc) or "not_found" in str(exc).lower():
                return None
            raise

    async def aclose(self) -> None:
        await self.http.aclose()
