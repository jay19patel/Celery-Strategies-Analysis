"""Liquidity, zones, profiles and estimated flow.

Four modules, one shared obligation: **nothing may change once its bar has closed.** Each of them
looks backwards over history to decide what a level is worth, and each of them would be trivially
profitable and completely worthless if it peeked. The look-ahead tests here are the ones that earn
the rest their place.

The second theme is that no rate is reported without the baseline it has to beat. A sweep that
"works 52% of the time" in a market that rose on 52% of all bars has found nothing, and these
assert that the comparison is always carried alongside.
"""

from __future__ import annotations

import math

import pytest

from app.analysis import liquidity as lq
from app.analysis import orderflow as of
from app.analysis import profile as pf
from app.analysis import zones as zn
from app.market.delta import Candle

MINUTE, HOUR, DAY = 60, 3600, 86_400
MIDNIGHT = 1_700_006_400  # a Thursday, 00:00:00 UTC


def bar(t: int, o: float, h: float, low: float, c: float, v: float = 10.0) -> Candle:
    return Candle(t, o, h, low, c, v)


def walk(n: int, seed: int = 1, start: float = 100.0, step: int = 900, at: int = MIDNIGHT) -> list[Candle]:
    """A deterministic random walk. No real structure, so anything found in it is an artefact —
    which is exactly what the baseline comparisons are for."""
    out, price, state = [], start, seed
    for i in range(n):
        state = (1103515245 * state + 12345) % (2**31)
        move = (state / 2**31 - 0.5) * 2
        o = price
        price = max(1.0, price + move)
        hi, lo = max(o, price) + abs(move) / 2, min(o, price) - abs(move) / 2
        out.append(Candle(at + i * step, o, hi, lo, price, 10 + (state % 20)))
    return out


def atr_of(candles: list[Candle]) -> float:
    spans = [c.high - c.low for c in candles[-14:]]
    return sum(spans) / len(spans)


# =====================================================================================
# liquidity
# =====================================================================================


def test_a_pivot_is_not_known_on_the_bar_it_happened():
    """THE rule. A high is only a pivot once `bars` lower highs have closed after it, so a strategy
    reading the series at bar i may use nothing whose confirmed_index is >= i."""
    candles = walk(300, seed=7)
    found = lq.pivots(candles, bars=3)
    assert found
    assert all(p.confirmed_index == p.index + 3 for p in found)
    assert all(p.confirmed_index > p.index for p in found)


def test_pivots_do_not_change_when_later_bars_arrive():
    candles = walk(400, seed=11)
    cut = 250
    early = lq.pivots(candles[:cut], bars=3)
    late = lq.pivots(candles, bars=3)
    # Everything confirmed before the cut must survive untouched.
    settled = [p for p in early if p.confirmed_index < cut - 3]
    by_key = {(p.kind, p.index): p for p in late}
    for p in settled:
        assert by_key[(p.kind, p.index)] == p


def test_two_equal_highs_both_count_as_pivots():
    """With a strict test on both sides, equal highs cancel each other and neither is marked —
    which would hide precisely the shape a liquidity pool is made of."""
    candles = [
        bar(MIDNIGHT + i * 900, 100, 101, 99, 100) for i in range(3)
    ] + [bar(MIDNIGHT + 3 * 900, 100, 110, 99, 100)] + [
        bar(MIDNIGHT + (4 + i) * 900, 100, 101, 99, 100) for i in range(3)
    ] + [bar(MIDNIGHT + 7 * 900, 100, 110, 99, 100)] + [
        bar(MIDNIGHT + (8 + i) * 900, 100, 101, 99, 100) for i in range(4)
    ]
    highs = [p for p in lq.pivots(candles, bars=3) if p.kind == "high"]
    assert len(highs) == 2
    assert {p.price for p in highs} == {110}


