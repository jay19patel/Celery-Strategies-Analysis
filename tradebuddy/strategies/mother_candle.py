"""Mother candle (inside bar) breakout on closed 15-minute and 1-hour candles.

A mother candle is followed by one or more inside bars, each within the mother's high and low.
The pattern trades when a later bar CLOSES outside the mother (a wick through is not a breakout):

    close above mother high, and above EMA50  -> buy
    close below mother low,  and below EMA50  -> sell

Filters, chosen by backtest on Delta BTCUSD/ETHUSD (fees and slippage 0.07% a side):
    - the mother's range is at least 1 x ATR(14): small mothers breaking out are mostly noise
    - EMA50 trend: only break out in the trend's direction
Stop loss at the far side of the mother, take profit at 2R. A stop wider than 4% or tighter than
0.15% is skipped: the first risks too much, the second is eaten by fees.

Backtest (1h, Apr 2025 - Oct 2026): ETHUSD PF 1.33, BTCUSD PF 0.95. 15m loses after fees on both.
"""

from __future__ import annotations

from typing import ClassVar

from tradebuddy.delta import Candle
from tradebuddy.strategies.base import Context, Signal, Strategy
from tradebuddy.strategies.indicators import atr, ema


def find_mother(candles: list[Candle], max_inside: int) -> int | None:
    """Index of the outermost mother whose range holds every bar between it and the last bar.
    The last bar is the breakout candidate; at least one inside bar is required."""
    last = len(candles) - 1
    mother = None
    for m in range(last - 2, max(-1, last - 2 - max_inside), -1):
        hi, lo = candles[m].high, candles[m].low
        if all(c.high <= hi and c.low >= lo for c in candles[m + 1 : last]):
            mother = m
        else:
            break
    return mother


class _MotherCandle(Strategy):
    """Shared logic. Not discovered: the leading underscore marks a base class."""

    symbols = ("BTCUSD", "ETHUSD")
    lookback = 200  # EMA50 and ATR14 need history to settle
    is_default_sl_tp = False  # stop at the far side of the mother, target 2R

    max_inside: ClassVar[int] = 5
    min_mother_atr: ClassVar[float] = 1.0
    trend_period: ClassVar[int] = 50
    reward_risk: ClassVar[float] = 2.0
    min_stop_pct: ClassVar[float] = 0.15
    max_stop_pct: ClassVar[float] = 4.0

    async def on_candle(self, ctx: Context) -> Signal | None:
        bars = ctx.candles
        m = find_mother(bars, self.max_inside)
        if m is None:
            return None
        highs, lows, closes = [c.high for c in bars], [c.low for c in bars], [c.close for c in bars]
        atrs, trend = atr(highs, lows, closes), ema(closes, self.trend_period)
        mother_atr_index = m - (len(bars) - len(atrs))  # atr is aligned to the end of the input
        if not trend or mother_atr_index < 0:
            return None
        mother, last = bars[m], bars[-1]
        if mother.high - mother.low < self.min_mother_atr * atrs[mother_atr_index]:
            return None

        inside = len(bars) - m - 2
        if last.close > mother.high and last.close > trend[-1]:
            side, stop = "buy", mother.low
        elif last.close < mother.low and last.close < trend[-1]:
            side, stop = "sell", mother.high
        else:
            return None
        stop_pct = abs(last.close - stop) / last.close * 100
        if not self.min_stop_pct <= stop_pct <= self.max_stop_pct:
            return None
        edge = "high" if side == "buy" else "low"
        return Signal(
            side,
            f"closed {last.close:.2f} beyond mother {edge} {getattr(mother, edge):.2f} after {inside} inside bar(s); EMA{self.trend_period} {trend[-1]:.2f}",
            stop_loss_pct=round(stop_pct, 3),
            take_profit_pct=round(stop_pct * self.reward_risk, 3),
        )


class MotherCandle15m(_MotherCandle):
    name = "mother_candle_15m"
    interval = "15m"


class MotherCandle1h(_MotherCandle):
    name = "mother_candle_1h"
    interval = "1h"
