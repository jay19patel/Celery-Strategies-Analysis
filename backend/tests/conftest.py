"""Shared fixtures: a fresh database per test, and a market feed made of a real captured option chain.

No test touches the network: the feed's REST client is a stub, its WebSocket is never started, and
the chain's expiry is shifted to `now + hours` so the fixture never ages out.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.brokers.paper import PaperBroker
from app.core.db import init_db
from app.core.events import EventHub
from app.market.feed import MarketFeed, PerpQuote
from app.market.options import OptionQuote, parse_ticker
from app.settings import Settings
from app.trading.desk import Desk
from app.trading.service import TradingService

FIXTURE = Path(__file__).parent / "fixtures" / "btc_chain.json"


def load_quotes(now: float | None = None, hours: float | None = None) -> list[OptionQuote]:
    data = json.loads(FIXTURE.read_text())
    now = time.time() if now is None else now
    hours = data["hours_to_expiry"] if hours is None else hours
    quotes = [parse_ticker(r, now) for r in data["rows"]]
    return [replace(q, expiry=now + hours * 3600) for q in quotes if q]


class StubRest:
    base_url = "http://stub"
    calls: dict[str, Any] = {}

    async def product(self, symbol: str) -> dict[str, Any]:
        return {"taker_commission_rate": "0.0001", "product_specs": {"premium_commission_rate": 0.035}}

    async def aclose(self) -> None:
        pass


class FakeFeed(MarketFeed):
    """The real feed's reads over a captured chain; `realised` sets the volatility of its bars."""

    def __init__(self, hours: float | None = None, realised: float = 0.15) -> None:
        super().__init__("http://stub", "ws://stub", ["BTC"])
        self.rest = StubRest()  # type: ignore[assignment]
        self.realised = realised
        self.load(hours)

    def load(self, hours: float | None = None) -> None:
        now = time.time()
        quotes = load_quotes(now, hours)
        self.book = {q.symbol: q for q in quotes}
        spot = quotes[0].spot or 80_000.0
        self.perps["BTCUSD"] = PerpQuote("BTCUSD", spot, spot, now)

    def set_quote(self, symbol: str, **changes: Any) -> None:
        self.book[symbol] = replace(self.book[symbol], **{"at": time.time()} | changes)

    def scale_marks(self, factor: float) -> None:
        """Every option repriced by `factor`, bid and ask with it."""
        for sym, q in list(self.book.items()):
            self.book[sym] = replace(
                q, at=time.time(), mark=(q.mark or 0) * factor,
                bid=q.bid * factor if q.bid else None, ask=q.ask * factor if q.ask else None,
            )

    def bars(self, symbol: str, limit: int = 3000) -> list[dict[str, Any]]:  # type: ignore[override]
        rng = random.Random(7)
        sigma = self.realised / math.sqrt(365 * 24 / 0.25)
        price, start, out = 80_000.0, int(time.time()) // 900 * 900 - 800 * 900, []
        for i in range(800):
            close = price * math.exp(rng.gauss(0, sigma))
            out.append({"time": start + i * 900, "open": price, "high": max(price, close), "low": min(price, close), "close": close, "volume": 1.0})
            price = close
        return out[-limit:]


@pytest.fixture(autouse=True)
def db(tmp_path: Path) -> None:
    init_db(str(tmp_path / "test.db"))


@pytest.fixture
def env() -> SimpleNamespace:
    """A desk and a trading service over the fake feed, with paper as the only broker."""
    holder = {"s": Settings()}
    feed = FakeFeed()
    hub = EventHub()
    desk = Desk(feed, lambda: holder["s"])
    paper = PaperBroker(feed, lambda: holder["s"], desk.fee_rate)
    brokers: dict[str, Any] = {"paper": paper}
    trading = TradingService(feed, desk, brokers, lambda: holder["s"], hub)

    def configure(**changes: Any) -> None:
        holder["s"] = holder["s"].model_copy(update=changes)

    return SimpleNamespace(feed=feed, hub=hub, desk=desk, paper=paper, brokers=brokers, trading=trading, configure=configure, settings=lambda: holder["s"])
