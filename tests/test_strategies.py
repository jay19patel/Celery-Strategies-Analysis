from datetime import date

import pytest

from tradebuddy.delta import OptionQuote
from tradebuddy.strategies import Context, discover
from tradebuddy.strategies.ema_cross_15m import EmaCross15m
from tradebuddy.strategies.indicators import ema, rsi
from tradebuddy.strategies.pcr_options import PcrOptions
from tradebuddy.strategies.random_1m import RandomOneMinute
from tradebuddy.strategies.rsi_5m import Rsi5m

from .conftest import FakeExchange, bars


def ctx(candles, market=None, symbol="BTCUSD"):
    return Context(symbol, "15m", candles[-1].time if candles else 0, candles, market or FakeExchange().client(""))


def test_discover_finds_every_strategy_once():
    names = [s.name for s in discover()]
    assert names == sorted(names) == ["ema_cross_15m", "pcr_options", "random_1m", "rsi_5m"]


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


async def test_random_always_picks_a_side():
    assert (await RandomOneMinute().on_candle(ctx([]))).side in ("buy", "sell")
