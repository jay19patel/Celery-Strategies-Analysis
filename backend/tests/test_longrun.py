"""What a process meant to run for years needs: no bad data kept, no log flood, no growth without end."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlmodel import select

from app.core import db as db_mod
from app.core import logstore
from app.core.db import session
from app.core.models import Bar, EventLog
from app.market.delta import Candle
from app.market.feed import MarketFeed, clean_candles


def bar(t: int, o: float, h: float, lo: float, c: float) -> Candle:
    return Candle(t, o, h, lo, c, 10.0)


def test_bad_prints_are_dropped_or_trimmed_against_the_last_good_close() -> None:
    """Shapes taken from the stored ETH series: a body at 99 in a 2,000 market, hours stuck at 1.0,
    a 28% wick on a quiet bar. A real 4% crash wick and a slow 30% slide survive."""
    rows = [
        bar(0, 2_000, 2_010, 1_990, 2_005),
        bar(1, 2_005, 2_082, 99.0, 99.0),  # body gone: dropped
        bar(2, 1.0, 1.0, 1.0, 1.0),  # a run of flat junk: dropped, however long
        bar(3, 1.0, 1.0, 1.0, 1.0),
        bar(4, 2_006, 2_576, 2_000, 2_010),  # a 28% wick on a real body: trimmed to the body
        bar(5, 2_010, 2_012, 1_930, 1_940),  # a real 4% flush below the body: kept as it is
    ]
    out = clean_candles(rows, reference=2_000)
    assert [c.time for c in out] == [0, 4, 5]
    assert out[1].high == 2_010 and out[1].low == 2_000
    assert out[2].low == 1_930
    slide = [bar(i, 2_000 * 0.97**i, 2_000 * 0.97**i, 2_000 * 0.97 ** (i + 1), 2_000 * 0.97 ** (i + 1)) for i in range(12)]
    assert len(clean_candles(slide)) == 12  # each bar is judged against the last good one, so a trend passes


async def test_the_collector_judges_new_bars_against_the_store_and_start_up_cleans_the_rest() -> None:
    class Rest:
        async def candles(self, symbol: str, interval: str, start: int, end: int) -> list[Candle]:
            return [bar(start, 2_000, 2_082, 99.0, 99.0), bar(start + 900, 2_080, 2_090, 2_070, 2_085)]

    feed = MarketFeed("http://stub", "ws://stub", ["ETH"])
    feed.rest = Rest()  # type: ignore[assignment]
    now = int(time.time()) // 900 * 900
    with session() as s:  # good history, and a bar that slipped in before this rule existed
        for i in range(8):
            s.add(Bar(symbol="ETHUSD", interval="15m", time=now - 14_400 + 900 * i, open=2_082, high=2_086, low=2_080, close=2_084))
        s.add(Bar(symbol="ETHUSD", interval="15m", time=now - 6_300, open=2_084, high=2_086, low=10.0, close=10.0))
        s.commit()
    assert MarketFeed.clean_stored() == {"dropped": 1, "trimmed": 0}
    assert await feed._store("ETHUSD", "15m", now - 3_600, now) == 1  # judged against 2,084: the 99 never lands
    assert [b["close"] for b in feed.bars("ETHUSD", 100)][-2:] == [2_084, 2_085]
    assert MarketFeed.clean_stored() == {"dropped": 0, "trimmed": 0}  # idempotent


def _logged() -> list[EventLog]:
    with session() as s:
        return list(s.exec(select(EventLog).where(EventLog.type == "Log")).all())


def test_a_repeating_warning_is_one_row_with_a_count_and_errors_keep_their_trace() -> None:
    handler = logstore.EventLogHandler()
    log = logging.getLogger("app.market.feed")
    log.addHandler(handler)
    try:
        for i in range(50):
            log.warning("websocket_down error=%s", f"timeout {i}")
        handler._open[("app.market.feed", "websocket_down error=%s")][4] = 0.0  # let the next one flush
        log.warning("websocket_down error=%s", "timeout 50")
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("job_failed id=%s", "x")
        logging.getLogger("app.core.events").warning("stored by the hub already")
        logging.getLogger("sqlalchemy.engine").warning("not ours")
    finally:
        log.removeHandler(handler)
    rows = _logged()
    assert len(rows) == 2  # fifty-one drops are one row; the hub's and the library's are not ours
    drops = next(r for r in rows if "websocket_down" in r.message)
    assert drops.level == "warning" and drops.data["count"] == 51
    error = next(r for r in rows if "job_failed" in r.message)
    assert error.level == "error" and "ValueError: boom" in error.data["trace"]


async def test_housekeeping_keeps_faults_longer_than_ordinary_events(env: Any) -> None:
    from app.engine import EVENT_RETENTION_DAYS

    old = time.time() - (EVENT_RETENTION_DAYS + 5) * 86_400
    ancient = time.time() - (logstore.KEEP_DAYS + 5) * 86_400
    with session() as s:
        s.add_all([
            EventLog(ts=old, type="StructureOpened", level="info", message="ordinary"),
            EventLog(ts=old, type="Log", level="error", message="a fault from last month"),
            EventLog(ts=ancient, type="Log", level="error", message="a fault from last season"),
        ])
        s.commit()
    from app.config import Config
    from app.engine import Engine

    await Engine(Config(engine_enabled=False)).housekeeping()
    with session() as s:
        kept = [e.message for e in s.exec(select(EventLog)).all()]
    assert kept == ["a fault from last month"]


def test_the_database_gives_back_what_pruning_frees(tmp_path: Path) -> None:
    db_mod.init_db(str(tmp_path / "vac.db"))
    with session() as s:
        assert s.exec(text("PRAGMA auto_vacuum")).one()[0] == 2  # incremental, set at start-up, on every connection
        s.add_all([Bar(symbol="X", interval="1m", time=i, open=1, high=1, low=1, close=1, volume=1) for i in range(20_000)])
        s.commit()
        s.exec(text("DELETE FROM bar"))
        s.commit()
    out = db_mod.maintain()
    assert out["freed_pages"] > 0 and out["free_pages"] == 0


async def test_option_fees_are_cached_per_underlying_not_per_listing(env: Any) -> None:
    quotes = env.feed.quotes("BTC")
    for q in quotes[:30]:
        await env.desk.fee_rate(q.symbol)
    await env.desk.fee_rate("BTCUSD")
    assert set(env.desk._fees) == {"BTC options", "BTCUSD"}
