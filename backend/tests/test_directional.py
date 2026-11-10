"""The directional strategies: each signal fires on the event, not on a state, and carries a stop
and target its own logic set — on the right sides of the entry, with the target tied to the risk."""

from __future__ import annotations

import math

from app.market.delta import Candle
from app.market.indicators import supertrend
from app.strategies import registry
from app.strategies.base import Context
from app.strategies.ema_vwap import MIN_SESSION_BARS, EmaVwap, session_vwap
from app.strategies.trend_4h_short import MIN_BARS, Trend4hShort

DAY = 86_400
MIDNIGHT = 1_700_006_400  # 00:00 UTC


def bar(t: int, price: float, volume: float = 10.0, span: float = 0.1) -> Candle:
    return Candle(t, price, price + span, price - span, price, volume)


def series(prices: list[float], start: int = MIDNIGHT, step: int = 300) -> list[Candle]:
    return [bar(start + i * step, p) for i, p in enumerate(prices)]


def ctx(candles: list[Candle], **kw: object) -> Context:
    return Context(symbol="BTCUSD", underlying="BTC", interval="5m", candles=candles, **kw)  # type: ignore[arg-type]


def view(short: int = 0) -> dict:
    return {"summary": [{"interval": "15m", "direction": short}]}


def levels(above: float | None = None, below: float | None = None) -> dict:
    return {"nearby": {
        "above": [{"price": above, "label": "3x highs", "confluence": 2}] if above else [],
        "below": [{"price": below, "label": "2x lows", "confluence": 1}] if below else [],
    }}


def crossing_up() -> list[Candle]:
    """A flat session that lifts on the last bar just enough for the 9 EMA to cross VWAP there."""
    return series([100.0] * 34 + [100.15])


# -- EMA x VWAP (ported from the v3 engine's tests) -------------------------------------------------

EV = EmaVwap()


def test_the_vwap_resets_at_midnight_utc() -> None:
    values = session_vwap(series([100.0] * 20) + series([200.0] * 20, start=MIDNIGHT + DAY))
    assert values[19] == 100.0 and values[-1] == 200.0 and values[20] is None


def test_a_session_too_young_to_have_a_vwap_says_so() -> None:
    assert all(v is None for v in session_vwap(series([100.0] * (MIN_SESSION_BARS - 1))))


def test_the_vwap_is_weighted_by_size_not_by_time() -> None:
    candles = [bar(MIDNIGHT + i * 300, 100.0, volume=1.0) for i in range(10)] + [bar(MIDNIGHT + 3000, 200.0, volume=1_000.0)]
    assert session_vwap(candles)[-1] > 190


def test_a_fresh_cross_is_a_signal_with_the_stop_beyond_vwap_and_the_target_at_the_level() -> None:
    candles = crossing_up()
    d = EV.decide(ctx(candles, outlook=view(), structure=levels(above=103.0)))
    assert d.side == "buy" and "crossed above session VWAP" in d.reason
    assert d.stop is not None and d.stop < (session_vwap(candles)[-1] or 0) < (d.entry or 0)
    assert d.target == 103.0


def test_a_cross_that_already_happened_is_not_a_signal() -> None:
    held = series([100.0] * 34 + [100.15, 100.3, 100.45, 100.6])
    d = EV.decide(ctx(held, outlook=view(), structure=levels(above=103.0)))
    assert d.side is None and "is above VWAP" in d.reason


def test_a_cross_against_the_15m_read_is_refused() -> None:
    d = EV.decide(ctx(crossing_up(), outlook=view(short=-1), structure=levels(above=103.0)))
    assert d.side is None and "15m read" in d.reason


def test_chasing_is_refused() -> None:
    d = EV.decide(ctx(series([100.0] * 34 + [101.2]), outlook=view(), structure=levels(above=112.0)))
    assert d.side is None and "chase" in d.reason


def test_a_target_that_cannot_pay_is_refused_and_no_level_is_no_guess() -> None:
    assert EV.decide(ctx(crossing_up(), outlook=view(), structure=levels(above=100.3))).side is None
    d = EV.decide(ctx(crossing_up(), outlook=view(), structure=levels()))
    assert d.side is None and "no level above" in d.reason


def test_ema_vwap_is_symmetrical() -> None:
    down = EV.decide(ctx(series([100.0] * 34 + [99.85]), outlook=view(), structure=levels(below=97.0)))
    assert down.side == "sell" and (down.target or 0) < (down.entry or 0) < (down.stop or 0)


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
