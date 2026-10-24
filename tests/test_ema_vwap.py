"""EMA(9) crossing session VWAP.

The two tests that matter: the cross has to be FRESH — `ema > vwap` stays true for hours after
the event, and a condition true on every bar is not a signal — and the VWAP has to be the
session's, reset at 00:00 UTC. A rolling VWAP drifts with whatever is inside the window and is a
level nobody trades against.
"""

from __future__ import annotations

from tradebuddy.delta import Candle
from tradebuddy.strategies.ema_vwap import (
    MIN_SESSION_BARS,
    EmaVwapCross,
    decide,
    session_vwap,
)

DAY = 86_400
MIDNIGHT = 1_700_006_400  # 00:00 UTC


def bar(t: int, price: float, volume: float = 10.0, span: float = 0.1) -> Candle:
    return Candle(t, price, price + span, price - span, price, volume)


def series(prices: list[float], start: int = MIDNIGHT, step: int = 300) -> list[Candle]:
    return [bar(start + i * step, p) for i, p in enumerate(prices)]


def view(short: int = 0) -> dict:
    return {"summary": [{"interval": "15m", "direction": short}]}


def structure(above: float | None = None, below: float | None = None) -> dict:
    return {"nearby": {
        "above": [{"price": above, "label": "3x highs", "kind_label": "resting stops", "confluence": 2}] if above else [],
        "below": [{"price": below, "label": "2x lows", "kind_label": "resting stops", "confluence": 1}] if below else [],
    }}


def crossing_up() -> list[Candle]:
    """A flat session that lifts on the last bar, just enough for the 9 EMA to cross VWAP there.

    0.15 on a 0.2-wide bar puts price about 0.7 ATR from VWAP — which is what entering AT the
    level looks like. The first version of this fixture jumped 0.9 in one bar, a four-sigma
    candle, and every signal was correctly refused as a chase."""
    return series([100.0] * 34 + [100.15])


# =====================================================================================
# the VWAP
# =====================================================================================


def test_the_vwap_resets_at_midnight_utc():
    """A crypto perpetual has no close, so the UTC day is the only session boundary there is."""
    day_one = series([100.0] * 20, start=MIDNIGHT)
    day_two = series([200.0] * 20, start=MIDNIGHT + DAY)
    values = session_vwap(day_one + day_two)
    assert values[19] == 100.0            # the first session
    assert values[-1] == 200.0            # the second knows nothing about the first
    assert values[20] is None             # and starts again with no value at all


def test_a_session_too_young_to_have_a_vwap_says_so():
    """Two prints and a rounding error is not a level."""
    values = session_vwap(series([100.0] * (MIN_SESSION_BARS - 1)))
    assert all(v is None for v in values)
    assert session_vwap(series([100.0] * MIN_SESSION_BARS))[-1] is not None


def test_the_vwap_is_weighted_by_size_not_by_time():
    """That is the whole reason it is a different line from a moving average: it is the price the
    money paid, not the price the clock saw."""
    candles = [bar(MIDNIGHT + i * 300, 100.0, volume=1.0) for i in range(10)]
    candles.append(bar(MIDNIGHT + 10 * 300, 200.0, volume=1_000.0))
    assert session_vwap(candles)[-1] > 190  # the one huge bar dominates


# =====================================================================================
# the cross
# =====================================================================================


def test_a_fresh_cross_is_a_signal():
    out = decide(crossing_up(), view(), structure(above=103.0))
    assert out.side == "buy"
    assert "crossed above session VWAP" in out.reason


def test_a_cross_that_already_happened_is_not():
    """`ema > vwap` stays true for hours. A condition true on every bar is not an event."""
    held = series([100.0] * 34 + [100.15] + [100.3, 100.45, 100.6, 100.75])
    out = decide(held, view(), structure(above=103.0))
    assert out.side is None
    assert "already above VWAP" in out.reason


def test_a_cross_against_the_higher_timeframe_is_refused():
    out = decide(crossing_up(), view(short=-1), structure(above=103.0))
    assert out.side is None and "gets faded" in out.reason


def test_a_cross_with_no_higher_timeframe_opinion_is_allowed():
    """Zero means the read has nothing to say, not that it disagrees."""
    assert decide(crossing_up(), view(short=0), structure(above=103.0)).side == "buy"


def test_chasing_a_cross_price_has_already_left_is_refused():
    """The idea is entering AT the level. Noticing it when price is an ATR past is a chase."""
    gone = series([100.0] * 34 + [101.2])
    out = decide(gone, view(), structure(above=112.0))
    assert out.side is None and "chase" in out.reason


# =====================================================================================
# the levels
# =====================================================================================


def test_the_stop_is_the_other_side_of_vwap():
    """Through it, the cross was noise and the reason for the trade is gone."""
    candles = crossing_up()
    out = decide(candles, view(), structure(above=103.0))
    assert out.side == "buy"
    price = candles[-1].close
    stop_price = price * (1 - out.stop_loss_pct / 100)
    assert stop_price < session_vwap(candles)[-1]


def test_a_trade_that_cannot_pay_for_its_costs_is_refused():
    out = decide(crossing_up(), view(), structure(above=100.3))
    assert out.side is None
    assert any(w in out.reason for w in ("cannot pay for its costs", "the spread and the fees need", "this wants"))


def test_a_target_inside_one_atr_is_refused_here_too():
    """`scalper` has always had this floor; this module imported its RR constant and not its
    floors, which left the faster strategy the looser one."""
    out = decide(crossing_up(), view(), structure(above=100.3))
    assert out.side is None and "this wants" in out.reason


def test_no_level_to_aim_at_is_a_refusal_not_a_guess():
    out = decide(crossing_up(), view(), structure())
    assert out.side is None and "no level above" in out.reason


def test_it_is_symmetrical():
    up = decide(crossing_up(), view(), structure(above=103.0))
    down = decide(series([100.0] * 34 + [99.85]), view(), structure(below=97.0))
    assert up.side == "buy" and down.side == "sell"


def test_every_refusal_says_why():
    for args in [(view(-1), structure(above=103.0)), (view(), structure())]:
        assert decide(crossing_up(), *args).reason
    assert decide(series([100.0] * 3), view(), structure()).reason


def test_it_is_pure():
    c, v, s = crossing_up(), view(), structure(above=103.0)
    assert decide(c, v, s) == decide(c, v, s)


def test_it_reads_the_engines_own_readings_and_ships_switched_on():
    assert EmaVwapCross.wants_outlook and EmaVwapCross.wants_structure
    assert EmaVwapCross.is_default_sl_tp is False
    assert EmaVwapCross.interval == "5m"
    # It used to ship off so it could not out-race the 15m strategies to the one slot per symbol.
    # `Trader._settle` ranks competing signals now, so speed no longer wins and it runs by default.
    assert EmaVwapCross.default_on is True
