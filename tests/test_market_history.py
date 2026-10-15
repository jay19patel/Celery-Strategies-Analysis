"""The history layer: bars, the analyst's numbers per bar, and process load — all kept.

Before this, everything analytical was recomputed from a fresh Delta REST call and thrown away.
Nothing could be backtested, no base rate could be counted, and an outage erased that window for
good. These tests are about what survives.
"""

from __future__ import annotations

import time

import pytest

from tradebuddy.api import Api
from tradebuddy.delta import Candle
from tradebuddy.events import MarketAnalysis
from tradebuddy.store import HEALTH_DAYS, SNAPSHOT_DAYS, Store

from .test_pipeline import make

STEP = 900


def bars(n: int, start: int = 1_700_000_000, price: float = 100.0, step: int = STEP) -> list[Candle]:
    """`n` closed 15m candles on the boundary, each a point higher than the last."""
    start = start // step * step
    return [Candle(start + i * step, price + i, price + i + 2, price + i - 2, price + i + 1, 10.0 + i) for i in range(n)]


# -- bars -------------------------------------------------------------------------


def test_bars_keep_the_first_reading_of_a_bar(tmp_path):
    """OR IGNORE, not REPLACE. A bar already stored was stored when it closed; a later REST read of
    it can only agree or be a revision, and a silently revised past is worse for a base rate than a
    missing one."""
    store = Store(str(tmp_path / "t.db"))
    first = bars(3)
    assert store.record_bars("BTCUSD", "15m", first) == 3

    revised = [Candle(c.time, c.open, c.high, c.low, 999.0, c.volume) for c in first]
    assert store.record_bars("BTCUSD", "15m", revised) == 0  # nothing written
    assert [r["close"] for r in store.bars("BTCUSD", "15m")] == [c.close for c in first]

    assert store.record_bars("BTCUSD", "15m", bars(5)) == 2  # only the two it did not have


