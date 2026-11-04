"""The Market Desk service: every reading builds from the stored bars, is cached per bar, and every
card's claim is written down once a bar and marked once its horizon has passed."""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.analysis.service import MarketDesk


@pytest.fixture
def desk(env: Any) -> MarketDesk:
    env.feed.set_perp(80_000.0, bid=79_999.5, ask=80_000.5)
    return MarketDesk(env.feed, env.settings, env.hub)


async def test_every_reading_is_built(desk: MarketDesk) -> None:
    outlook = await desk.outlook("BTC")
    assert outlook["timeframes"] and "summary" in outlook
    pulse = await desk.pulse("BTC")
    assert "state" in pulse
    structure = await desk.structure("BTC")
    assert structure["ready"] and {"liquidity", "zones", "profile", "flow", "nearby"} <= set(structure)
    assert structure["profile"]["source"] == "1m"  # the minute base is preferred when there is enough of it
    analysis = desk.analysis("BTC")
    assert analysis["options"]["day"]["atm_iv"] and analysis["price"] == 80_000.0


async def test_readings_are_cached_once_a_bar(desk: MarketDesk) -> None:
    first = await desk.outlook("BTC")
    assert await desk.outlook("BTC") is first


async def test_folded_bars_never_mix_timeframes(desk: MarketDesk) -> None:
    hourly = await desk.bars("BTC", "1h", days=10, limit=50)
    assert hourly["derived"] and hourly["source_interval"] == "15m"
    assert all(b["time"] % 3600 == 0 for b in hourly["bars"])
    five = await desk.bars("BTC", "5m", days=1, limit=20)
    assert five["source_interval"] == "1m" and len(five["bars"]) == 20


async def test_the_options_book_is_recorded_each_minute(desk: MarketDesk) -> None:
    await desk.record_options()
    await desk.record_options()  # the same minute: one row
    rows = desk.options_history("BTC", 1)
    assert len(rows) == 1 and rows[0]["pcr_oi"]


async def test_the_bar_collector_fills_both_ends_and_never_rewrites() -> None:
    from app.market.delta import Candle
    from app.market.feed import SERIES, MarketFeed

    class Rest:
        calls: list[tuple[int, int]] = []

        async def candles(self, symbol: str, interval: str, start: int, end: int) -> list[Candle]:
            self.calls.append((start, end))
            first = (start + 899) // 900 * 900
            return [Candle(t, 1.0, 1.0, 1.0, 1.0) for t in range(first, end - 900 + 1, 900)]

    feed = MarketFeed("http://stub", "ws://stub", ["BTC"])
    feed.rest = Rest()  # type: ignore[assignment]
    now = int(time.time()) // 900 * 900
    await feed._store("BTCUSD", "15m", now - 2 * 86_400, now)  # only the last two days are stored
    await feed.collect_bars("15m")
    rows = feed.bars("BTCUSD", 100_000)
    assert rows[0]["time"] <= now - (SERIES["15m"] - 1) * 86_400  # the year before them was filled in
    count = len(rows)
    await feed.collect_bars("15m")
    assert len(feed.bars("BTCUSD", 100_000)) == count  # nothing twice


async def test_trendlines_read_a_bounded_window(desk: MarketDesk, monkeypatch: Any) -> None:
    """Pairing every two swings over every bar is quadratic: over the full structure window it took
    minutes of CPU on the server and starved the event loop, so trendlines see only the recent bars."""
    from app.analysis import service, trendlines

    seen: list[int] = []
    real = trendlines.analyze_trendlines
    monkeypatch.setattr(trendlines, "analyze_trendlines", lambda bars: seen.append(len(bars)) or real(bars))
    monkeypatch.setattr(service, "TRENDLINE_BARS", 300)  # under the fixture's 800 stored bars
    structure = await desk.structure("BTC")
    assert seen == [300] and structure["bars"] > 300  # the rest of the structure still reads every bar