def test_equal_highs_become_one_pool_with_both_touches():
    candles = walk(200, seed=3)
    # Plant two highs at the same price, far enough apart to be separate pivots.
    for i in (60, 100):
        candles[i] = bar(candles[i].time, 100, 200.0, 99, 100)
    found = lq.pools(candles, atr_of(candles))
    pool = next((p for p in found if p.kind == "high" and abs(p.price - 200.0) < 1), None)
    assert pool is not None
    assert pool.touches >= 2


def test_a_sweep_needs_the_close_back_inside():
    """Through the level and back is a raid. Through the level and holding is a break, and this
    module must not call it a sweep."""
    candles = walk(200, seed=5)
    for i in (60, 100):
        candles[i] = bar(candles[i].time, 100, 200.0, 99, 100)
    atr = atr_of(candles)

    raided = list(candles)
    raided[150] = bar(candles[150].time, 150, 220.0, 149, 150)  # pokes through, closes well under
    assert any(s.direction == "high" and s.index == 150 for s in lq.sweeps(raided, atr))

    broke = list(candles)
    broke[150] = bar(candles[150].time, 150, 220.0, 149, 215.0)  # closes beyond: a break
    assert not any(s.direction == "high" and s.index == 150 for s in lq.sweeps(broke, atr))


def test_an_unresolved_sweep_is_not_a_failed_one():
    candles = walk(200, seed=5)
    for i in (60, 100):
        candles[i] = bar(candles[i].time, 100, 200.0, 99, 100)
    candles[-1] = bar(candles[-1].time, 150, 220.0, 149, 150)
    last = [s for s in lq.sweeps(candles, atr_of(candles)) if s.index == len(candles) - 1]
    assert last and last[0].resolved is False
    assert last[0].worked is None and last[0].reaction_pct is None


def test_the_sweep_record_always_carries_its_baseline():
    """A rate with no baseline is not a finding. 51% of sweeps working in a market that rose 51%
    of the time is the market."""
    candles = walk(900, seed=13)
    view = lq.read(candles, atr_of(candles))
    for side in ("high", "low"):
        row = view.record["by_direction"][side]
        assert "baseline" in row and "edge" in row and "enough" in row
        if row["rate"] is not None:
            assert row["baseline"] is not None
            assert row["edge"] == pytest.approx(row["rate"] - row["baseline"], abs=1e-9)


def test_resting_pools_are_listed_before_ones_price_already_took():
    """Sorting by distance alone fills the list with dead levels: the nearest pools are nearly
    always the ones price has just run through."""
    candles = walk(600, seed=17)
    found = lq.pools(candles, atr_of(candles))
    states = [p.swept for p in found]
    assert states == sorted(states)  # False (resting) before True (taken)


def test_week_and_month_boundaries_are_utc():
    """Floor division, no calendar. MIDNIGHT is Wed 15 Nov 2023 00:00 UTC, so its week opened on
    Monday the 13th and its month on the 1st."""
    from datetime import UTC, datetime

    assert lq.day_start(MIDNIGHT) == MIDNIGHT
    assert lq.week_start(MIDNIGHT) == MIDNIGHT - 2 * DAY
    assert lq.month_start(MIDNIGHT) == int(datetime(2023, 11, 1, tzinfo=UTC).timestamp())
    # And it is a Monday for every day of a year, not just this one — the arithmetic has no
    # calendar in it and an off-by-one would be invisible on a single sample.
    for d in range(400):
        at = lq.week_start(MIDNIGHT + d * DAY)
        assert at % DAY == 0
        assert datetime.fromtimestamp(at, tz=UTC).weekday() == 0


def test_sessions_overlap_because_the_market_does():
    assert lq.sessions_at(MIDNIGHT + 3 * HOUR) == ["asia"]
    assert lq.sessions_at(MIDNIGHT + 7 * HOUR + 1800) == ["asia", "london"]
    assert lq.sessions_at(MIDNIGHT + 14 * HOUR) == ["london", "newyork"]
    assert lq.sessions_at(MIDNIGHT + 23 * HOUR) == []


