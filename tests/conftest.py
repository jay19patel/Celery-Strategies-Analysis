from __future__ import annotations

from typing import Any

import pytest

from tradebuddy.config import load_config
from tradebuddy.delta import Candle, DeltaError, OptionQuote


class FakeExchange:
    """Stands in for Delta. Every DeltaClient the System builds talks to this one exchange."""

    def __init__(self) -> None:
        self.open_positions: list[dict[str, Any]] = []
        self.positions_error: Exception | None = None
        self.place_error: Exception | None = None
        self.placed: list[dict[str, Any]] = []
        self.lookup: dict[str, Any] = {}
        self.history: list[Candle] = []
        self.chain: list[OptionQuote] = []
        self.spec: dict[str, Any] = {"id": 27, "contract_value": 0.001, "tick_size": 0.5}
        self.clients: list[FakeClient] = []

    def client(self, base_url: str, api_key: str = "", api_secret: str = "") -> FakeClient:
        c = FakeClient(self, base_url, api_key, api_secret)
        self.clients.append(c)
        return c


class FakeClient:
    def __init__(self, exchange: FakeExchange, base_url: str, api_key: str, api_secret: str) -> None:
        self.x = exchange
        self.base_url = base_url
        self.api_key = api_key
        self.api_secret = api_secret
        self.calls: dict[str, Any] = {}
        self.closed = False

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _auth(self):
        if not self.has_credentials:
            raise DeltaError("DELTA_API_KEY / DELTA_API_SECRET are not set")

    async def positions(self):
        self._auth()
        if self.x.positions_error:
            raise self.x.positions_error
        return self.x.open_positions

    async def place_order(self, symbol, side, size, client_order_id, stop_loss=None, take_profit=None, reduce_only=False):
        self.x.placed.append({"symbol": symbol, "side": side, "size": size, "client_order_id": client_order_id, "stop_loss": stop_loss, "take_profit": take_profit, "reduce_only": reduce_only})
        if self.x.place_error:
            raise self.x.place_error
        return {"id": 42, "state": "closed", "client_order_id": client_order_id}

    async def order_by_client_id(self, cid):
        value = self.x.lookup.get(cid)
        if isinstance(value, Exception):
            raise value
        return value

    async def candles(self, symbol, resolution, count):
        return self.x.history[-count:]

    async def option_chain(self, underlying):
        return self.x.chain

    async def product(self, symbol):
        return self.x.spec

    async def balances(self):
        self._auth()
        return [{"asset_symbol": "USD", "balance": "100", "available_balance": "90", "position_margin": "10", "order_margin": "0"}]

    async def open_orders(self):
        return []

    async def close_all(self):
        return {"closed": [], "errors": []}

    async def aclose(self):
        self.closed = True


class IdleStream:
    instances: list[IdleStream] = []

    def __init__(self, url="", bus=None, closer=None, api_key="", api_secret="") -> None:
        self.url, self.api_key = url, api_key
        IdleStream.instances.append(self)

    async def run(self) -> None:
        pass

    def status(self) -> dict[str, Any]:
        return {"connected": False, "url": self.url}


@pytest.fixture
def cfg(tmp_path):
    return load_config({"DB_PATH": str(tmp_path / "t.db")})


@pytest.fixture
def exchange():
    return FakeExchange()


def bars(closes: list[float], step: int = 60, start: int = 1_700_000_000) -> list[Candle]:
    return [Candle(start + i * step, c, c, c, c) for i, c in enumerate(closes)]
