"""The directional view: timeframes, distances, counted odds — and the verdict each card makes.

The arithmetic here goes in front of someone deciding what to risk, so it is checked against
simulation rather than against itself.
"""

from __future__ import annotations

import math
import random
import time

import pytest

from tradebuddy import outlook, verdict
from tradebuddy import structure as mkt
from tradebuddy.api import Api
from tradebuddy.delta import Candle
from tradebuddy.store import Store

from .test_pipeline import make

STEP = 900


def walk(n: int, seed: int = 1, drift: float = 0.0, vol: float = 0.0022, start: float = 80_000.0, step: int = STEP) -> list[Candle]:
    """A random walk of closed bars, with highs and lows around each close."""
    rng = random.Random(seed)
    t0 = 1_700_000_000 // step * step
    price, out = start, []
    for i in range(n):
        o = price
        price = max(1.0, price * (1 + drift + rng.gauss(0, vol)))
        pad = abs(rng.gauss(0, vol / 3))
        out.append(Candle(t0 + i * step, o, max(o, price) * (1 + pad), min(o, price) * (1 - pad), price, abs(rng.gauss(100, 20))))
    return out


# -- the arithmetic ------------------------------------------------------------------


def test_volatility_scales_with_the_square_root_of_time():
    rv = 0.5
    assert outlook.sigma_over(rv, 4) == pytest.approx(2 * outlook.sigma_over(rv, 1))
    assert outlook.sigma_over(rv, 24) == pytest.approx(math.sqrt(24) * outlook.sigma_over(rv, 1))
    # One year of it is the annualised number it started from.
    assert outlook.sigma_over(rv, 365 * 24) == pytest.approx(rv)


def test_the_expected_move_is_smaller_than_one_sigma():
    """E|X| = sigma * sqrt(2/pi) ~ 0.8 sigma. Quoting sigma as "the expected move" overstates it
    by a quarter, which on a 2% day is half a percent of position sizing."""
    assert outlook.expected_abs_move(0.02) == pytest.approx(0.02 * 0.7978845, rel=1e-6)
    assert outlook.expected_abs_move(0.02) < 0.02


def test_touching_one_side_is_not_the_same_as_touching_either():
    """They must never be added: a path can touch both, so the sum runs over 1."""
    sigma = 0.02
    one = outlook.p_touch(0.01, sigma)
    both = outlook.p_touch_either(0.01, sigma)
    assert one < both < 1.0
    assert both < 2 * one  # the overlap is real and is not double-counted
    assert outlook.p_touch(0.0, sigma) == 0.0 and outlook.p_touch(0.01, 0.0) == 0.0


def test_touch_probability_matches_a_simulated_walk():
    """The continuous formula, checked against paths monitored finely enough to approach it.

    A coarse simulation reads lower — a barrier watched once a bar is touched less often than one
    watched continuously. Real bars carry their high and low, which is why the continuous figure
    is the right one here."""
    rng = random.Random(42)
    sigma_t, barrier, steps, runs = 0.02, 0.01, 2000, 4000
    step_vol = sigma_t / math.sqrt(steps)
    touched = 0
    for _ in range(runs):
        x = peak = 0.0
        for _ in range(steps):
            x += rng.gauss(0, step_vol)
            peak = max(peak, x)
        touched += peak >= barrier
    assert outlook.p_touch(barrier, sigma_t) == pytest.approx(touched / runs, abs=0.04)


def test_stop_before_target_depends_only_on_the_ratio():
    """Volatility cancels: a 1:2 is the same two-thirds in a quiet market and a fast one."""
    assert outlook.p_stop_first(1.0, 1.0) == pytest.approx(0.5)
    assert outlook.p_stop_first(1.0, 2.0) == pytest.approx(2 / 3)
    assert outlook.p_stop_first(0.5, 1.0) == pytest.approx(2 / 3)  # same ratio, half the distance
    assert outlook.p_stop_first(1.0, 3.0) == pytest.approx(0.75)
    assert outlook.p_stop_first(0, 1) is None


