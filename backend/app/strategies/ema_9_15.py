"""Asymmetric 9/15 EMA close signals on the 5m chart.

    BUY   previous high below EMA(15), current close above EMA(15).
    SELL  previous close above EMA(9), current close below EMA(9).

Entries are evaluated only after the candle closes. The signal candle sets the stop and the
target is two times that risk away.
"""

from __future__ import annotations

from app.market.indicators import ema
from app.strategies.base import Context, Decision, Strategy, bracket, min_stop_pct, none

FAST_EMA, SLOW_EMA = 9, 15
MIN_STOP_PCT = 0.10
MAX_STOP_PCT = 1.5
REWARD_MULTIPLE = 2.0


class Ema915(Strategy):
    name = "ema_9_15"
    label = "9/15 EMA Close Cross"
    summary = "Buy when the previous high is below EMA(15) and the candle closes above it; sell on a close below EMA(9)."
    stop_rule = "beyond the signal candle"
    target_rule = f"{REWARD_MULTIPLE:g}× the stop distance"
    interval = "5m"
    lookback = 200

    def decide(self, ctx: Context) -> Decision:
        candles = ctx.candles
        if len(candles) < SLOW_EMA + 1:
            return none(f"needs {SLOW_EMA + 1} bars, has {len(candles)}")

        closes = [c.close for c in candles]
        fast, slow = ema(closes, FAST_EMA), ema(closes, SLOW_EMA)
        current = candles[-1]
        price = current.close

        before_slow = slow[-2]
        before_fast = fast[-2]
        buy = candles[-2].high < before_slow and price > slow[-1]
        sell = candles[-2].close > before_fast and price < fast[-1]
        if not buy and not sell:
            return none("no 9/15 EMA close signal on this bar")

        side = 1 if buy else -1
        stop = candles[-1].low if buy else candles[-1].high
        stop_pct = 100 * abs(price - stop) / price
        floor = min_stop_pct(ctx.cost_pct, MIN_STOP_PCT)
        if (buy and stop >= price) or (sell and stop <= price):
            return none("signal candle does not put the stop beyond the entry")
        if stop_pct < floor:
            return none(f"signal candle stop is only {stop_pct:.2f}% away, inside the {floor:.2f}% the costs need")
        if stop_pct > MAX_STOP_PCT:
            return none(f"signal candle stop is {stop_pct:.2f}% away, wider than {MAX_STOP_PCT:g}%")

        target = round(bracket(side, price, stop, REWARD_MULTIPLE), 2)
        return Decision(
            "buy" if buy else "sell",
            f"close crossed {'above EMA(15) after the previous high stayed below it' if buy else 'below EMA(9) after the previous close was above it'}; "
            f"stop beyond signal candle ({stop_pct:.2f}%), target {REWARD_MULTIPLE:g}R",
            entry=price, stop=round(stop, 2), target=target,
        )
