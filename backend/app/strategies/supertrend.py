"""Supertrend flip on the 15m chart, with the 50 EMA as the trend filter.

Supertrend(10, 3) is an ATR band that only moves towards price while the trend holds and flips
sides when a close crosses it. A flip on THIS bar is the signal — a trend that has been on for hours
is not news. The EMA(50) has to agree (price above it for a long, below for a short): a flip against
the slower trend is usually just a pause in it.

    STOP    the Supertrend line itself — where the band would flip back and the trend is over. It
            widens in a fast market and tightens in a quiet one, so the risk is the market's.
    TARGET  REWARD_MULTIPLE times that risk.
"""

from __future__ import annotations

from app.market.indicators import ema, supertrend
from app.strategies.base import Context, Decision, Strategy, bracket, min_stop_pct, net_rr, none

PERIOD, MULTIPLIER = 10, 3.0
TREND_EMA = 50
MIN_STOP_PCT = 0.2
MAX_STOP_PCT = 2.5
REWARD_MULTIPLE = 2.0


class SupertrendTrend(Strategy):
    name = "supertrend"
    label = "Supertrend"
    summary = f"A Supertrend({PERIOD}, {MULTIPLIER:g}) flip on 15m, with price on the same side of the {TREND_EMA} EMA."
    stop_rule = "on the Supertrend line"
    target_rule = f"{REWARD_MULTIPLE:g}× the stop distance"
    interval = "15m"
    lookback = 200

    def decide(self, ctx: Context) -> Decision:
        candles = ctx.candles
        if len(candles) < TREND_EMA + PERIOD + 2:
            return none(f"needs {TREND_EMA + PERIOD + 2} bars, has {len(candles)}")
        highs, lows, closes = [c.high for c in candles], [c.low for c in candles], [c.close for c in candles]
        st = supertrend(highs, lows, closes, PERIOD, MULTIPLIER)
        if len(st) < 2:
            return none("no Supertrend yet")
        (line, direction), (_, before) = st[-1], st[-2]
        if direction == before:
            return none(f"no flip on this bar — Supertrend has been {'up' if direction > 0 else 'down'}")
        price = closes[-1]
        trend = ema(closes, TREND_EMA)[-1]
        if (price - trend) * direction <= 0:
            return none(f"flipped {'up' if direction > 0 else 'down'} but price is on the other side of the {TREND_EMA} EMA ({trend:,.0f})")
        stop_pct = 100 * abs(price - line) / price
        floor = min_stop_pct(ctx.cost_pct, MIN_STOP_PCT)
        if stop_pct < floor:
            return none(f"the Supertrend line is only {stop_pct:.2f}% away, inside the {floor:.2f}% the costs need")
        if stop_pct > MAX_STOP_PCT:
            return none(f"the Supertrend line is {stop_pct:.2f}% away, more risk than {MAX_STOP_PCT:g}%")
        stop = round(line, 2)  # rounded first, so the target is exactly its multiple of the risk sent
        target = bracket(direction, price, stop, REWARD_MULTIPLE)
        rr = net_rr(100 * abs(target - price) / price, stop_pct, ctx.cost_pct)
        return Decision(
            "buy" if direction > 0 else "sell",
            f"Supertrend flipped {'up' if direction > 0 else 'down'} with price {'above' if direction > 0 else 'below'} "
            f"the {TREND_EMA} EMA; stop on the line {line:,.0f} ({stop_pct:.2f}%), target {REWARD_MULTIPLE:g}R ({rr:.1f}:1 after costs)",
            entry=price, stop=stop, target=round(target, 2),
        )