def test_stop_before_target_matches_a_simulated_walk():
    rng = random.Random(7)
    stop, target, steps, runs = 0.01, 0.02, 4000, 4000
    step_vol = 0.03 / math.sqrt(steps)
    stopped = resolved = 0
    for _ in range(runs):
        x = 0.0
        for _ in range(steps):
            x += rng.gauss(0, step_vol)
            if x <= -stop:
                stopped += 1
                resolved += 1
                break
            if x >= target:
                resolved += 1
                break
    assert outlook.p_stop_first(100 * stop, 100 * target) == pytest.approx(stopped / resolved, abs=0.04)


# -- structure -----------------------------------------------------------------------


def test_resampling_folds_bars_without_inventing_one():
    bars = walk(400, seed=2)
    hourly = mkt.resample(bars, 4)
    assert len(hourly) == 100
    first, source = hourly[0], bars[:4]
    assert (first.open, first.close) == (source[0].open, source[-1].close)
    assert first.high == max(c.high for c in source) and first.low == min(c.low for c in source)
    assert first.volume == pytest.approx(sum(c.volume for c in source))
    # A part-formed group is dropped, not closed early: a partial hour pretending to be an hour is
    # what makes a trend look like it turned.
    assert len(mkt.resample(bars[:-1], 4)) == 99


def test_a_break_fires_on_the_crossing_bar_and_not_the_next_one():
    """The level stays broken for hours. A condition true on every bar is not an event — which is
    the bug pcr_options v1 shipped, 262 signals in 70 hours."""
    flat = [Candle(1_700_000_000 + i * STEP, 100, 100.5, 99.5, 100, 10) for i in range(40)]
    broke = [*flat, Candle(flat[-1].time + STEP, 100, 103, 100, 102, 30)]
    held = [*broke, Candle(broke[-1].time + STEP, 102, 103.5, 101.5, 102.5, 10)]
    assert len(mkt.find_breaks(broke)) == 1
    assert len(mkt.find_breaks(held)) == 1  # still one: it did not break again by staying through
    [b] = mkt.find_breaks(held)
    assert b.direction == "up" and b.kind == "bos" and b.level == pytest.approx(100.5)
    assert b.displacement > 2 and b.volume_ratio > 2  # a big bar on heavy volume, and it says so


def test_a_gap_is_only_a_gap_until_price_returns():
    up = [
        Candle(1_700_000_000, 100, 101, 99, 100, 10),
        Candle(1_700_000_900, 101, 106, 101, 105, 10),
        Candle(1_700_001_800, 105, 107, 103, 106, 10),  # low 103 > bar 0's high 101: a gap
    ]
    [gap] = mkt.find_gaps(up)
    assert gap.direction == "up" and (gap.low, gap.high) == (101, 103)
    back = [*up, Candle(1_700_002_700, 106, 106, 102, 103, 10)]  # trades back into it
    assert mkt.find_gaps(back) == []


# -- the view ------------------------------------------------------------------------


async def test_the_outlook_reads_every_timeframe_and_counts_the_odds(cfg, exchange):
    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", "15m", walk(20_000, seed=3, drift=0.00002))
    view = await Api(system).outlook("BTCUSD")

    assert view["ready"] and view["bars"] == 20_000
    assert [f["interval"] for f in view["timeframes"]] == ["15m", "1h", "1d"]
    assert all(f["trend"] in ("up", "weak up", "flat", "weak down", "down", "unknown") for f in view["timeframes"])
    assert view["alignment"]["verdict"]
    assert [h["hours"] for h in view["horizons"]] == [0.25, 1.0, 24.0]
    # Longer horizon, bigger move — and the bands widen with it.
    moves = [h["expected_move_pct"] for h in view["horizons"]]
    assert moves[0] < moves[1] < moves[2]
    assert view["narrative"]["headline"] and view["narrative"]["plan"]


