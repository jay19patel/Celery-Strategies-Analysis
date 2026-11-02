"""A Supertrend flip on the 4h chart with the 200 EMA as the regime, held for days — one side each.

On 15m the same flip loses: its stop is a few tenths of a percent away, so the round trip alone is
a fifth of the risk, and the 15m chart flips on noise. On 4h the stop is a few percent away, fees
are a rounding error against it, and a flip is a change in the trend that lasts days. The target is
far (REWARD_MULTIPLE times the risk) because a trend-follower earns on the few trades that run; most
end at the stop or the time limit.

Tested on two years of 4h bars, costs 0.07% a side and funding charged on both sides: picked on
BTC+ETH 2024-11..2025-10, checked on BTC+ETH 2025-11..2026-10 and on eight perpetuals the choice
never saw (SOL XRP DOGE BNB LTC ADA AVAX LINK). The short held on all three; the long only on the
eight, and lost on BTC and ETH in both years — which is why it is a separate switch, off by default.

    SIGNAL  Supertrend(10, 3) flipped to the side on THIS bar, and price is on that side of the 200 EMA.
    STOP    STOP_ATR x ATR(14) from the close.
    TARGET  REWARD_MULTIPLE times that risk.
    HOLD    up to MAX_HOLD_HOURS, then closed at the market.
"""

from __future__ import annotations

from typing import ClassVar

from app.market.indicators import atr, ema, supertrend
from app.strategies.base import Context, Decision, Strategy, bracket, min_stop_pct, net_rr, none

PERIOD, MULTIPLIER = 10, 3.0
TREND_EMA = 200
MIN_BARS = 400  # the 200 EMA needs about twice its length to forget where it was seeded
STOP_ATR = 2.0
MIN_STOP_PCT = 0.5
MAX_STOP_PCT = 10.0
REWARD_MULTIPLE = 5.0
MAX_HOLD_HOURS = 240.0


class Trend4h(Strategy):
    side: ClassVar[int]
    summary = f"A Supertrend({PERIOD}, {MULTIPLIER:g}) flip on 4h with price on the same side of the {TREND_EMA} EMA, held up to {MAX_HOLD_HOURS / 24:g} days."
    stop_rule = f"{STOP_ATR:g} ATR from the entry"
    target_rule = f"{REWARD_MULTIPLE:g}× the stop distance"
    interval = "4h"
    lookback = 600
    max_hold_hours = MAX_HOLD_HOURS

    def decide(self, ctx: Context) -> Decision:
        candles = ctx.candles
        if len(candles) < MIN_BARS:
            return none(f"needs {MIN_BARS} bars of 4h, has {len(candles)}")
        highs, lows, closes = [c.high for c in candles], [c.low for c in candles], [c.close for c in candles]
        st = supertrend(highs, lows, closes, PERIOD, MULTIPLIER)
        (_, direction), (_, before) = st[-1], st[-2]
        word = "up" if self.side > 0 else "down"
        if direction != self.side or before == self.side:
            return none(f"no flip {word} on this bar — Supertrend has been {'up' if direction > 0 else 'down'}")
        price = closes[-1]
        trend = ema(closes, TREND_EMA)[-1]
        if (price - trend) * self.side <= 0:
            return none(f"flipped {word} but price is on the other side of the {TREND_EMA} EMA ({trend:,.0f})")
        risk = STOP_ATR * atr(highs, lows, closes, 14)[-1]
        stop_pct = 100 * risk / price
        floor = min_stop_pct(ctx.cost_pct, MIN_STOP_PCT)
        if stop_pct < floor:
            return none(f"{STOP_ATR:g} ATR is only {stop_pct:.2f}%, inside the {floor:.2f}% the costs need")
        if stop_pct > MAX_STOP_PCT:
            return none(f"{STOP_ATR:g} ATR is {stop_pct:.2f}%, more risk than {MAX_STOP_PCT:g}%")
        stop = round(price - self.side * risk, 2)  # rounded first, so the target is exactly its multiple of the risk sent
        target = bracket(self.side, price, stop, REWARD_MULTIPLE)
        rr = net_rr(100 * abs(target - price) / price, stop_pct, ctx.cost_pct)
        return Decision(
            "buy" if self.side > 0 else "sell",
            f"4h Supertrend flipped {word} with price {'above' if self.side > 0 else 'below'} the {TREND_EMA} EMA {trend:,.0f}; "
            f"stop {STOP_ATR:g} ATR ({stop_pct:.2f}%), target {REWARD_MULTIPLE:g}R ({rr:.1f}:1 after costs), held up to {MAX_HOLD_HOURS / 24:g} days",
            entry=price, stop=stop, target=round(target, 2),
        )


class Trend4hShort(Trend4h):
    name = "trend_4h_short"
    label = "4h Trend Short"
    side = -1


class Trend4hLong(Trend4h):
    name = "trend_4h_long"
    label = "4h Trend Long"
    side = 1
