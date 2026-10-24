"""The directional strategies: each signal fires on the event, not on a state, and carries a stop
and target its own logic set — on the right sides of the entry, with the target tied to the risk."""

from __future__ import annotations

import math

from app.market.delta import Candle
from app.market.indicators import supertrend
from app.strategies import REGISTRY
from app.strategies.base import Context
from app.strategies.ema_vwap import MIN_SESSION_BARS, EmaVwap, session_vwap
from app.strategies.pcr_options import MIN_SHIFT, PcrOptions
from app.strategies.supertrend import REWARD_MULTIPLE, SupertrendTrend

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


# -- put/call shift -------------------------------------------------------------------------------

PCR = PcrOptions()


def pcr_rows(start: float, end: float, close_ts: float) -> list[dict]:
    """Five hours of minute readings, the ratio moving linearly from `start` to `end`."""
    n = 300
    return [{"ts": close_ts - (n - i) * 60, "pcr_oi": start + (end - start) * i / (n - 1)} for i in range(n)]


def fifteen(prices: list[float]) -> list[Candle]:
    return [Candle(MIDNIGHT + i * 900, p, p + 30, p - 30, p, 5.0) for i, p in enumerate(prices)]


def test_a_rising_put_call_ratio_buys_with_the_stop_below_the_signal_candle() -> None:
    bars = fifteen([80_000.0 + i for i in range(40)])
    close = bars[-1].time + 900
    d = PCR.decide(Context("BTCUSD", "BTC", "15m", bars, options_history=pcr_rows(0.60, 0.60 + MIN_SHIFT + 0.05, close)))
    assert d.side == "buy"
    assert d.stop is not None and d.stop < bars[-1].low
    assert math.isclose((d.target or 0) - (d.entry or 0), 2 * ((d.entry or 0) - d.stop), rel_tol=1e-6)


def test_a_falling_ratio_sells_and_a_small_move_is_nothing() -> None:
    bars = fifteen([80_000.0] * 40)
    close = bars[-1].time + 900
    down = PCR.decide(Context("BTCUSD", "BTC", "15m", bars, options_history=pcr_rows(0.9, 0.6, close)))
    assert down.side == "sell" and (down.stop or 0) > bars[-1].high
    flat = PCR.decide(Context("BTCUSD", "BTC", "15m", bars, options_history=pcr_rows(0.60, 0.65, close)))
    assert flat.side is None and "under" in flat.reason


def test_without_enough_recorded_history_it_says_so() -> None:
    bars = fifteen([80_000.0] * 40)
    d = PCR.decide(Context("BTCUSD", "BTC", "15m", bars, options_history=[]))
    assert d.side is None and "recorded" in d.reason
    stale = PCR.decide(Context("BTCUSD", "BTC", "15m", bars, options_history=pcr_rows(0.6, 0.9, bars[-1].time - 7200)))
    assert stale.side is None and "stale" in stale.reason


# -- Supertrend -----------------------------------------------------------------------------------

ST = SupertrendTrend()


def trend_then_flip() -> list[Candle]:
    """A long climb, a pullback deep enough to flip Supertrend down, then a rally — cut at the bar
    where it flips back up, with price still above the 50 EMA."""
    prices = [1000.0 + i * 0.5 for i in range(80)]
    prices += [prices[-1] - i * 1.2 for i in range(1, 9)]
    prices += [prices[-1] + i * 1.5 for i in range(1, 25)]
    bars = [Candle(MIDNIGHT + i * 900, p, p + 0.4, p - 0.4, p, 5.0) for i, p in enumerate(prices)]
    flips = supertrend([c.high for c in bars], [c.low for c in bars], [c.close for c in bars], 10, 3.0)
    offset = len(bars) - len(flips)
    for i in range(1, len(flips)):
        if flips[i][1] == 1 and flips[i - 1][1] == -1:
            return bars[: offset + i + 1]
    raise AssertionError("the fixture never flips up")


def test_a_supertrend_flip_with_the_trend_buys_with_the_stop_on_the_line() -> None:
    bars = trend_then_flip()
    d = ST.decide(Context("BTCUSD", "BTC", "15m", bars))
    assert d.side == "buy", d.reason
    line = supertrend([c.high for c in bars], [c.low for c in bars], [c.close for c in bars], 10, 3.0)[-1][0]
    assert d.stop == round(line, 2) and (d.stop or 0) < (d.entry or 0)
    assert math.isclose((d.target or 0) - (d.entry or 0), REWARD_MULTIPLE * ((d.entry or 0) - (d.stop or 0)), rel_tol=1e-3)


def test_no_flip_is_no_signal() -> None:
    bars = trend_then_flip()
    later = [*bars, Candle(bars[-1].time + 900, bars[-1].close + 1, bars[-1].close + 1.4, bars[-1].close + 0.6, bars[-1].close + 1, 5.0)]
    d = ST.decide(Context("BTCUSD", "BTC", "15m", later))
    assert d.side is None and "no flip" in d.reason


def test_every_strategy_describes_its_own_stop_and_target() -> None:
    for strategy in REGISTRY.values():
        info = strategy.describe()
        assert info["stop_rule"] and info["target_rule"] and info["interval"] in ("5m", "15m")
