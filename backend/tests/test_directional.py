"""The directional strategies: each signal fires on the event, not on a state, and carries a stop
and target its own logic set — on the right sides of the entry, with the target tied to the risk."""

from __future__ import annotations

import math

from app.market.delta import Candle
from app.market.indicators import supertrend
from app.strategies import registry
from app.strategies.base import Context
from app.strategies.breakout_4h_long import Breakout4hLong
from app.strategies.trend_4h_short import MIN_BARS, Trend4hShort

DAY = 86_400
MIDNIGHT = 1_700_006_400  # 00:00 UTC


def bar(t: int, price: float, volume: float = 10.0, span: float = 0.1) -> Candle:
    return Candle(t, price, price + span, price - span, price, volume)


def series(prices: list[float], start: int = MIDNIGHT, step: int = 300) -> list[Candle]:
    return [bar(start + i * step, p) for i, p in enumerate(prices)]


def ctx(candles: list[Candle], **kw: object) -> Context:
    return Context(symbol="BTCUSD", underlying="BTC", interval="5m", candles=candles, **kw)  # type: ignore[arg-type]


def test_every_perpetual_strategy_describes_its_own_stop_and_target() -> None:
    for name in registry.names("perp"):
        info = registry.make(name).describe()
        assert info["stop_rule"] and info["target_rule"] and info["interval"] in ("5m", "15m", "4h")


# -- 4h trend ----------------------------------------------------------------------------------------

FOUR_H = 14_400


def wave(drift: float, flip: int) -> list[Candle]:
    """A 4h series drifting `drift` a bar with swings on top, cut on the first bar past MIN_BARS where
    Supertrend flips to `flip`."""
    closes = [200 + drift * i + 4 * math.sin(i / 7) for i in range(1200)]
    bars = [Candle(i * FOUR_H, closes[i - 1] if i else c, max(closes[i - 1] if i else c, c) + 0.3,
                   min(closes[i - 1] if i else c, c) - 0.3, c, 1.0) for i, c in enumerate(closes)]
    st = supertrend([c.high for c in bars], [c.low for c in bars], [c.close for c in bars], 10, 3.0)
    offset = len(bars) - len(st)
    for i in range(MIN_BARS - offset, len(st)):
        if st[i][1] == flip and st[i - 1][1] != flip:
            return bars[: offset + i + 1]
    raise AssertionError("the fixture never flips")


def four_h(candles: list[Candle]) -> Context:
    return Context("BTCUSD", "BTC", "4h", candles, cost_pct=0.12)


def test_a_4h_flip_down_below_the_200_ema_sells_with_a_wide_stop_and_a_far_target() -> None:
    bars = wave(-0.08, -1)
    strategy = Trend4hShort()
    d = strategy.decide(four_h(bars))
    assert d.side == "sell", d.reason
    risk = (d.stop or 0) - (d.entry or 0)
    assert risk > 0
    assert math.isclose((d.entry or 0) - (d.target or 0), strategy.p.reward_r * risk, rel_tol=1e-3)


def test_a_flip_up_is_not_this_strategys_trade() -> None:
    d = Trend4hShort().decide(four_h(wave(0.08, 1)))
    assert d.side is None and "no flip down" in d.reason


def test_a_4h_flip_against_the_200_ema_is_refused() -> None:
    d = Trend4hShort().decide(four_h(wave(0.08, -1)))  # a dip inside an uptrend
    assert d.side is None and "200 EMA" in d.reason


def test_the_4h_trend_waits_for_a_settled_200_ema() -> None:
    d = Trend4hShort().decide(four_h(wave(-0.08, -1)[-(MIN_BARS - 1):]))
    assert d.side is None and f"needs {MIN_BARS}" in d.reason


def test_the_4h_trend_holds_for_days_and_its_params_change_its_trade() -> None:
    assert Trend4hShort().max_hold_hours >= 24 * 5
    bars = wave(-0.08, -1)
    tight, wide = Trend4hShort({"reward_r": 2.0}).decide(four_h(bars)), Trend4hShort().decide(four_h(bars))
    assert tight.side == wide.side == "sell" and (tight.target or 0) > (wide.target or 0)  # a nearer target below a short
    assert Trend4hShort({"max_hold_hours": 48}).max_hold_hours == 48


# -- 4h breakout long ----------------------------------------------------------------------------------


def test_a_4h_close_over_the_55_bar_high_above_the_200_ema_buys_with_an_atr_stop() -> None:
    closes = [200 + 0.08 * i + 4 * math.sin(i / 7) for i in range(700)] + [270.0]  # an uptrend, then a breakout
    bars = [Candle(i * FOUR_H, c, c + 0.8, c - 0.8, c, 1.0) for i, c in enumerate(closes)]
    s = Breakout4hLong()
    d = s.decide(four_h(bars))
    assert d.side == "buy", d.reason
    risk = (d.entry or 0) - (d.stop or 0)
    assert risk > 0 and math.isclose((d.target or 0) - (d.entry or 0), s.p.reward_r * risk, rel_tol=1e-3)
    inside = [*bars[:-1], Candle(700 * FOUR_H, 250.0, 250.8, 249.2, 250.0, 1.0)]
    assert s.decide(four_h(inside)).side is None  # under the channel high
    down = [Candle(i * FOUR_H, c, c + 0.8, c - 0.8, c, 1.0) for i, c in enumerate(400 - 0.3 * i for i in range(700))]
    spike = [*down, Candle(700 * FOUR_H, 300.0, 300.8, 299.2, 300.0, 1.0)]  # over the 55-bar high, under the 200 EMA
    assert "200 EMA" in s.decide(four_h(spike)).reason
