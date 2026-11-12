"""A Supertrend flip down on the 4h chart below the 200 EMA, held for days.

On 15m the same flip loses (backtested: every walk-forward fold negative on BTC and ETH): its stop
is a few tenths of a percent away, so the round trip alone is a fifth of the risk. On 4h the stop is
a few percent away and a flip is a change in the trend that lasts days. The target is far because a
trend-follower earns on the few trades that run; most end at the stop or the time limit.

Only the short side is traded: the long side lost on BTC and ETH in the stored year. About ten
trades a year per symbol — far too few to prove anything soon, so it runs on paper and
`trading/edge.py` decides what size it has earned.

    SIGNAL  Supertrend(10, 3) flipped down on THIS bar, and price is below the 200 EMA.
    STOP    `stop_atr` x ATR(14) above the close.
    TARGET  `reward_r` times that risk.
    HOLD    up to `max_hold_hours`, then closed at the market.
"""

from __future__ import annotations

from pydantic import Field

from app.market.indicators import atr, ema, supertrend
from app.strategies.base import Context, Decision, Params, Strategy, bracket, min_stop_pct, net_rr, none

PERIOD, MULTIPLIER = 10, 3.0
TREND_EMA = 200
MIN_BARS = 400  # the 200 EMA needs about twice its length to forget where it was seeded
MIN_STOP_PCT = 0.5
MAX_STOP_PCT = 10.0
SIDE = -1


class TrendParams(Params):
    stop_atr: float = Field(2.0, ge=0.5, le=6, description="Stop this many ATRs from the entry")
    reward_r: float = Field(5.0, ge=1, le=15, description="Target this many times the risk")
    max_hold_hours: float = Field(240.0, ge=4, le=720, description="Closed at the market after this long")


class Trend4hShort(Strategy):
    name = "trend_4h_short"
    label = "4h Trend Short"
    summary = f"A Supertrend({PERIOD}, {MULTIPLIER:g}) flip down on 4h with price below the {TREND_EMA} EMA, held for days."
    stop_rule = "stop_atr x ATR(14) above the entry"
    target_rule = "reward_r x the stop distance"
    interval = "4h"
    lookback = 600
    default_on = True
    trails = False  # backtested 2019-2026: the 1R trail gave back most of its edge
    Params = TrendParams

    def decide(self, ctx: Context) -> Decision:
        candles = ctx.candles
        if len(candles) < MIN_BARS:
            return none(f"needs {MIN_BARS} bars of 4h, has {len(candles)}")
        highs, lows, closes = [c.high for c in candles], [c.low for c in candles], [c.close for c in candles]
        st = supertrend(highs, lows, closes, PERIOD, MULTIPLIER)
        (_, direction), (_, before) = st[-1], st[-2]
        if direction != SIDE or before == SIDE:
            return none(f"no flip down on this bar — Supertrend has been {'up' if direction > 0 else 'down'}")
        price = closes[-1]
        trend = ema(closes, TREND_EMA)[-1]
        if (price - trend) * SIDE <= 0:
            return none(f"flipped down but price is on the other side of the {TREND_EMA} EMA ({trend:,.0f})")
        risk = self.p.stop_atr * atr(highs, lows, closes, 14)[-1]
        stop_pct = 100 * risk / price
        floor = min_stop_pct(ctx.cost_pct, MIN_STOP_PCT)
        if stop_pct < floor:
            return none(f"{self.p.stop_atr:g} ATR is only {stop_pct:.2f}%, inside the {floor:.2f}% the costs need")
        if stop_pct > MAX_STOP_PCT:
            return none(f"{self.p.stop_atr:g} ATR is {stop_pct:.2f}%, more risk than {MAX_STOP_PCT:g}%")
        stop = round(price - SIDE * risk, 2)  # rounded first, so the target is exactly its multiple of the risk sent
        target = bracket(SIDE, price, stop, self.p.reward_r)
        rr = net_rr(100 * abs(target - price) / price, stop_pct, ctx.cost_pct)
        return Decision(
            "sell",
            f"4h Supertrend flipped down with price below the {TREND_EMA} EMA {trend:,.0f}; "
            f"stop {self.p.stop_atr:g} ATR ({stop_pct:.2f}%), target {self.p.reward_r:g}R ({rr:.1f}:1 after costs), "
            f"held up to {self.p.max_hold_hours / 24:g} days",
            entry=price, stop=stop, target=round(target, 2),
        )
