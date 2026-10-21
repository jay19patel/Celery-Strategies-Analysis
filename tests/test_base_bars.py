"""The base series: the one raw timeframe stored below 15m, and everything folded from it.

Why it earns its keep and its retention rule: a fifteen-minute bar cannot see inside itself, so a
session high, a volume profile and a liquidity sweep are all questions it has no answer to. A
minute bar has the answer and costs 90 bytes — but 1,440 of them a day a symbol is ~45 MB a year
against ~5 MB for the 15m series, which is why this is the only bar series with a retention rule
and the 15m one is kept for good.
"""

from __future__ import annotations

import time

import pytest

from tradebuddy import resample
from tradebuddy.api import Api, _bar_source
from tradebuddy.delta import Candle
from tradebuddy.store import BASE_BAR_DAYS, BASE_INTERVAL, Store

from .test_pipeline import make

MINUTE = 60


def minute_bars(n: int, start: int | None = None, price: float = 100.0) -> list[Candle]:
    """`n` closed one-minute candles, starting on an HOUR boundary.

    The hour matters. Folding is bucketed by `time // span * span`, so how many folded bars a run
    produces depends on where its first bar sits inside the first bucket. Defaulting the start to
    "now minus n minutes" made that a function of the wall clock: this file asserted 11 folded 5m
    bars and got 12 whenever the clock happened to land on a 5-minute boundary — a test that
    passed four times in five. An hour divides 1m, 3m, 5m, 15m, 30m and 1h, so every fold these
    tests make is now the same fold on every run."""
    start = ((int(time.time()) - n * MINUTE) // 3600 * 3600 if start is None else start) // MINUTE * MINUTE
    return [
        Candle(start + i * MINUTE, price + i, price + i + 2, price + i - 2, price + i + 1, 10.0 + i)
        for i in range(n)
    ]


# -- stored, and kept for as long as it is worth -------------------------------------


def test_the_base_series_lives_beside_the_fifteen_minute_one(tmp_path):
    """Same table, different interval. Neither can overwrite the other."""
    store = Store(str(tmp_path / "t.db"))
    base = minute_bars(30, start=1_700_006_400)
    assert store.record_bars("BTCUSD", BASE_INTERVAL, base) == 30
    assert store.record_bars("BTCUSD", "15m", base[:2]) == 2
    assert len(store.bars("BTCUSD", BASE_INTERVAL)) == 30
    assert len(store.bars("BTCUSD", "15m")) == 2


def test_only_the_base_series_is_pruned(tmp_path):
    """The 15m series is the permanent record of what this market did. The base is a working set:
    long enough for every session, profile and sweep the dashboard counts, and refetchable."""
    store = Store(str(tmp_path / "t.db"))
    now = int(time.time())
    old = now - (BASE_BAR_DAYS + 5) * 86_400
    store.record_bars("BTCUSD", BASE_INTERVAL, minute_bars(10, start=old))
    store.record_bars("BTCUSD", BASE_INTERVAL, minute_bars(10, start=now - 3600))
    store.record_bars("BTCUSD", "15m", minute_bars(10, start=old))

    assert store.prune_bars(now) == 10
    assert len(store.bars("BTCUSD", BASE_INTERVAL)) == 10  # the recent ones survive
    assert len(store.bars("BTCUSD", "15m")) == 10  # untouched, whatever its age


def test_coverage_says_what_is_stored_and_what_is_missing(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    every = minute_bars(20, start=1_700_006_400)
    store.record_bars("BTCUSD", BASE_INTERVAL, [*every[:5], *every[9:]])

    cov = store.bar_coverage("BTCUSD", BASE_INTERVAL)
    assert cov["bars"] == 16
    assert cov["expected"] == 20
    assert cov["missing"] == 4
    assert cov["first"] == every[0].time and cov["last"] == every[-1].time


def test_coverage_of_a_series_that_was_never_stored_is_empty(tmp_path):
    cov = Store(str(tmp_path / "t.db")).bar_coverage("BTCUSD", BASE_INTERVAL)
    assert cov == {"symbol": "BTCUSD", "interval": BASE_INTERVAL, "bars": 0, "first": None,
                   "last": None, "expected": 0, "missing": 0}


# -- collected on its own minute cadence ----------------------------------------------


async def test_the_engine_collects_the_base_series_without_open_interest(cfg, exchange):
    """Open interest and funding are levels that barely move in a minute and already ride the 15m
    series. Three REST calls a symbol a minute to carry them would be the engine's biggest cost."""
    system = await make(cfg, exchange)
    every = minute_bars(40)
    exchange.series_by_interval[("BTCUSD", BASE_INTERVAL)] = every
    exchange.series_by_interval[("ETHUSD", BASE_INTERVAL)] = every
    asked: list[tuple[str, str]] = []
    real = system.market_client.candles

    async def spy(symbol, resolution, count):
        asked.append((symbol, resolution))
        return await real(symbol, resolution, count)

    system.market_client.candles = spy
    note = await system._collect_base_bars_once(every[-1].time + MINUTE)

    assert f"{BASE_INTERVAL} bars" in note
    assert len(system.store.bars("BTCUSD", BASE_INTERVAL)) == 40
    assert all(res == BASE_INTERVAL for _, res in asked)
    assert not [s for s, _ in asked if s.startswith(("OI:", "FUNDING:"))]


async def test_collecting_the_base_series_twice_stores_nothing_the_second_time(cfg, exchange):
    """Idempotent against the same table as the backfill, so the two can run at once."""
    system = await make(cfg, exchange)
    every = minute_bars(20)
    exchange.series_by_interval[("BTCUSD", BASE_INTERVAL)] = every
    now = every[-1].time + MINUTE

    await system._collect_base_bars_once(now)
    before = len(system.store.bars("BTCUSD", BASE_INTERVAL))
    await system._collect_base_bars_once(now)
    assert len(system.store.bars("BTCUSD", BASE_INTERVAL)) == before


async def test_one_symbols_outage_never_stops_the_base_collection(cfg, exchange):
    system = await make(cfg, exchange)
    every = minute_bars(5)
    exchange.series_by_interval[("BTCUSD", BASE_INTERVAL)] = every

    async def candles(symbol, resolution, count):
        if symbol.endswith("ETHUSD"):
            raise TimeoutError("exchange down")
        return every[-count:]

    system.market_client.candles = candles
    note = await system._collect_base_bars_once(every[-1].time + MINUTE)
    assert "no data for" in note and "ETHUSD" in note
    assert len(system.store.bars("BTCUSD", BASE_INTERVAL)) == 5


# -- the backfill ---------------------------------------------------------------------


async def test_the_backfill_resumes_from_what_is_already_stored(cfg, exchange, tmp_path):
    """525,600 minute bars a symbol a year. A run that dies halfway must not start again at the far
    end of the window to find the first row it does not have."""
    from tradebuddy.analyst import backfill

    db = str(tmp_path / "t.db")
    every = minute_bars(600)
    exchange.series_by_interval[("BTCUSD", BASE_INTERVAL)] = every
    store = Store(db)
    store.record_bars("BTCUSD", BASE_INTERVAL, every[:500])
    store.db.close()

    client = exchange.client("http://fake")
    report = await backfill(db, ["BTCUSD"], days=2, interval=BASE_INTERVAL, client=client)

    assert report["BTCUSD"]["written"] == 100
    assert report["BTCUSD"]["resumed_from"] == every[499].time + MINUTE
    assert report["BTCUSD"]["bars"] == 600


async def test_the_backfill_run_again_writes_nothing(cfg, exchange, tmp_path):
    from tradebuddy.analyst import backfill

    db = str(tmp_path / "t.db")
    exchange.series_by_interval[("BTCUSD", BASE_INTERVAL)] = minute_bars(200)
    client = exchange.client("http://fake")

    first = await backfill(db, ["BTCUSD"], days=2, interval=BASE_INTERVAL, client=client)
    second = await backfill(db, ["BTCUSD"], days=2, interval=BASE_INTERVAL, client=client)
    assert first["BTCUSD"]["written"] == 200
    assert second["BTCUSD"]["written"] == 0
    assert second["BTCUSD"]["bars"] == 200


async def test_the_backfill_reports_its_holes_rather_than_filling_them(cfg, exchange, tmp_path):
    """A gap is market the exchange never gave us. Interpolating it is how a base rate comes to be
    counted over bars that did not exist."""
    from tradebuddy.analyst import backfill

    db = str(tmp_path / "t.db")
    every = minute_bars(100)
    exchange.series_by_interval[("BTCUSD", BASE_INTERVAL)] = [*every[:40], *every[55:]]
    client = exchange.client("http://fake")

    report = await backfill(db, ["BTCUSD"], days=2, interval=BASE_INTERVAL, client=client)
    assert report["BTCUSD"]["ok"] is False
    assert report["BTCUSD"]["missing_bars"] == 15
    assert report["BTCUSD"]["gaps"] == [
        {"after": every[39].time, "before": every[55].time, "missing": 15},
    ]


async def test_the_backfill_skips_open_interest_for_the_base_series_only(cfg, exchange, tmp_path):
    from tradebuddy.analyst import backfill

    asked: list[str] = []

    class Spy:
        async def history_range(self, symbol, resolution, start, end, page=1900):
            asked.append(symbol)
            return [c for c in minute_bars(60, start=1_700_006_400) if start <= c.time < end]

        async def aclose(self):
            pass

    await backfill(str(tmp_path / "a.db"), ["BTCUSD"], days=1, interval=BASE_INTERVAL, client=Spy())
    assert not [s for s in asked if s.startswith(("OI:", "FUNDING:"))]

    asked.clear()
    await backfill(str(tmp_path / "b.db"), ["BTCUSD"], days=1, interval="15m", client=Spy())
    assert [s for s in asked if s.startswith("OI:")]


# -- served, and honest about where it came from --------------------------------------


@pytest.mark.parametrize(
    ("interval", "source"),
    [("1m", "1m"), ("3m", "1m"), ("5m", "1m"), ("15m", "15m"), ("30m", "15m"), ("1h", "15m"), ("1d", "15m")],
)
def test_every_timeframe_is_built_from_the_coarser_stored_series_that_divides_it(interval, source):
    """Reading 96 bars to build a day beats reading 1,440 to build the same day out of the same
    market — and nothing is ever fetched twice for two timeframes that must agree."""
    assert _bar_source(interval) == source


async def test_an_hour_served_is_the_four_quarter_hours_underneath_it(cfg, exchange):
    system = await make(cfg, exchange)
    start = (int(time.time()) - 8 * 3600) // 3600 * 3600
    quarters = [
        Candle(start + i * 900, 100.0 + i, 102.0 + i, 98.0 + i, 101.0 + i, 5.0)
        for i in range(16)
    ]
    system.store.record_bars("BTCUSD", "15m", quarters)

    out = await Api(system).bars("BTCUSD", "1h", days=1)
    assert out["derived"] is True and out["source_interval"] == "15m"
    assert len(out["bars"]) == 4
    first = out["bars"][0]
    assert first["time"] == start
    assert first["open"] == quarters[0].open
    assert first["close"] == quarters[3].close
    assert first["high"] == max(c.high for c in quarters[:4])
    assert first["volume"] == 20.0
    assert first["coverage"] == 1.0


async def test_a_stored_timeframe_is_served_as_stored(cfg, exchange):
    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", BASE_INTERVAL, minute_bars(30))
    out = await Api(system).bars("BTCUSD", BASE_INTERVAL, days=1)
    assert out["derived"] is False and out["source_interval"] == BASE_INTERVAL
    assert len(out["bars"]) == 30


async def test_five_minutes_comes_from_the_base_not_from_fifteen(cfg, exchange):
    """15m does not divide 5m. Without a base series this timeframe cannot exist at all, which is
    the whole reason the base is stored."""
    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", BASE_INTERVAL, minute_bars(60))
    out = await Api(system).bars("BTCUSD", "5m", days=1)
    assert out["source_interval"] == BASE_INTERVAL and out["derived"] is True
    # Twelve complete buckets, not eleven: a bucket is finished once the source has run PAST its
    # end, and the last 1m bar's own minute carries the series to exactly T+60. Nothing here is
    # still forming, because the fixture starts on an hour boundary.
    assert len(out["bars"]) == 12
    assert all(b["time"] % 300 == 0 for b in out["bars"])
    assert all(b["bars"] == 5 and b["coverage"] == 1.0 for b in out["bars"])


async def test_the_bucket_still_forming_is_never_served(cfg, exchange):
    """The rule the count above depends on, asserted directly rather than inferred from it. A bar
    still being built must not be served as a closed one — a strategy cannot tell the difference
    and would act on a high that has not happened yet."""
    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", BASE_INTERVAL, minute_bars(62))  # two minutes into bucket 13
    out = await Api(system).bars("BTCUSD", "5m", days=1)
    assert len(out["bars"]) == 12  # the 13th has only two of its five minutes
    assert out["bars"][-1]["bars"] == 5


async def test_an_unknown_timeframe_is_refused(cfg, exchange):
    system = await make(cfg, exchange)
    with pytest.raises(Exception, match="unknown interval"):
        await Api(system).bars("BTCUSD", "7m")


async def test_the_served_limit_is_capped(cfg, exchange):
    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", BASE_INTERVAL, minute_bars(50))
    assert len((await Api(system).bars("BTCUSD", BASE_INTERVAL, days=1, limit=10))["bars"]) == 10


async def test_a_derived_timeframe_reports_its_gaps(cfg, exchange):
    """A hole in the base becomes a hole in the hour, named rather than drawn through."""
    system = await make(cfg, exchange)
    start = (int(time.time()) - 8 * 3600) // 3600 * 3600
    quarters = [Candle(start + i * 900, 100.0, 102.0, 98.0, 101.0, 5.0) for i in range(24)]
    system.store.record_bars("BTCUSD", "15m", [*quarters[:4], *quarters[8:]])

    out = await Api(system).bars("BTCUSD", "1h", days=1)
    # Six hours of quarter-hours with the second hour missing: five hours survive, and the hole
    # between the first and the third is named rather than drawn through.
    assert [b["time"] for b in out["bars"]] == [start + h * 3600 for h in (0, 2, 3, 4, 5)]
    assert out["gaps"] == [{"after": start, "before": start + 2 * 3600}]


# -- the duplication, checked ----------------------------------------------------------


async def test_the_stored_quarter_hours_are_what_the_minutes_fold_to(cfg, exchange):
    """Both series are stored, so both can be asked the same question. If they ever disagree, one
    of them is wrong and nothing downstream would say which."""
    system = await make(cfg, exchange)
    start = 1_700_006_400
    base = minute_bars(120, start=start)
    system.store.record_bars("BTCUSD", BASE_INTERVAL, base)
    folded = resample.fold([vars(c) | {"oi": None, "funding": None} for c in base], BASE_INTERVAL, "15m")
    system.store.record_bars("BTCUSD", "15m", [
        Candle(b["time"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in folded
    ])

    stored = system.store.bars("BTCUSD", "15m")
    assert len(stored) == 8
    for row, bar in zip(stored, folded, strict=True):
        assert (row["time"], row["open"], row["high"], row["low"], row["close"], row["volume"]) == (
            bar["time"], bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"],
        )
