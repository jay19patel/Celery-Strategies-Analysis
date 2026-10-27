"""The directional strategies: each signal fires on the event, not on a state, and carries a stop
and target its own logic set — on the right sides of the entry, with the target tied to the risk."""

from __future__ import annotations

import math

from app.market.delta import Candle
from app.market.indicators import supertrend
from app.strategies import REGISTRY
from app.strategies.base import Context
from app.strategies.ema_9_15 import REWARD_MULTIPLE as EMA_REWARD_MULTIPLE
from app.strategies.ema_9_15 import Ema915
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
EMA915 = Ema915()


def test_ema_9_15_buy_requires_previous_high_below_15_ema_and_close_above() -> None:
    bars = [Candle(MIDNIGHT + i * 300, 100.0, 100.0, 100.0, 100.0, 10.0) for i in range(20)]
    bars.append(Candle(MIDNIGHT + 20 * 300, 99.0, 99.5, 98.8, 99.2, 10.0))
    bars.append(Candle(MIDNIGHT + 21 * 300, 99.2, 99.5, 99.1, 99.4, 10.0))
    bars.append(Candle(MIDNIGHT + 22 * 300, 99.4, 100.5, 99.3, 100.4, 10.0))
    d = EMA915.decide(ctx(bars))
    assert d.side == "buy", d.reason
    assert d.stop == bars[-1].low and d.stop < d.entry < d.target
    assert math.isclose((d.target or 0) - (d.entry or 0), EMA_REWARD_MULTIPLE * ((d.entry or 0) - d.stop), rel_tol=1e-3)


def test_ema_9_15_sell_requires_previous_close_above_9_ema_and_close_below() -> None:
    bars = [Candle(MIDNIGHT + i * 300, 100.0, 100.0, 100.0, 100.0, 10.0) for i in range(20)]
    bars.append(Candle(MIDNIGHT + 20 * 300, 101.0, 101.2, 100.8, 100.9, 10.0))
    bars.append(Candle(MIDNIGHT + 21 * 300, 100.9, 101.0, 100.7, 100.8, 10.0))
    bars.append(Candle(MIDNIGHT + 22 * 300, 100.8, 100.9, 99.5, 99.6, 10.0))
    d = EMA915.decide(ctx(bars))
    assert d.side == "sell", d.reason
    assert d.stop == bars[-1].high and d.target < d.entry < d.stop


def test_ema_9_15_does_not_signal_when_price_only_remains_above_ema() -> None:
    bars = series([100.0] * 30 + [101.0, 101.1])
    d = EMA915.decide(ctx(bars))
    assert d.side is None and "no 9/15 EMA close signal" in d.reason


def test_ema_9_15_rejects_a_signal_candle_with_stop_on_wrong_side() -> None:
    bars = [Candle(MIDNIGHT + i * 300, 100.0, 100.0, 100.0, 100.0, 10.0) for i in range(20)]
    bars.append(Candle(MIDNIGHT + 20 * 300, 99.0, 99.5, 98.8, 99.2, 10.0))
    bars.append(Candle(MIDNIGHT + 21 * 300, 99.2, 99.5, 99.1, 99.4, 10.0))
    bars.append(Candle(MIDNIGHT + 22 * 300, 100.4, 100.5, 100.4, 100.4, 10.0))
    d = EMA915.decide(ctx(bars))
    assert d.side is None and "stop beyond the entry" in d.reason


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
    """Five hours of minute readings: the ratio sits at `start`, then moves to `end` inside the bar
    closing at `close_ts` — so the 4h window crosses on that bar and not on the one before it."""
    n = 300
    return [
        {"ts": ts, "pcr_oi": start if ts <= close_ts - 900 else end}
        for ts in (close_ts - (n - i) * 60 for i in range(n))
    ]


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


def test_a_shift_already_past_the_threshold_a_bar_ago_is_not_news() -> None:
    """The crossing is the signal. Without this the same shift re-fires for hours and the strategy
    re-opens the idea after every stop, paying the round trip each time."""
    bars = fifteen([80_000.0 + i for i in range(41)])
    rows = pcr_rows(0.60, 0.60 + MIN_SHIFT + 0.05, bars[-2].time + 900)
    assert PCR.decide(Context("BTCUSD", "BTC", "15m", bars[:-1], options_history=rows)).side == "buy"

    # One bar on, with the ratio holding where it got to: the move is still 4h wide, but it is old.
    held = rows + [{"ts": rows[-1]["ts"] + 60 * i, "pcr_oi": rows[-1]["pcr_oi"]} for i in range(1, 16)]
    later = PCR.decide(Context("BTCUSD", "BTC", "15m", bars, options_history=held))
    assert later.side is None and "already" in later.reason


def test_a_shift_that_crosses_the_other_way_still_fires() -> None:
    """A latch that only lets one side through would miss the turn."""
    bars = fifteen([80_000.0] * 41)
    close = bars[-1].time + 900
    # Rising through the threshold up to the previous bar, then falling back through it on this one.
    up = [{"ts": close - 900 - (300 - i) * 60, "pcr_oi": 0.60 + 0.25 * i / 299} for i in range(300)]
    down = [{"ts": close - 900 + 60 * i, "pcr_oi": 0.85 - 0.60 * i / 15} for i in range(1, 16)]
    d = PCR.decide(Context("BTCUSD", "BTC", "15m", bars, options_history=up + down))
    assert d.side == "sell", d.reason


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
