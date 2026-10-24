"""One base series, every higher timeframe folded from it.

The tests that matter here are not the arithmetic ones — summing volume is hard to get wrong. They
are the two that catch the failures nothing downstream would report:

  * the **look-ahead** test. A bar's value must not change when more bars arrive after it. Get this
    wrong and every holdout score, base rate and backtest in the system is measuring a future it
    could not have seen, and nothing will say so.
  * the **parity** test. The 15-minute series is stored and is also foldable from the minute bars.
    Two paths to one number is a drift waiting to happen, so the duplication is only allowed on
    condition that it is asserted.
"""

from __future__ import annotations

import pytest

from app.analysis import resample
from app.market.delta import Candle

MINUTE = 60
HOUR = 3600
DAY = 86_400

# A Thursday 00:00:00 UTC, so a daily bucket starts exactly here.
MIDNIGHT = 1_700_006_400


def minutes(n: int, start: int = MIDNIGHT, price: float = 100.0) -> list[dict]:
    """`n` one-minute bars on the boundary, each a point above the last."""
    return [
        {"time": start + i * MINUTE, "open": price + i, "high": price + i + 2,
         "low": price + i - 2, "close": price + i + 1, "volume": 10.0, "oi": 1000.0 + i, "funding": 0.01}
        for i in range(n)
    ]


# -- the clock ------------------------------------------------------------------------