async def test_a_random_walk_shows_no_edge(cfg, exchange):
    """The point of counting rather than asserting. On a walk with no structure to find, every
    conditional rate must land on its baseline — a machine that reports an edge here would report
    one anywhere."""
    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", "15m", walk(20_000, seed=11))
    view = await Api(system).outlook("BTCUSD")

    for d in view["direction"]:
        if d["conditional"] is not None:
            assert abs(d["edge"]) < 0.06, f"{d['horizon_hours']}h claims an edge on noise: {d['edge']}"
    for p in view["patterns"]["15m"]:
        if p["enough"]:
            assert abs(p["edge"]) < 0.08, f"{p['kind']} {p['direction']} claims an edge on noise: {p['edge']}"


async def test_a_percentage_is_never_shown_without_its_sample(cfg, exchange):
    """Below the threshold it says how many it has, and no number."""
    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", "15m", walk(150, seed=5))  # 150 outcomes at best, under MIN_SAMPLE
    view = await Api(system).outlook("BTCUSD")
    for d in view["direction"]:
        assert d["baseline"] is None and d["conditional"] is None
        assert "needs" in d["note"]
    assert view["patterns"]["15m"] == []


async def test_the_outlook_says_so_when_there_are_no_bars(cfg, exchange):
    system = await make(cfg, exchange)
    view = await Api(system).outlook("BTCUSD")
    assert view["ready"] is False and "backfill" in view["hint"]


async def test_the_outlook_is_recomputed_once_a_bar(cfg, exchange):
    """A scan over a year of bars on every poll would be the most expensive thing on the page."""
    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", "15m", walk(2_000, seed=9))
    api = Api(system)
    first = await api.outlook("BTCUSD")
    assert await api.outlook("BTCUSD") is first  # same object: not recomputed

    system.store.record_bars("BTCUSD", "15m", [Candle(walk(2_001, seed=9)[-1].time, 1, 1, 1, 1, 1)])
    assert await api.outlook("BTCUSD") is not first  # a new bar closed


# -- verdicts ------------------------------------------------------------------------


def test_an_inverted_curve_says_an_event_is_priced():
    v = verdict.term_structure({"term": [{"atm_iv": 0.60, "label": "17 Oct", "hours": 42}, {"atm_iv": 0.35, "label": "25 Dec"}]})
    assert v.stance == "vol_up" and v.measure == "volatility"
    assert "17 Oct" in v.label and v.confidence > 0.5

    normal = verdict.term_structure({"term": [{"atm_iv": 0.28, "label": "16 Oct", "hours": 8}, {"atm_iv": 0.37, "label": "25 Dec"}]})
    assert normal.stance == "vol_down"  # a steep normal curve: nothing priced near term


def test_a_card_with_nothing_to_say_is_not_scored():
    v = verdict.term_structure({"term": []})
    assert v.stance == "unclear" and v.scorable is False and v.measure == ""


def test_the_put_call_card_reads_the_move_not_the_level():
    rows = [{"pcr_oi": 0.60, "ts": 0}, {"pcr_oi": 0.62, "ts": 1}] * 4
    assert verdict.pcr_shift(rows).stance == "neutral"  # a high or low level on its own says nothing
    rising = [{"pcr_oi": 0.60 + 0.05 * i} for i in range(8)]
    assert verdict.pcr_shift(rising).stance == "bullish"
    assert verdict.pcr_shift(rising[::-1]).stance == "bearish"


def test_a_verdict_is_marked_against_what_happened_and_against_saying_nothing():
    direction = {"measure": "direction", "stance": "bullish"}
    assert verdict.settle(direction, ret=0.02, vol=0.5, prior_vol=0.5, sigma=0.01) == (True, True)
    assert verdict.settle(direction, ret=-0.02, vol=0.5, prior_vol=0.5, sigma=0.01) == (False, False)
    bearish = {"measure": "direction", "stance": "bearish"}
    # Right for the right reason, and the null claim ("it went up") was wrong at the same time.
    assert verdict.settle(bearish, ret=-0.02, vol=0.5, prior_vol=0.5, sigma=0.01) == (True, False)

    vol_up = {"measure": "volatility", "stance": "vol_up"}
    assert verdict.settle(vol_up, ret=0.0, vol=0.8, prior_vol=0.4, sigma=0.01) == (True, True)
    assert verdict.settle(vol_up, ret=0.0, vol=0.3, prior_vol=0.4, sigma=0.01) == (False, False)

    # A range card names its own band; the null is the band plain volatility would have drawn.
    walls = {"measure": "range", "stance": "range", "price": 100.0, "numbers": {"put_wall": 98.0, "call_wall": 102.0}}
    assert verdict.settle(walls, ret=0.01, vol=0.5, prior_vol=0.5, sigma=0.005) == (True, False)
    assert verdict.settle(walls, ret=0.05, vol=0.5, prior_vol=0.5, sigma=0.005) == (False, False)


