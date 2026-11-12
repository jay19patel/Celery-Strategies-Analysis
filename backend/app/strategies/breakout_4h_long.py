"""A 4h close above the highest high of the last 55 bars, with price above the 200 EMA: buy the
breakout of a market already trending up, and hold it for up to a week.

Backtested in Master Backtester (`strategy_finder/mtf_lab.py`) on Binance 4h bars with this
engine's execution and Delta costs. The variant was picked on BTC+ETH 2019-2022 only, then
checked on 2023-2024, on 2025-2026, and on SOL+XRP, which no choice ever saw:

    BTC+ETH  2019-22 +0.41R a trade (187)   2023-24 +0.39R (97)    2025-26 +0.05R (72)
    SOL+XRP  2019-22 +0.07R                 2023-24 +0.36R         2025-26 +0.04R

The short side of the same rule lost, so this is long only. The guard's 1R trail halved the
result in every period, so it does not trail: the stop stays where it was set.

    SIGNAL  the close clears the highest high of the `channel` bars before it, above the 200 EMA.
    STOP    `stop_atr` x ATR(14) under the close.
    TARGET  `reward_r` times that risk.
    HOLD    up to `max_hold_hours`, then closed at the market.
"""

from __future__ import annotations

from pydantic import Field

from app.market.indicators import atr, ema
from app.strategies.base import Context, Decision, Params, Strategy, bracket, min_stop_pct, net_rr, none

TREND_EMA = 200
MIN_BARS = 400  # the 200 EMA needs about twice its length to forget where it was seeded
MIN_STOP_PCT = 0.5
MAX_STOP_PCT = 10.0


class BreakoutParams(Params):
    channel: int = Field(55, ge=10, le=200, description="Bars whose highest high the close must clear")
    stop_atr: float = Field(2.0, ge=0.5, le=6, description="Stop this many ATRs under the entry")
    reward_r: float = Field(3.0, ge=1, le=10, description="Target this many times the risk")
    max_hold_hours: float = Field(168.0, ge=4, le=720, description="Closed at the market after this long")


class Breakout4hLong(Strategy):
    name = "breakout_4h_long"
    label = "4h Breakout Long"
    summary = f"A 4h close above the last 55 bars' high with price above the {TREND_EMA} EMA, held up to a week."
    stop_rule = "stop_atr x ATR(14) under the entry"
    target_rule = "reward_r x the stop distance"
    interval = "4h"
    lookback = 600
    default_on = True
    trails = False
    Params = BreakoutParams

    def decide(self, ctx: Context) -> Decision:
        c = ctx.candles
        if len(c) < max(MIN_BARS, self.p.channel + 2):
            return none(f"needs {MIN_BARS} bars of 4h, has {len(c)}")
        highs, lows, closes = [x.high for x in c], [x.low for x in c], [x.close for x in c]
        price, ceiling = closes[-1], max(highs[-self.p.channel - 1:-1])
        if price <= ceiling:
            return none(f"no close above the {self.p.channel}-bar high {ceiling:,.2f}")
        trend = ema(closes, TREND_EMA)[-1]
        if price <= trend:
            return none(f"broke the {self.p.channel}-bar high but price is under the {TREND_EMA} EMA ({trend:,.0f})")
        risk = self.p.stop_atr * atr(highs, lows, closes, 14)[-1]
        stop_pct = 100 * risk / price
        floor = min_stop_pct(ctx.cost_pct, MIN_STOP_PCT)
        if stop_pct < floor:
            return none(f"{self.p.stop_atr:g} ATR is only {stop_pct:.2f}%, inside the {floor:.2f}% the costs need")
        if stop_pct > MAX_STOP_PCT:
            return none(f"{self.p.stop_atr:g} ATR is {stop_pct:.2f}%, more risk than {MAX_STOP_PCT:g}%")
        stop = round(price - risk, 2)  # rounded first, so the target is exactly its multiple of the risk sent
        target = bracket(1, price, stop, self.p.reward_r)
        rr = net_rr(100 * (target - price) / price, stop_pct, ctx.cost_pct)
        return Decision(
            "buy",
            f"4h close {price:,.2f} cleared the {self.p.channel}-bar high {ceiling:,.2f} above the {TREND_EMA} EMA {trend:,.0f}; "
            f"stop {self.p.stop_atr:g} ATR ({stop_pct:.2f}%), target {self.p.reward_r:g}R ({rr:.1f}:1 after costs), "
            f"held up to {self.p.max_hold_hours / 24:g} days",
            entry=price, stop=stop, target=round(target, 2),
        )
