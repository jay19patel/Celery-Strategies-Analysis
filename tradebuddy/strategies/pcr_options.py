"""Put/Call ratio of open interest on the nearest options expiry; trades the perpetual.

A high PCR (put writers dominate) is read as support -> buy; a low PCR -> sell.
"""

from __future__ import annotations

from tradebuddy.strategies.base import Context, Signal, Strategy


class PcrOptions(Strategy):
    name = "pcr_options"
    interval = "15m"
    symbols = ("BTCUSD",)

    bullish_above = 1.3
    bearish_below = 0.7

    async def on_candle(self, ctx: Context) -> Signal | None:
        chain = await ctx.market.option_chain(ctx.symbol.removesuffix("USD"))
        if not chain:
            return None
        expiry = min(q.expiry for q in chain)
        puts = sum(q.oi for q in chain if q.expiry == expiry and q.kind == "put")
        calls = sum(q.oi for q in chain if q.expiry == expiry and q.kind == "call")
        if calls <= 0:
            return None
        pcr = puts / calls
        detail = f"PCR {pcr:.2f} (put OI {puts:g} / call OI {calls:g}, expiry {expiry:%d-%b})"
        if pcr >= self.bullish_above:
            return Signal("buy", detail)
        if pcr <= self.bearish_below:
            return Signal("sell", detail)
        return None
