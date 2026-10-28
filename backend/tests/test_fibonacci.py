"""Fibonacci: the leg is found deterministically and the ratios are arithmetic over it."""

from __future__ import annotations

from app.analysis import fibonacci as fib
from app.market.delta import Candle


def ramp(start: float, end: float, bars: int, t0: int = 0) -> list[Candle]:
    step = (end - start) / max(1, bars - 1)
    return [
        Candle(t0 + i * 900, p := start + i * step, p + abs(step) / 2, p - abs(step) / 2, p, 10.0)
        for i in range(bars)
    ]


def up_then_pullback() -> list[Candle]:
    """100 -> 200 over 60 bars, then back to 160 over 12. The impulse is the up leg."""
    rise = ramp(100, 200, 60)
    fall = ramp(200, 160, 12, t0=60 * 900)
    return rise + fall


def test_an_up_leg_is_found_and_measured() -> None:
    out = fib.read(up_then_pullback(), lookback=70)
    assert out.ready and out.leg is not None
    assert out.leg.direction == "up"
    assert out.leg.start_price < 105 and out.leg.end_price > 195


def test_retracements_divide_the_leg_and_extensions_project_past_it() -> None:
    leg = fib.Leg("up", 100.0, 200.0, 0, 100)
    by_label = {v.label: v for v in fib.levels_for(leg)}
    assert by_label["0.5"].price == 150.0  # halfway back into a 100-wide leg
    assert by_label["0.618"].price == 138.2
    assert by_label["1.618"].price == 261.8  # and the projection beyond its end
    assert by_label["0.618"].golden and not by_label["0.382"].golden


def test_a_down_leg_mirrors_exactly() -> None:
    leg = fib.Leg("down", 200.0, 100.0, 0, 100)
    by_label = {v.label: v for v in fib.levels_for(leg)}
    assert by_label["0.5"].price == 150.0
    assert by_label["1.618"].price == 38.2  # projects DOWN, below the leg's end


def test_an_extension_is_only_offered_in_the_legs_own_direction() -> None:
    """An up leg projects up. Reading a downward target off it would be drawing a line to suit
    the trade, which is the one thing this module must not let a caller do."""
    out = fib.read(up_then_pullback(), lookback=70)
    assert out.leg is not None and out.leg.direction == "up"
    assert fib.next_extension(out, 1, 205) is not None
    assert fib.next_extension(out, -1, 205) is None
    assert fib.next_extension(out, 0, 205) is None


def test_the_next_extension_is_the_nearest_one_beyond_price() -> None:
    out = fib.read(up_then_pullback(), lookback=70)
    first = fib.next_extension(out, 1, 205)
    assert first is not None
    beyond = fib.next_extension(out, 1, first.price + 0.01)
    assert beyond is not None and beyond.price > first.price


def test_too_few_bars_and_a_flat_window_report_not_ready_rather_than_guessing() -> None:
    assert not fib.read(ramp(100, 110, 5)).ready
    flat = [Candle(i * 900, 100.0, 100.0, 100.0, 100.0, 1.0) for i in range(80)]
    assert not fib.read(flat).ready