def test_buckets_start_on_a_utc_boundary():
    """Epoch seconds are UTC and every interval divides the epoch evenly, so a daily bar opens at
    00:00 UTC and a 4-hour bar at 00, 04, 08 — with no timezone arithmetic anywhere."""
    rows = minutes(3 * DAY // MINUTE)
    for interval, span in (("1h", HOUR), ("4h", 4 * HOUR), ("1d", DAY)):
        folded = resample.fold(rows, "1m", interval)
        assert folded, interval
        assert all(b["time"] % span == 0 for b in folded), interval
    assert resample.fold(rows, "1m", "1d")[0]["time"] == MIDNIGHT


def test_a_bar_is_stamped_when_it_opened_and_covers_what_follows():
    """label = left, closed = left. Open from the first bar, close from the last, extremes across,
    volume summed. Stamp it on the right instead and every join shifts by one bar."""
    rows = minutes(15)
    (bar,) = resample.fold(rows, "1m", "15m")
    assert bar["time"] == MIDNIGHT
    assert bar["open"] == rows[0]["open"]
    assert bar["close"] == rows[-1]["close"]
    assert bar["high"] == max(r["high"] for r in rows)
    assert bar["low"] == min(r["low"] for r in rows)
    assert bar["volume"] == sum(r["volume"] for r in rows)
    assert bar["bars"] == 15 and bar["coverage"] == 1.0


def test_open_interest_is_carried_not_summed():
    """OI and funding are levels, not flows. Adding up open interest would be meaningless, so the
    bucket takes the last one it saw."""
    (bar,) = resample.fold(minutes(15), "1m", "15m")
    assert bar["oi"] == 1000.0 + 14
    assert bar["funding"] == 0.01


def test_a_level_missing_from_the_last_bar_falls_back_to_the_newest_one_that_had_it():
    rows = minutes(15)
    rows[-1]["oi"] = None
    rows[-2]["oi"] = None
    (bar,) = resample.fold(rows, "1m", "15m")
    assert bar["oi"] == 1000.0 + 12


# -- nothing still forming ------------------------------------------------------------


def test_the_bucket_still_forming_is_never_emitted():
    """A bucket is closed when the source has run past its end — not when it has enough bars. This
    is what stops a half-finished day being drawn as a closed one."""
    rows = minutes(20)  # one full 15m bucket, then five minutes into the next
    folded = resample.fold(rows, "1m", "15m")
    assert [b["time"] for b in folded] == [MIDNIGHT]


def test_a_quiet_but_finished_bucket_is_emitted_while_a_busy_unfinished_one_is_not():
    """Two buckets, both holding three bars. The first is finished because the series moved past
    it; the second is not. Counting bars cannot tell them apart; reach can."""
    rows = minutes(3) + minutes(3, start=MIDNIGHT + 15 * MINUTE)
    folded = resample.fold(rows, "1m", "15m", min_coverage=0.1)
    assert [b["time"] for b in folded] == [MIDNIGHT]
    assert folded[0]["bars"] == 3 and folded[0]["coverage"] == 0.2


# -- the look-ahead guarantee ---------------------------------------------------------


@pytest.mark.parametrize("interval", ["5m", "15m", "1h", "4h"])
def test_a_folded_bar_never_changes_once_more_bars_arrive(interval):
    """THE test. Fold to bar t, then fold again with k more bars: every bar at or before t must be
    identical. A value that moves when the future arrives is a value that saw the future."""
    rows = minutes(6 * DAY // MINUTE)
    cut = len(rows) // 2
    early = resample.fold(rows[:cut], "1m", interval)
    late = resample.fold(rows, "1m", interval)
    assert early, interval
    by_time = {b["time"]: b for b in late}
    for bar in early:
        assert by_time[bar["time"]] == bar, f"{interval} bar at {bar['time']} changed when later bars arrived"


def test_folding_one_bar_at_a_time_gives_the_same_series_as_folding_all_of_it():
    """The incremental path and the whole-history path must agree, or live and backtest diverge."""
    rows = minutes(3 * DAY // MINUTE)
    whole = resample.fold(rows, "1m", "1h")
    grown: dict[int, dict] = {}
    for i in range(1, len(rows) + 1):
        for bar in resample.fold(rows[:i], "1m", "1h"):
            grown[bar["time"]] = bar
    assert [grown[b["time"]] for b in whole] == whole


# -- holes, stated rather than smoothed -----------------------------------------------


def test_a_bucket_below_its_coverage_floor_is_dropped():
    rows = minutes(30)
    del rows[16:28]  # gut the second 15-minute bucket, leaving 3 of 15
    folded = resample.fold(rows, "1m", "15m", min_coverage=1.0)
    assert [b["time"] for b in folded] == [MIDNIGHT]


def test_a_bucket_above_its_floor_is_kept_and_says_how_complete_it_is():
    rows = minutes(2 * HOUR // MINUTE)
    del rows[61:65]  # four minutes out of the second hour
    folded = resample.fold(rows, "1m", "1h", min_coverage=0.9)
    assert [b["time"] for b in folded] == [MIDNIGHT, MIDNIGHT + HOUR]
    assert folded[1]["bars"] == 56
    assert folded[1]["coverage"] == round(56 / 60, 4)


def test_the_default_floor_is_strict_for_short_folds_and_looser_for_long_ones():
    """A tenth of an hour missing barely moves it; a tenth of a day is two and a half hours of
    market nobody saw. The defaults say so rather than using one number for both."""
    assert resample.MIN_COVERAGE["5m"] == 1.0
    assert resample.MIN_COVERAGE["1d"] < 1.0


def test_gaps_are_reported_not_filled():
    rows = minutes(10)
    del rows[4:7]
    holes = resample.gaps(rows, "1m")
    assert holes == [(MIDNIGHT + 3 * MINUTE, MIDNIGHT + 7 * MINUTE)]


def test_validate_names_everything_wrong_with_a_series():
    rows = minutes(10)
    del rows[4:7]
    rows.append(dict(rows[0], time=MIDNIGHT + 30))  # misaligned, and out of order after the gap
    rows.append(dict(rows[0]))  # a duplicate of the first bar
    report = resample.validate(rows, "1m")
    assert report["ok"] is False
    assert report["duplicates"] == [MIDNIGHT]
    assert report["misaligned"] == [MIDNIGHT + 30]
    assert report["out_of_order"] is True
    assert report["missing_bars"] == 3


def test_a_clean_series_validates():
    report = resample.validate(minutes(120), "1m")
    assert report["ok"] is True
    assert report["bars"] == 120 and report["missing_bars"] == 0


# -- what cannot be folded ------------------------------------------------------------


def test_a_fold_that_is_not_a_timeframe_is_refused():
    assert resample.divides("4h", "1d") == 6  # a day is six 4-hour bars
    assert resample.divides("1m", "15m") == 15
    with pytest.raises(resample.ResampleError, match="shorter than source"):
        resample.divides("15m", "5m")
    with pytest.raises(resample.ResampleError, match="whole number"):
        resample.divides("3m", "5m")  # five minutes is one and two-thirds three-minute bars
    with pytest.raises(resample.ResampleError, match="unknown interval"):
        resample.seconds("7m")


def test_rows_that_are_not_the_source_they_are_called_are_refused():
    """The quietest possible bug: hand minute bars to a fold told they are 15-minute bars and it
    builds buckets of the wrong size, reports 1,500% coverage, and nothing raises. The bar times
    themselves settle it."""
    with pytest.raises(resample.ResampleError, match="not on a 15m boundary"):
        resample.fold(minutes(60), "15m", "1h")


def test_folding_to_the_same_interval_passes_the_rows_through():
    rows = minutes(5)
    assert [b["time"] for b in resample.fold(rows, "1m", "1m")] == [r["time"] for r in rows]


def test_to_candles_drops_what_a_candle_cannot_hold():
    (candle,) = resample.to_candles(minutes(1))
    assert isinstance(candle, Candle)
    assert candle.time == MIDNIGHT and candle.close == 101.0


# -- the duplication that is allowed, on condition it is checked -----------------------


def aggregate(rows: list[dict], span: int) -> list[dict]:
    """A deliberately independent fold, written here so the parity test below is not resample.py
    checking itself. Slow and obvious on purpose."""
    out = []
    start = rows[0]["time"] // span * span
    while start + span <= rows[-1]["time"] + MINUTE:
        group = [r for r in rows if start <= r["time"] < start + span]
        if len(group) == span // MINUTE:
            out.append({
                "time": start, "open": group[0]["open"], "close": group[-1]["close"],
                "high": max(r["high"] for r in group), "low": min(r["low"] for r in group),
                "volume": sum(r["volume"] for r in group),
            })
        start += span
    return out


def test_minute_bars_folded_to_fifteen_match_an_independent_aggregation():
    """The 15-minute series is stored AND foldable from the base. Two paths to one number is a
    drift waiting to happen; this is the condition under which the duplication is allowed."""
    rows = minutes(2 * DAY // MINUTE)
    folded = resample.fold(rows, "1m", "15m")
    plain = aggregate(rows, 15 * MINUTE)
    assert len(folded) == len(plain)
    for a, b in zip(folded, plain, strict=True):
        assert {k: a[k] for k in b} == b
