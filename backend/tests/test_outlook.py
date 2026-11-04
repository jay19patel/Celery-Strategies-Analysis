"""The directional view: timeframes, distances, counted odds.

The arithmetic here goes in front of someone deciding what to risk, so it is checked against
simulation rather than against itself.
"""

from __future__ import annotations

import math
import random

import pytest

from app.analysis import outlook
from app.analysis import structure as mkt
from app.market.delta import Candle

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


# -- the sources ------------------------------------------------------------------------


def test_every_source_says_what_it_depends_on():
    """A badge that does not explain itself is decoration. Each one has to name its dependency,
    because that is the whole point of separating them."""
    from app.analysis import provenance as prov

    assert [s["key"] for s in prov.legend()] == list(prov.ORDER)
    for s in prov.legend():
        assert s["badge"] and s["colour"].startswith("#")
        assert s["depends_on"].endswith(".") and len(s["detail"]) > 80
