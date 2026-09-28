"""Random side every minute. For exercising the pipeline on the demo account only."""

from __future__ import annotations

import random

from tradebuddy.strategies.base import Context, Signal, Strategy


class RandomOneMinute(Strategy):
    name = "random_1m"
    interval = "1m"
    symbols = ("BTCUSD",)

    async def on_candle(self, ctx: Context) -> Signal | None:
        return Signal(random.choice(("buy", "sell")), reason="random pick")  # noqa: S311 - not security