def test_bars_carry_open_interest_and_funding_of_the_same_bar(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    candles = bars(3)
    oi = {int(c.time): 1000.0 + i for i, c in enumerate(candles)}
    funding = {int(candles[0].time): 0.01}  # only the first bar has one
    store.record_bars("BTCUSD", "15m", candles, oi, funding)

    rows = store.bars("BTCUSD", "15m")
    assert [r["oi"] for r in rows] == [1000.0, 1001.0, 1002.0]
    assert [r["funding"] for r in rows] == [0.01, None, None]  # absent, not invented


def test_bars_are_separate_per_symbol_and_interval(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.record_bars("BTCUSD", "15m", bars(3))
    store.record_bars("ETHUSD", "15m", bars(2))
    store.record_bars("BTCUSD", "1h", bars(4, step=3600), {}, {})
    assert len(store.bars("BTCUSD", "15m")) == 3
    assert len(store.bars("ETHUSD", "15m")) == 2
    assert len(store.bars("BTCUSD", "1h")) == 4
    assert store.last_bar_time("ETHUSD", "15m") == bars(2)[-1].time
    assert store.last_bar_time("SOLUSD", "15m") == 0  # nothing stored: a collector takes a run-up


def test_a_gap_is_reported_not_smoothed_over(tmp_path):
    """A chart that joins across a missing hour draws a move the market never made."""
    store = Store(str(tmp_path / "t.db"))
    every = bars(10)
    store.record_bars("BTCUSD", "15m", [*every[:3], *every[6:]])  # bars 3, 4, 5 never arrived
    assert store.bar_gaps("BTCUSD", "15m") == [(every[2].time, every[6].time)]

    store.record_bars("BTCUSD", "15m", every)
    assert store.bar_gaps("BTCUSD", "15m") == []


async def test_the_engine_collects_only_the_bars_it_is_missing(cfg, exchange):
    """Steady state is a handful of bars; a restart after an outage fills the hole by itself."""
    system = await make(cfg, exchange)
    everything = bars(40)
    exchange.history = everything
    exchange.series = {"OI:BTCUSD": everything, "FUNDING:BTCUSD": everything}
    now = everything[-1].time + STEP

    note = await system._collect_bars_once(now)
    assert "bars" in note
    stored = system.store.bars("BTCUSD", "15m")
    assert len(stored) == len(everything)

    # Nothing new since: the pass asks for two bars and stores none of them twice.
    calls_before = len(system.store.bars("BTCUSD", "15m"))
    await system._collect_bars_once(now)
    assert len(system.store.bars("BTCUSD", "15m")) == calls_before


async def test_one_symbols_outage_never_stops_the_others(cfg, exchange):
    system = await make(cfg, exchange)
    exchange.history = bars(5)

    async def candles(symbol, resolution, count):
        if symbol.endswith("ETHUSD"):
            raise TimeoutError("exchange down")
        return exchange.history[-count:]

    system.market_client.candles = candles
    note = await system._collect_bars_once(bars(5)[-1].time + STEP)
    assert "no data for" in note and "ETHUSD" in note
    assert len(system.store.bars("BTCUSD", "15m")) == 5  # the one that answered is stored


# -- the analysis, kept -----------------------------------------------------------


async def test_every_analysis_is_stored_as_numbers_not_json(cfg, exchange):
    """The event log kept this as a JSON blob for three days — long enough to look at, far too
    short to count a base rate from, and in a shape no query can scan."""
    system = await make(cfg, exchange)
    bar = 1_700_000_000 // STEP * STEP
    await system.on_analysis(MarketAnalysis(
        symbol="BTCUSD",
        context={"price": 100.0, "atm_iv": 0.55, "iv_rv_ratio": 1.4, "implied_move_label": "Friday",
                 "lean": {"bullish": 3.0, "bearish": 1.0, "lean": "bullish"}},
        insights=[{"key": "iv_rich", "severity": "watch", "bias": "volatility", "title": "", "detail": ""},
                  {"key": "trend", "severity": "info", "bias": "bullish", "title": "", "detail": ""}],
        forecast={"bar_time": bar, "horizon_hours": 24},
    ))

    row = system.store.snapshots("BTCUSD")[-1]
    assert (row["price"], row["atm_iv"], row["iv_rv_ratio"]) == (100.0, 0.55, 1.4)
    assert row["insight_keys"] == "iv_rich,trend"
    assert row["lean"] == 2.0  # bullish weight minus bearish
    assert row["keep"] == 1  # on a 15-minute boundary, so kept for good


async def test_a_label_never_lands_in_a_number_column(cfg, exchange):
    """market_context carries strings too. SQLite would store one in a REAL column without
    complaint and hand it back as text to whatever next did arithmetic on it."""
    system = await make(cfg, exchange)
    await system.on_analysis(MarketAnalysis(
        symbol="BTCUSD",
        context={"price": "not a number", "atm_iv": None, "rv_24h": True},
        insights=[],
    ))
    row = system.store.snapshots("BTCUSD")[-1]
    assert row["price"] is None and row["atm_iv"] is None
    assert row["rv_24h"] is None  # True is an int in Python and is not a measurement


def test_off_boundary_snapshots_are_pruned_and_boundary_ones_are_kept(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    now = time.time()
    old = now - (SNAPSHOT_DAYS + 1) * 86_400
    boundary = 1_700_000_000 // STEP * STEP
    store.record_snapshot("BTCUSD", boundary, old, {"price": 1.0}, "", 0.0)
    store.record_snapshot("BTCUSD", boundary + 60, old, {"price": 2.0}, "", 0.0)  # not on a bar
    store.record_snapshot("BTCUSD", boundary + 120, now, {"price": 3.0}, "", 0.0)

    assert store.prune_snapshots(now) == 1
    assert [r["price"] for r in store.snapshots("BTCUSD")] == [1.0, 3.0]


# -- process load -----------------------------------------------------------------


async def test_process_load_is_kept_so_yesterday_has_an_answer(cfg, exchange):
    """The monitor has always measured CPU, memory and loop lag every two seconds and stored none
    of it, so the System page could only ever show the current instant."""
    system = await make(cfg, exchange)
    system.remote_processes["analyst"] = {
        "process": {"role": "analyst", "process_cpu_pct": 4.0, "process_memory_mb": 105.0, "loop_lag_max_ms": 7.0},
        "jobs": [], "status": {}, "at": time.time(),
    }
    now = time.time()
    assert system._record_health_once(now - 600) == "2 processes"
    system._record_health_once(now - 300)

    series = (await Api(system).health(hours=48))["processes"]
    assert set(series) == {"all", "analyst"}
    assert [p["memory_mb"] for p in series["analyst"]] == [105.0, 105.0]
    assert series["analyst"][0]["ts"] < series["analyst"][1]["ts"]  # oldest first


def test_old_health_rows_are_pruned(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    now = time.time()
    store.record_health("engine", now - (HEALTH_DAYS + 1) * 86_400, 10.0, 100.0, 1.0)
    store.record_health("engine", now, 11.0, 101.0, 2.0)
    assert store.prune_health(now) == 1
    assert [r["cpu_pct"] for r in store.health()] == [11.0]


# -- the reads the dashboard makes ------------------------------------------------


async def test_the_bars_route_reports_its_gaps(cfg, exchange):
    system = await make(cfg, exchange)
    every = bars(10, start=int(time.time()) - 10 * STEP)
    system.store.record_bars("BTCUSD", "15m", [*every[:3], *every[6:]])

    out = await Api(system).bars("BTCUSD", "15m", days=7)
    assert len(out["bars"]) == 7
    assert out["gaps"] == [{"after": every[2].time, "before": every[6].time}]
    assert out["bars"][0]["time"] < out["bars"][-1]["time"]  # oldest first


@pytest.mark.parametrize(("days", "expected"), [(0, 0.25), (9999, 365)])
async def test_the_reads_clamp_their_window(cfg, exchange, days, expected):
    system = await make(cfg, exchange)
    assert (await Api(system).bars("BTCUSD", "15m", days=days))["days"] == expected
    assert (await Api(system).snapshots("BTCUSD", days=days))["days"] == expected
