"""EMA 9 re-entry with an EMA 15 trend filter, on closed 15-minute candles.

    buy:  previous candle's high was below the previous EMA9, this candle's high is above
          this EMA9, and EMA9 > EMA15 (the pullback below EMA9 ends inside an uptrend)
    sell: previous candle's low was above the previous EMA9, this candle's low is below
          this EMA9, and EMA9 < EMA15

The order goes in right after the signal candle closes, i.e. at the next candle's open.
Stop loss and take profit come from Settings.

Backtest on Delta BTCUSD/ETHUSD, Feb - Oct 2026, 5m/15m/1h, fees and slippage 0.07% a side:
the bare rule loses everywhere; the EMA15 filter is what turns it towards break-even, and 15m
is its best timeframe (gross +22% BTC, +3.5% ETH). After costs it is still negative, so run
it on paper. Requiring the close (not only the high) beyond EMA9 made it worse on 15m.
"""

from __future__ import annotations

from tradebuddy.strategies.base import Context, Signal, Strategy
from tradebuddy.strategies.indicators import ema


class Ema9Ema15(Strategy):
    name = "ema_9_15_15m"
    interval = "15m"
    symbols = ("BTCUSD", "ETHUSD")
    lookback = 100

    fast = 9
    trend = 15

    async def on_candle(self, ctx: Context) -> Signal | None:
        bars = ctx.candles
        closes = [c.close for c in bars]
        fast, trend = ema(closes, self.fast), ema(closes, self.trend)
        if len(trend) < 2 or len(fast) < 2:
            return None
        prev, last = bars[-2], bars[-1]
        if prev.high < fast[-2] and last.high > fast[-1] and fast[-1] > trend[-1]:
            return Signal("buy", f"high {last.high:.2f} back above EMA{self.fast} {fast[-1]:.2f} (prev high {prev.high:.2f} < {fast[-2]:.2f}); EMA{self.fast} > EMA{self.trend} {trend[-1]:.2f}")
        if prev.low > fast[-2] and last.low < fast[-1] and fast[-1] < trend[-1]:
            return Signal("sell", f"low {last.low:.2f} back below EMA{self.fast} {fast[-1]:.2f} (prev low {prev.low:.2f} > {fast[-2]:.2f}); EMA{self.fast} < EMA{self.trend} {trend[-1]:.2f}")
        return None
