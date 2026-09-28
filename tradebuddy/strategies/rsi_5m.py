"""RSI(14) mean reversion on closed 5-minute candles: buy on a cross back above 30, sell below 70."""

from __future__ import annotations

from tradebuddy.strategies.base import Context, Signal, Strategy
from tradebuddy.strategies.indicators import rsi


class Rsi5m(Strategy):
    name = "rsi_5m"
    interval = "5m"
    symbols = ("BTCUSD", "ETHUSD")
    lookback = 100

    period = 14
    oversold = 30.0
    overbought = 70.0

    async def on_candle(self, ctx: Context) -> Signal | None:
        values = rsi([c.close for c in ctx.candles], self.period)
        if len(values) < 2:
            return None
        prev, now = values[-2], values[-1]
        if prev < self.oversold <= now:
            return Signal("buy", f"RSI {prev:.1f} -> {now:.1f} left oversold")
        if prev > self.overbought >= now:
            return Signal("sell", f"RSI {prev:.1f} -> {now:.1f} left overbought")
        return None