def test_the_scoreboard_reports_the_edge_over_saying_nothing(tmp_path):
    rows = [{"source": "pcr", "stance": "bullish", "measure": "direction", "bar_time": i,
             "correct": int(i % 10 < 7), "baseline_correct": int(i % 10 < 5)} for i in range(100)]
    [card] = verdict.score(rows)["cards"]
    assert card["n"] == 100 and card["hit_rate"] == 0.7 and card["baseline"] == 0.5
    assert card["edge"] == pytest.approx(0.2)
    assert verdict.score([])["cards"] == []


async def test_verdicts_are_stored_when_the_analyst_speaks_and_marked_when_the_horizon_passes(cfg, exchange):
    from tradebuddy.events import MarketAnalysis

    system = await make(cfg, exchange)
    bars = walk(400, seed=13)
    exchange.history = bars
    bar_time = bars[200].time
    system.options_latest["BTCUSD"] = {"summary": {
        "spot": bars[200].close,
        "day": {"atm_iv": 0.6, "call_wall": bars[200].close * 1.01, "put_wall": bars[200].close * 0.99, "hours": 24},
        "nearest": {"max_pain": bars[200].close, "hours": 24},
        "term": [{"atm_iv": 0.60, "label": "near", "hours": 24}, {"atm_iv": 0.35, "label": "far"}],
    }}
    await system.on_analysis(MarketAnalysis(
        symbol="BTCUSD", context={"price": bars[200].close, "lean": {"bullish": 3.0, "bearish": 0.5}},
        insights=[{"key": "iv_rich", "severity": "alert", "bias": "volatility", "title": "rich", "detail": ""}],
        forecast={"bar_time": bar_time, "horizon_hours": 24},
    ))

    written = [dict(r) for r in system.store.db.execute("SELECT * FROM predictions")]
    assert {r["source"] for r in written} >= {"term_structure", "chain_oi", "insights"}
    # Nothing is due yet: a 24-hour claim cannot be marked ten seconds after it was made.
    assert system.store.unsettled_verdicts(now=bar_time + 10) == []
    due = system.store.unsettled_verdicts(now=bar_time + 25 * 3600)
    assert due and all(r["measure"] for r in due)  # a claim about nothing is never queued for marking

    note = await system._score_once(now=bars[-1].time)
    assert "marked" in note
    marked = system.store.verdict_record("BTCUSD")
    assert marked and all(r["correct"] in (0, 1) and r["baseline_correct"] in (0, 1) for r in marked)
    assert verdict.score(marked)["total"] == len(marked)


async def test_the_verdicts_route_gives_the_page_what_it_draws(cfg, exchange):
    """Every field the Market page reads, in one check: a rename here is a silently blank card."""
    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", "15m", walk(3_000, seed=17))
    body = await Api(system).verdicts("BTCUSD")

    assert {"symbol", "days", "verdicts", "record"} <= body.keys()
    assert {v["key"] for v in body["verdicts"]} == {"term_structure", "atm_iv", "pcr", "chain_oi", "insights", "outlook"}
    for v in body["verdicts"]:
        assert {"key", "stance", "label", "detail", "confidence", "horizon_hours", "measure", "scorable"} <= v.keys()
        assert v["stance"] in verdict.STANCES
    assert {"cards", "total"} <= body["record"].keys()


