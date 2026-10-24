"""The Market Desk service: every reading builds from the stored bars, is cached per bar, and every
card's claim is written down once a bar and marked once its horizon has passed."""

from __future__ import annotations

import time
from typing import Any

import pytest
from sqlmodel import select

from app.analysis.service import MarketDesk
from app.core.db import session
from app.core.models import Prediction


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


async def test_claims_are_written_once_a_bar_and_marked_when_due(desk: MarketDesk, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_series(*_: Any) -> list:
        return []

    monkeypatch.setattr(desk, "_series", no_series)
    await desk.analyse()
    await desk.analyse()
    with session() as s:
        rows = s.exec(select(Prediction)).all()
    assert rows and len({(r.source, r.bar_time) for r in rows}) == len(rows)

    # Pretend they were made two days ago on bars that exist, so every horizon has passed.
    bars = desk.feed.bars("BTCUSD", 800)
    old = int(bars[-200]["time"])
    with session() as s:
        for r in s.exec(select(Prediction)).all():
            s.delete(r)
            s.add(Prediction(**(r.model_dump() | {"bar_time": old, "ts": time.time() - 2 * 86_400})))
        s.commit()
    note = await desk.settle()
    assert note.startswith("marked") and not note.startswith("marked 0 ")
    view = await desk.verdicts("BTC")
    assert view["verdicts"] and "record" in view


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
