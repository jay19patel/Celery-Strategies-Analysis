"""EMA 9/21 crossover on closed 15-minute candles."""

from __future__ import annotations

from tradebuddy.strategies.base import Context, Signal, Strategy
from tradebuddy.strategies.indicators import crossed_above, crossed_below, ema


class EmaCross15m(Strategy):
    name = "ema_cross_15m"
    interval = "15m"
    symbols = ("BTCUSD", "ETHUSD")
    lookback = 100

    fast = 9
    slow = 21

    async def on_candle(self, ctx: Context) -> Signal | None:
        closes = [c.close for c in ctx.candles]
        fast, slow = ema(closes, self.fast), ema(closes, self.slow)
        if crossed_above(fast, slow):
            return Signal("buy", f"EMA{self.fast} {fast[-1]:.2f} crossed above EMA{self.slow} {slow[-1]:.2f}")
        if crossed_below(fast, slow):
            return Signal("sell", f"EMA{self.fast} {fast[-1]:.2f} crossed below EMA{self.slow} {slow[-1]:.2f}")
        return None