def test_every_stance_has_a_measure_and_a_label():
    """A stance the UI cannot colour, or the scorer cannot mark, is a stance that silently vanishes."""
    for stance in verdict.STANCES:
        assert stance in verdict.STANCE_LABEL
        assert verdict.STANCES[stance] in (*verdict.MEASURES, "")


def test_stored_verdicts_are_pruned(tmp_path):
    from tradebuddy.store import PREDICTION_DAYS

    store = Store(str(tmp_path / "t.db"))
    now = time.time()
    v = {"key": "pcr", "stance": "bullish", "measure": "direction", "label": "x", "confidence": 0.5,
         "horizon_hours": 4.0, "numbers": {}}
    store.record_verdict("BTCUSD", 1, now - (PREDICTION_DAYS + 1) * 86_400, 100.0, v)
    store.record_verdict("BTCUSD", 2, now, 100.0, v)
    assert store.prune_predictions(now) == 1


# -- provenance ----------------------------------------------------------------------


def test_every_source_says_what_it_depends_on():
    """A badge that does not explain itself is decoration. Each one has to name its dependency,
    because that is the whole point of separating them."""
    from tradebuddy import provenance as prov

    assert [s["key"] for s in prov.legend()] == list(prov.ORDER)
    for s in prov.legend():
        assert s["badge"] and s["colour"].startswith("#")
        assert s["depends_on"].endswith(".") and len(s["detail"]) > 80
    # Every card the page draws resolves to one, and the options cards never claim to be counted.
    for card in ("term_structure", "atm_iv", "pcr", "chain_oi"):
        assert prov.of(card)["key"] == prov.IMPLIED
    assert prov.of("forecast")["key"] == prov.MODEL
    assert prov.of("outlook")["key"] == prov.COUNTED
    assert prov.of("something new")["key"] == prov.COUNTED  # falls back without claiming more


async def test_the_view_separates_arithmetic_from_counted_history(cfg, exchange):
    """Which way is counted; how far is arithmetic. One row, two kinds of claim, and the row says
    so — a reader should never have to guess which half rests on a fit."""
    from tradebuddy import provenance as prov

    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", "15m", walk(20_000, seed=21))
    view = await Api(system).outlook("BTCUSD")

    assert [s["key"] for s in view["sources"]] == list(prov.ORDER)
    for row in view["summary"]:
        assert row["direction_source"] == prov.COUNTED
        assert row["move_source"] == prov.MATH
    assert all(h["source"] == prov.MATH for h in view["horizons"])


async def test_a_verdict_carries_the_kind_of_evidence_behind_it(cfg, exchange):
    from tradebuddy import provenance as prov

    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", "15m", walk(3_000, seed=22))
    body = await Api(system).verdicts("BTCUSD")
    by_key = {v["key"]: v for v in body["verdicts"]}
    assert by_key["pcr"]["source"] == prov.IMPLIED
    assert by_key["outlook"]["source"] == prov.COUNTED


async def test_one_reading_per_bar_reaches_the_chart(cfg, exchange):
    """Several cards speak on the same bar; stacking their markers on one candle is a smear, not a
    record. Outlook wins where it spoke, because it is the page's direction."""
    from tradebuddy.api import _one_claim_per_bar

    rows = [
        {"bar_time": 100, "source": "pcr", "stance": "bullish", "confidence": 0.9, "label": "a", "price": 1.0,
         "horizon_hours": 4, "settled_at": None, "correct": None, "actual_return": None},
        {"bar_time": 100, "source": "outlook", "stance": "bearish", "confidence": 0.2, "label": "b", "price": 1.0,
         "horizon_hours": 1, "settled_at": 5.0, "correct": 1, "actual_return": -0.01},
        {"bar_time": 200, "source": "atm_iv", "stance": "vol_up", "confidence": 0.9, "label": "c", "price": 1.0,
         "horizon_hours": 24, "settled_at": None, "correct": None, "actual_return": None},
    ]
    out = _one_claim_per_bar(rows)
    assert [r["bar_time"] for r in out] == [100]  # the volatility claim has no side to draw
    assert out[0]["source"] == "outlook" and out[0]["settled"] is True and out[0]["correct"] is True
    assert "_rank" not in out[0]