def test_only_finished_periods_become_reference_levels():
    """Today's high is not a level, it is a number that is still moving."""
    candles = walk(3 * DAY // 900, seed=19)
    levels = lq.reference_levels(candles)
    names = {level.name for level in levels}
    assert {"PDH", "PDL", "PDO", "PDC", "DO"} <= names
    last_day = lq.day_start(candles[-1].time)
    assert all(level.at < last_day for level in levels if level.name.startswith("PD"))


def test_an_unfinished_session_is_marked_unfinished():
    candles = walk(30 * 4, seed=23, at=MIDNIGHT)  # 30 hours from midnight: NY of day 2 is open
    rows = lq.session_ranges(candles, days=2)
    assert rows
    assert any(r["complete"] for r in rows)
    assert not rows[-1]["complete"] or rows[-1]["end"] <= candles[-1].time


def test_liquidity_refuses_without_enough_bars():
    assert lq.read(walk(20), 1.0).ready is False
    assert lq.read(walk(200), 0.0).ready is False


# =====================================================================================
# zones
# =====================================================================================


def test_a_zone_state_only_ever_moves_forward():
    z = zn.Zone(kind="fvg", direction="bull", low=100, high=110, created_at=0, timeframe="15m")
    z.advance("mitigated")
    assert z.state == "mitigated"
    z.advance("tested")  # backwards
    assert z.state == "mitigated"
    z.advance("invalidated")
    assert z.state == "invalidated"
    z.advance("fresh")
    assert z.state == "invalidated"


def test_the_candle_that_made_a_zone_cannot_test_it():
    candles = walk(200, seed=29)
    z = zn.Zone(kind="fvg", direction="bull", low=candles[100].low, high=candles[100].high,
                created_at=candles[100].time, timeframe="15m")
    zn.settle(z, candles[:101])
    assert z.touches == 0 and z.state == "fresh"


def test_a_zone_never_regresses_when_more_history_is_added():
    """Settling over a longer window may push a zone further along its lifecycle. It must never
    pull it back — that is the property that makes the record countable."""
    candles = walk(500, seed=31)
    atr = atr_of(candles)
    short = {(z.kind, z.created_at, z.low): z for z in zn.read(candles[:300], atr).zones}
    long = {(z.kind, z.created_at, z.low): z for z in zn.read(candles, atr).zones}
    for key, early in short.items():
        later = long.get(key)
        if later is None:
            continue
        assert zn.STATE_ORDER[later.state] >= zn.STATE_ORDER[early.state]


def test_an_order_block_needs_both_a_break_and_a_big_candle():
    """A break that crept through on an ordinary bar absorbed nothing and leaves no order block."""
    candles = walk(300, seed=37)
    atr = atr_of(candles)
    quiet = zn.order_blocks(candles, atr, displacement_atr=99.0)
    assert quiet == []
    loose = zn.order_blocks(candles, atr, displacement_atr=0.1)
    assert all(z.kind == "ob" and z.direction in ("bull", "bear") for z in loose)


def test_a_gap_under_its_minimum_is_not_a_zone():
    candles = [bar(MIDNIGHT + i * 900, 100, 101, 99, 100) for i in range(10)]
    # A three-bar imbalance a hair wide.
    candles[4] = bar(candles[4].time, 100, 100.2, 99.9, 100.1)
    candles[6] = bar(candles[6].time, 101.05, 101.2, 101.01, 101.1)
    assert zn.fair_value_gaps(candles, atr=10.0, min_atr=0.2) == []


def test_the_zone_record_measures_the_first_touch_against_a_baseline():
    """Asking "did price ever trade through this" answers yes for nearly every zone ever drawn.
    The question worth counting is what happened the first time price came back."""
    candles = walk(900, seed=41)
    view = zn.read(candles, atr_of(candles))
    for kind in ("ob", "fvg", "sr"):
        row = view.record[kind]
        assert {"found", "fresh", "resolved", "rate", "baseline", "edge", "enough"} <= set(row)
        if row["rate"] is not None and row["baseline"] is not None:
            assert row["edge"] == pytest.approx(row["rate"] - row["baseline"], abs=1e-9)


def test_only_live_zones_are_offered_as_the_nearest():
    candles = walk(600, seed=43)
    view = zn.read(candles, atr_of(candles))
    for z in (view.nearest_above, view.nearest_below):
        if z is not None:
            assert z.state in zn.LIVE_STATES


def test_zones_refuse_without_an_atr():
    assert zn.read(walk(200), 0.0).ready is False
    assert zn.read(walk(10), 1.0).ready is False


# =====================================================================================
# profile
# =====================================================================================


def test_the_value_area_always_contains_the_point_of_control():
    for seed in (2, 4, 6, 8):
        p = pf.build(walk(400, seed=seed))
        assert p is not None
        assert p.value_low <= p.poc <= p.value_high
        assert p.value_high >= p.value_low
        assert p.low <= p.poc <= p.high


def test_the_value_area_holds_about_seventy_percent_of_the_volume():
    p = pf.build(walk(500, seed=9))
    assert p is not None
    inside = sum(n.volume for n in p.nodes if n.in_value)
    assert 0.70 <= inside / p.volume <= 0.88  # grows one bin at a time, so it overshoots a little


def test_volume_is_spread_across_a_bars_range_not_dropped_on_its_close():
    """A bar that travelled 2% did not trade all of its size at one price, and pretending it did
    puts the point of control wherever the closes happened to cluster."""
    candles = [bar(MIDNIGHT + i * 900, 100, 120, 100, 101, 100.0) for i in range(10)]
    p = pf.build(candles, bins=20)
    assert p is not None
    touched = [n for n in p.nodes if n.volume > 0]
    assert len(touched) > 10  # spread over the range, not piled on the close


def test_a_flat_distribution_names_no_nodes():
    """A peak has to stand above the profile's own average. Otherwise every profile reports its
    least-flat bin as a finding."""
    candles = [bar(MIDNIGHT + i * 900, 100, 110, 90, 100, 10.0) for i in range(60)]
    p = pf.build(candles, bins=40)
    assert p is not None
    assert p.hvn == [] and p.lvn == []


def test_a_naked_point_of_control_is_one_price_has_not_been_back_to():
    candles = walk(4 * DAY // 900, seed=47)
    days = pf.mark_naked(pf.by_day(candles, days=4), candles)
    for p in days:
        after = [c for c in candles if c.time > p.end]
        revisited = any(c.low <= p.poc <= c.high for c in after)
        assert p.naked == (p.complete and bool(after) and not revisited)


def test_the_newest_window_is_marked_incomplete():
    candles = walk(3 * DAY // 900, seed=53)
    days = pf.by_day(candles, days=3)
    assert days and days[-1].complete is False
    assert all(p.complete for p in days[:-1])


def test_the_tpo_value_area_contains_its_point_of_control():
    t = pf.tpo(walk(DAY // 900, seed=59))
    assert t is not None
    assert t.value_low <= t.poc <= t.value_high
    assert t.ib_low is not None and t.ib_high >= t.ib_low
    assert t.periods > 0
    assert all(r["count"] >= 1 for r in t.rows)
    assert all(r["price"] in t.single_prints for r in t.rows if r["single"])


def test_profile_refuses_without_enough_bars():
    assert pf.read(walk(10)).ready is False
    assert pf.build([]) is None


# =====================================================================================
# order flow
# =====================================================================================


def test_every_flow_number_is_marked_approximate():
    """It is inferred from where a bar closed in its range, not read off a tape. A footprint built
    from candles is not a footprint, and the page has to say so."""
    view = of.read(walk(200, seed=61))
    assert view.approx is True
    assert "tape" in view.approx_note
    assert view.to_dict()["approx"] is True


def test_a_bar_closing_on_its_high_is_all_buying():
    buy, sell = of.bar_delta(bar(MIDNIGHT, 100, 110, 100, 110, 50))
    assert buy == 50 and sell == 0
    buy, sell = of.bar_delta(bar(MIDNIGHT, 110, 110, 100, 100, 50))
    assert buy == 0 and sell == 50


def test_a_bar_with_no_range_splits_evenly_because_it_cannot_say_who_won():
    buy, sell = of.bar_delta(bar(MIDNIGHT, 100, 100, 100, 100, 50))
    assert buy == sell == 25


def test_cumulative_delta_resets_each_utc_day():
    """An unbounded running total drifts with however much has traded since the series began,
    which makes "CVD is rising" true for most of history and useful for none of it."""
    candles = walk(3 * DAY // 900, seed=67)
    flow = of.series(candles, reset_daily=True)
    first_of_day = [f for f in flow if f.time % DAY == 0]
    assert len(first_of_day) >= 2
    for f in first_of_day:
        assert math.isclose(f.cvd, f.delta, rel_tol=1e-9)

    running = of.series(candles, reset_daily=False)
    assert abs(running[-1].cvd) != abs(flow[-1].cvd)


def test_a_divergence_is_dated_to_the_bar_it_was_knowable_on():
    candles = walk(400, seed=71)
    flow = of.series(candles)
    found = of.divergences(candles, flow)
    confirmed = {p.index: p.confirmed_index for p in lq.pivots(candles)}
    for d in found:
        assert d.index in confirmed.values()


def test_the_flow_record_carries_its_baseline():
    candles = walk(900, seed=73)
    view = of.read(candles)
    for kind in ("bullish", "bearish"):
        row = view.record[kind]
        assert {"n", "rate", "baseline", "edge", "enough"} <= set(row)
        if row["rate"] is not None and row["baseline"] is not None:
            assert row["edge"] == pytest.approx(row["rate"] - row["baseline"], abs=1e-9)


def test_flow_refuses_without_enough_bars():
    assert of.read(walk(10)).ready is False


# =====================================================================================
# the obligation all four share
# =====================================================================================


@pytest.mark.parametrize("kind", ["liquidity", "zones", "profile", "flow"])
def test_nothing_changes_once_its_bar_has_closed(kind):
    """Compute to bar t, then compute again over the full history, and compare what each said
    about the bars at or before t. Any difference is the future leaking backwards."""
    candles = walk(700, seed=79)
    atr = atr_of(candles[:400])
    cut, pad = 400, 20  # the last few bars at the cut are legitimately still resolving

    if kind == "liquidity":
        early = {(s.direction, s.bar_time): s.level for s in lq.sweeps(candles[:cut], atr) if s.index < cut - pad}
        late = {(s.direction, s.bar_time): s.level for s in lq.sweeps(candles, atr)}
        assert early and all(late.get(k) == v for k, v in early.items())
    elif kind == "zones":
        early = {(z.kind, z.created_at): (z.low, z.high) for z in zn.read(candles[:cut], atr).zones}
        late = {(z.kind, z.created_at): (z.low, z.high) for z in zn.read(candles, atr).zones}
        shared = set(early) & set(late)
        assert shared and all(early[k] == late[k] for k in shared)
    elif kind == "profile":
        # A finished day's profile is finished: more history cannot move its POC.
        early = {p.label: p.poc for p in pf.by_day(candles[:cut], days=9) if p.complete}
        late = {p.label: p.poc for p in pf.by_day(candles, days=9)}
        shared = set(early) & set(late)
        assert shared and all(early[k] == late[k] for k in shared)
    else:
        early = {b.time: b.delta for b in of.series(candles[:cut])}
        late = {b.time: b.delta for b in of.series(candles)}
        assert early and all(late[t] == d for t, d in early.items())
