from datetime import date

import pytest

from tradebuddy.delta import Candle, OptionQuote
from tradebuddy.strategies import Context, discover
from tradebuddy.strategies.ema_9_15 import Ema9Ema15
from tradebuddy.strategies.ema_cross_15m import EmaCross15m
from tradebuddy.strategies.indicators import atr, ema, rsi
from tradebuddy.strategies.mother_candle import MotherCandle1h, MotherCandle15m, find_mother
from tradebuddy.strategies.pcr_options import PcrOptions
from tradebuddy.strategies.rsi_5m import Rsi5m

from .conftest import FakeExchange, bars


def ctx(candles, market=None, symbol="BTCUSD"):
    return Context(symbol, "15m", candles[-1].time if candles else 0, candles, market or FakeExchange().client(""))


def test_discover_finds_every_strategy_once():
    names = [s.name for s in discover()]
    assert names == sorted(names) == [
        "ema_9_15_15m", "ema_cross_15m", "mother_candle_15m", "mother_candle_1h", "pcr_options", "rsi_5m", "tb_master_15m",
    ]


def test_ema_matches_hand_calculation():
    assert ema([1, 2, 3, 4], 3) == [2.0, 3.0]
    assert ema([1, 2], 3) == []


def test_rsi_extremes():
    assert rsi(list(range(20)), 14)[-1] == 100.0
    assert rsi(list(range(20, 0, -1)), 14)[-1] == 0.0


async def test_ema_cross_up_and_down():
    s = EmaCross15m()
    up = [100.0] * 40 + [90.0] * 10 + [130.0]
    assert (await s.on_candle(ctx(bars(up)))).side == "buy"
    down = [100.0] * 40 + [110.0] * 10 + [70.0]
    assert (await s.on_candle(ctx(bars(down)))).side == "sell"
    assert await s.on_candle(ctx(bars([100.0] * 60))) is None


async def test_rsi_leaves_oversold():
    s = Rsi5m()
    falling = [100.0 - i for i in range(30)]
    assert await s.on_candle(ctx(bars(falling))) is None
    assert (await s.on_candle(ctx(bars([*falling, 90.0])))).side == "buy"


@pytest.mark.parametrize(("puts", "calls", "side"), [(140, 100, "buy"), (60, 100, "sell"), (100, 100, None), (5, 0, None)])
async def test_pcr(puts, calls, side):
    near, far = date(2026, 9, 29), date(2026, 10, 30)
    exchange = FakeExchange()
    market = exchange.client("")
    exchange.chain = [
        OptionQuote("put", 90000, near, puts),
        OptionQuote("call", 100000, near, calls),
        OptionQuote("call", 100000, far, 10_000),  # a later expiry must not count
    ]
    signal = await PcrOptions().on_candle(ctx([], market))
    assert (signal.side if signal else None) == side


def candle(i, o, h, lo, c):
    return Candle(1_700_000_000 + i * 900, o, h, lo, c)


def flat(closes, start=0, wick=0.2):
    return [candle(start + i, x, x + wick, x - wick, x) for i, x in enumerate(closes)]


def test_atr_of_constant_ranges():
    assert atr([2, 2, 2, 2], [0, 0, 0, 0], [1, 1, 1, 1], period=2) == [2.0, 2.0]
    assert atr([1], [0], [1], period=2) == []


async def test_ema_9_15_buys_when_a_pullback_below_ema9_ends_in_an_uptrend():
    bars = [*flat([100 + i for i in range(60)]), *flat([150, 149], start=60), candle(62, 150, 160, 149.5, 158)]
    assert (await Ema9Ema15().on_candle(ctx(bars))).side == "buy"
    assert await Ema9Ema15().on_candle(ctx(bars[:-1])) is None  # still below EMA9


async def test_ema_9_15_sells_when_a_rally_above_ema9_ends_in_a_downtrend():
    bars = [*flat([200 - i for i in range(60)]), *flat([150, 151], start=60), candle(62, 150, 150.5, 130, 132)]
    assert (await Ema9Ema15().on_candle(ctx(bars))).side == "sell"


async def test_ema_9_15_ignores_a_cross_against_the_ema15_trend():
    # Downtrend (EMA9 < EMA15): the high crossing back above EMA9 is not a buy.
    bars = [*flat([200 - i for i in range(60)]), *flat([130, 129], start=60), candle(62, 129, 140, 128.5, 139)]
    assert await Ema9Ema15().on_candle(ctx(bars)) is None


def mother_setup(breakout_close, mother_range=3.0, trend=1):
    """200 trending bars around 1000, a mother, two inside bars, then the bar under test."""
    base = [1000 + trend * 0.5 * i for i in range(196)]
    m, inner = base[-1], min(1.0, mother_range / 2)  # mother centred on 1097.5 (uptrend) or 902.5 (downtrend)
    return [
        *[candle(i, x, x + 0.5, x - 0.5, x) for i, x in enumerate(base)],
        candle(196, m, m + mother_range, m - mother_range, m),
        candle(197, m, m + inner, m - inner, m + inner / 2),
        candle(198, m, m + inner / 2, m - inner / 2, m - inner / 4),
        candle(199, m, max(m, breakout_close) + 0.5, min(m, breakout_close) - 0.5, breakout_close),
    ]


def test_find_mother_takes_the_outermost_mother():
    bars = mother_setup(1200)
    assert find_mother(bars, max_inside=5) == 196
    assert find_mother(bars[:-2], max_inside=5) is None  # no inside bar yet
    assert find_mother(bars, max_inside=1) == 197  # one inside bar allowed: the first inside bar is the mother


async def test_mother_candle_buys_a_close_above_the_mother_with_a_stop_at_its_low():
    s = await MotherCandle1h().on_candle(ctx(mother_setup(1101.0)))  # mother 1094.5 - 1100.5
    assert s.side == "buy"
    assert s.stop_loss_pct == pytest.approx((1101.0 - 1094.5) / 1101.0 * 100, abs=1e-3)
    assert s.take_profit_pct == pytest.approx(2 * s.stop_loss_pct, abs=2e-3)


async def test_mother_candle_sells_a_close_below_the_mother_in_a_downtrend():
    bars = mother_setup(895.0, trend=-1)  # mother 899.5 - 905.5
    assert (await MotherCandle15m().on_candle(ctx(bars))).side == "sell"


@pytest.mark.parametrize(
    ("close", "mother_range", "trend"),
    [
        (1100.0, 3.0, 1),  # closes inside the mother: a wick is not a breakout
        (895.0, 3.0, 1),  # breaks down against the EMA50 uptrend
    ],
)
async def test_mother_candle_filters(close, mother_range, trend):
    assert await MotherCandle1h().on_candle(ctx(mother_setup(close, mother_range, trend))) is None


async def test_mother_candle_needs_a_mother_of_at_least_min_atr():
    class Strict(MotherCandle1h):
        min_mother_atr = 7.0  # the mother's range is 6, ATR about 1

    assert await MotherCandle1h().on_candle(ctx(mother_setup(1101.0))) is not None
    assert await Strict().on_candle(ctx(mother_setup(1101.0))) is None
