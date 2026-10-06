"""TB Master (swing and scalp from 1d/1h/15m candles, open interest and options) and is_default_sl_tp."""

from __future__ import annotations

import math
from typing import ClassVar

import pytest

from tradebuddy.delta import Candle
from tradebuddy.runner import evaluate
from tradebuddy.strategies import Context, Signal, Strategy
from tradebuddy.strategies import tb_master as tm

START = 1_699_920_000  # a UTC midnight, so hours and days line up


def m15(closes: list[float], spread: float = 0.3, volumes: list[float] | None = None) -> list[Candle]:
    vols = volumes or [100.0 + i % 3 for i in range(len(closes))]
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(START + i * 900, prev, max(prev, c) + spread, min(prev, c) - spread, c, vols[i]))
        prev = c
    return out


def hours(bars: list[Candle]) -> list[Candle]:
    out = []
    for h in range(len(bars) // 4):
        q = bars[4 * h : 4 * h + 4]
        out.append(Candle(q[0].time, q[0].open, max(c.high for c in q), min(c.low for c in q), q[-1].close, sum(c.volume for c in q)))
    return out


def days(direction: int) -> list[Candle]:
    """120 closed daily bars ending the day before START + 6 days, trending up (1) or down (-1)."""
    out = []
    for n in range(120):
        c = 100 + direction * (n - 119) * 0.5
        out.append(Candle(START + (n - 114) * 86400, c, c + 1, c - 1, c, 1.0))
    return out


def market(b15: list[Candle], daily: list[Candle], oi: list[float], options=None, h1: list[Candle] | None = None) -> tm.Market:
    f15, fh, fd = tm.frame(b15), tm.frame(h1 if h1 is not None else hours(b15)), tm.frame(daily)
    close_at = b15[-1].time + 900
    return tm.Market(f15, len(f15) - 1, fh, tm.last_closed(fh, 3600, close_at), fd, tm.last_closed(fd, 86400, close_at), oi, -0.002, options)


def swing_setup() -> list[Candle]:
    """150 hours oscillating 99-101, then an hour that runs to 104: a close above the 20-hour high."""
    closes = [100 + math.sin(i / 7) for i in range(596)] + [101.5, 102.5, 103.5, 104.0]
    return m15(closes)


def scalp_setup() -> list[Candle]:
    """Wide swings, then a long tightening squeeze around 100, then a 15m bar that breaks out on 10x volume.
    598 bars, so the last one does not close an hour (no swing check)."""
    wide = [100 + 3 * math.sin(i / 5) for i in range(400)]
    tight = [100 + (1.0 - 0.95 * k / 196) * math.sin(k / 3) for k in range(197)]
    closes = [*wide, *tight, 100.8]
    vols = [100.0 + i % 3 for i in range(len(closes) - 1)] + [1000.0]
    return m15(closes, spread=0.05, volumes=vols)


RISING_OI = [1000.0] * 36 + [1005.0, 1010.0, 1015.0, 1020.0]  # +2% in the last hour
FLAT_OI = [1000.0] * 40


# -- swing -----------------------------------------------------------------------------------------


def test_swing_buys_an_hourly_breakout_with_new_open_interest_and_sets_its_own_levels():
    m = market(swing_setup(), days(1), RISING_OI)
    assert m.hour_close
    plan, why = tm.decide(m)
    assert plan is not None and plan.side == "buy" and plan.mode == "swing"
    a = m.h1.atr[m.j]
    assert plan.entry == 104.0
    assert plan.stop == pytest.approx(104.0 - 1.5 * a)
    assert plan.target == pytest.approx(104.0 + 3.0 * a)
    assert why.startswith("SWING") and "open interest +" in why and "SL " in why


@pytest.mark.parametrize(("oi", "daily", "why"), [
    (FLAT_OI, 1, "no new open interest: stops being run, not a new move"),
    (RISING_OI, -1, "the daily trend is down"),
    ([], 1, "no open interest data at all"),
])
def test_swing_needs_open_interest_and_the_daily_bias(oi, daily, why):
    plan, _ = tm.decide(market(swing_setup(), days(daily), oi))
    assert plan is None, why


def test_swing_sells_the_mirror_image():
    closes = [100 - math.sin(i / 7) for i in range(596)] + [98.5, 97.5, 96.5, 96.0]
    plan, _ = tm.decide(market(m15(closes), days(-1), RISING_OI))
    assert plan is not None and plan.side == "sell" and plan.mode == "swing"
    assert plan.stop > plan.entry > plan.target


def test_swing_is_only_decided_when_an_hour_closes():
    b = swing_setup()[:-1]  # 599 bars: the last one closes at :45
    m = market(b, days(1), RISING_OI)
    assert not m.hour_close
    assert tm.swing(m, tm.Params()) is None


# -- scalp -----------------------------------------------------------------------------------------


def test_scalp_buys_a_squeeze_breakout_on_volume_with_rising_open_interest():
    m = market(scalp_setup(), days(1), RISING_OI)
    assert not m.hour_close
    plan, why = tm.decide(m)
    assert plan is not None and plan.mode == "scalp" and plan.side == "buy"
    risk = plan.entry - plan.stop
    a = m.m15.atr[m.i]
    assert 0.8 * a - 1e-9 <= risk <= 2.5 * a + 1e-9
    assert plan.target - plan.entry == pytest.approx(3 * risk)
    assert why.startswith("SCALP [squeeze]")


def test_scalp_needs_volume_and_open_interest():
    b = scalp_setup()
    quiet = [*b[:-1], Candle(b[-1].time, b[-1].open, b[-1].high, b[-1].low, b[-1].close, 101.0)]
    assert tm.decide(market(quiet, days(1), RISING_OI))[0] is None
    assert tm.decide(market(b, days(1), FLAT_OI))[0] is None


# -- costs and options -----------------------------------------------------------------------------


def test_a_trade_that_costs_eat_is_skipped():
    plan, why = tm.decide(market(swing_setup(), days(1), RISING_OI), tm.Params(swing_target_atr=0.2))
    assert plan is None and "after costs" in why


def test_an_options_wall_inside_the_target_caps_it():
    m = market(swing_setup(), days(1), RISING_OI)
    free, _ = tm.decide(m)
    wall = round(free.entry + 0.95 * (free.target - free.entry), 2)  # just short of the target
    m.options = {"pcr_oi": 0.8, "day": {"atm_iv": 0.45, "skew_25d": 0.03, "call_wall": wall, "put_wall": 90.0}}
    capped, why = tm.decide(m)
    assert capped is not None and capped.target < wall < free.target
    assert "call wall" in why and "ATM IV 45%" in why and "put/call 0.80" in why

    m.options["day"]["call_wall"] = round(free.entry + (free.target - free.entry) / 2, 2)  # halfway: too little reward left
    plan, why = tm.decide(m)
    assert plan is None and "after costs" in why


def test_a_wall_right_above_entry_leaves_no_trade():
    m = market(swing_setup(), days(1), RISING_OI)
    m.options = {"day": {"call_wall": 104.05}}
    plan, why = tm.decide(m)
    assert plan is None and "wall" in why


# -- live ------------------------------------------------------------------------------------------


def test_with_hour_builds_the_hour_rest_has_not_published_yet():
    b = swing_setup()
    h1 = hours(b)[:-1]  # REST is a moment behind
    close_at = b[-1].time + 900
    built = tm.with_hour(h1, b, close_at)
    assert built[-1] == hours(b)[-1]
    assert tm.with_hour(hours(b), b, close_at) == hours(b)  # already there: unchanged
    assert tm.with_hour(h1, b, close_at - 900) == h1  # not an hour close


class FakeMarket:
    def __init__(self, series: dict[tuple[str, str], list[Candle]], fail: set[str] = frozenset()):
        self.series, self.fail = series, fail

    def price(self, symbol):
        return 104.0

    async def candles(self, symbol, resolution, count):
        if symbol in self.fail:
            raise RuntimeError("down")
        return self.series[(symbol, resolution)][-count:]

    async def option_chain(self, underlying):
        return []

    async def option_summary(self, underlying):
        return None


def live_market(oi: list[float] | None = RISING_OI) -> tuple[FakeMarket, list[Candle]]:
    b = swing_setup()
    oi_bars = [Candle(b[-len(oi) + n].time, v, v, v, v) for n, v in enumerate(oi)] if oi else []
    fund = [Candle(c.time, 0.01, 0.01, 0.01, 0.01) for c in b[-8:]]
    series = {("BTCUSD", "1h"): hours(b)[:-1], ("BTCUSD", "1d"): days(1), ("OI:BTCUSD", "15m"): oi_bars, ("FUNDING:BTCUSD", "15m"): fund}
    return FakeMarket(series), b


async def test_tb_master_signal_carries_its_own_stop_and_target():
    mk, b = live_market()
    s = await tm.TbMaster().on_candle(Context("BTCUSD", "15m", b[-1].time, b[-tm.TbMaster.lookback :], mk))
    assert s is not None and s.side == "buy"
    assert 0.2 <= s.stop_loss_pct <= 4 and s.take_profit_pct == pytest.approx(2 * s.stop_loss_pct, rel=0.01)
    assert "funding 0.0100%" in s.reason


async def test_tb_master_without_open_interest_does_not_trade():
    mk, b = live_market(None)
    mk.fail = {"OI:BTCUSD"}
    assert await tm.TbMaster().on_candle(Context("BTCUSD", "15m", b[-1].time, b[-500:], mk)) is None


# -- is_default_sl_tp ------------------------------------------------------------------------------


class Levels(Strategy):
    name = "levels"
    interval = "15m"
    symbols = ("BTCUSD",)
    returns: ClassVar[Signal] = Signal("buy", "x", 1.2, 3.4)

    async def on_candle(self, ctx: Context) -> Signal | None:
        return self.returns


class OwnLevels(Levels):
    name = "own_levels"
    is_default_sl_tp = False


JOB = {"symbol": "BTCUSD", "resolution": "15m", "bar_time": START, "version": 1}


async def test_default_levels_ignore_what_the_signal_says():
    e = await evaluate(Levels(), JOB, FakeMarket({}))
    assert e.side == "buy" and e.stop_loss_pct is None and e.take_profit_pct is None  # the Trader uses Settings


async def test_own_levels_pass_through():
    e = await evaluate(OwnLevels(), JOB, FakeMarket({}))
    assert not e.error and (e.stop_loss_pct, e.take_profit_pct) == (1.2, 3.4)


@pytest.mark.parametrize("signal", [Signal("buy", "x"), Signal("buy", "x", 1.0, None), Signal("buy", "x", 0.0, 2.0), Signal("buy", "x", 60.0, 2.0)])
async def test_own_levels_missing_or_out_of_range_is_an_error_not_a_trade(signal, monkeypatch):
    monkeypatch.setattr(OwnLevels, "returns", signal)
    e = await evaluate(OwnLevels(), JOB, FakeMarket({}))
    assert e.error and "is_default_sl_tp is False" in e.error and not e.side


def test_every_strategy_says_where_its_levels_come_from():
    from tradebuddy.strategies import discover

    own = {s.name for s in discover() if not s.is_default_sl_tp}
    assert own == {"mother_candle_15m", "mother_candle_1h", "tb_master_15m"}
