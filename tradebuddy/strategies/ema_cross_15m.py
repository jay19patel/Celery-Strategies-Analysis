"""EMA 9/21 crossover on closed 15-minute candles, stopped on the Supertrend it is following.

The cross says a trend started; the Supertrend says where that claim stops being true. So the stop
goes at the Supertrend line rather than a fixed percentage: it sits wide in a fast market and tight
in a quiet one, and it is a level the trade can actually be measured against. Where the line is on
the wrong side of the price — which happens on the bar the trend flips — the stop falls back to a
multiple of the ATR, and the trade is skipped only if even that cannot be placed.

The target is `reward` times the stop distance, so the trade is sized and judged in R.
"""

from __future__ import annotations

from tradebuddy.strategies.base import Context, Signal, Strategy
from tradebuddy.strategies.indicators import atr, crossed_above, crossed_below, ema, supertrend
from tradebuddy.strategies.levels import from_stop


class EmaCross15m(Strategy):
    name = "ema_cross_15m"
    version = 2  # the stop moved from Settings' fixed percentage to the Supertrend
    interval = "15m"
    symbols = ("BTCUSD", "ETHUSD")
    lookback = 100
    is_default_sl_tp = False  # it sets its own levels

    fast = 9
    slow = 21
    st_period = 10
    st_multiplier = 3.0
    atr_period = 14
    atr_stop_mult = 2.0  # the fallback stop when the Supertrend is not usable yet
    min_atr_mult = 1.0  # a stop closer than this to the price is noise, not a level
    reward = 2.5

    async def on_candle(self, ctx: Context) -> Signal | None:
        bars = ctx.candles
        closes = [c.close for c in bars]
        fast, slow = ema(closes, self.fast), ema(closes, self.slow)
        if crossed_above(fast, slow):
            side, what = "buy", f"EMA{self.fast} {fast[-1]:.2f} crossed above EMA{self.slow} {slow[-1]:.2f}"
        elif crossed_below(fast, slow):
            side, what = "sell", f"EMA{self.fast} {fast[-1]:.2f} crossed below EMA{self.slow} {slow[-1]:.2f}"
        else:
            return None

        price = closes[-1]
        highs, lows = [c.high for c in bars], [c.low for c in bars]
        band = atr(highs, lows, closes, self.atr_period)
        if not band:
            return None
        atr_pct = 100 * band[-1] / price
        line = supertrend(highs, lows, closes, self.st_period, self.st_multiplier)
        stop, where = self._stop(side, price, line, band[-1])
        levels = from_stop(side, price, stop, self.reward, floor_pct=self.min_atr_mult * atr_pct)
        if levels is None:
            return None
        sl_pct, tp_pct = levels
        return Signal(side, f"{what}; stop {where} {stop:.2f} ({sl_pct:.2f}%), target {tp_pct:.2f}%", sl_pct, tp_pct)

    def _stop(self, side: str, price: float, line: list[tuple[float, int]], band: float) -> tuple[float, str]:
        """The Supertrend line when it is below a long / above a short, else an ATR stop."""
        if line:
            level, direction = line[-1]
            if direction == (1 if side == "buy" else -1) and (level < price if side == "buy" else level > price):
                return level, f"Supertrend({self.st_period},{self.st_multiplier:g})"
        offset = self.atr_stop_mult * band
        return (price - offset, f"{self.atr_stop_mult:g}x ATR") if side == "buy" else (price + offset, f"{self.atr_stop_mult:g}x ATR")
