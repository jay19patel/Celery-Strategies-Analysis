"""A shift in the put/call ratio of open interest; trades the perpetual.

The level is not the signal. Put/call OI sits wherever the book happens to sit and stays there for
hours, so "PCR above 1.3 -> buy" fires on every bar until the open-position gate stops it: in one
70-hour run v1 produced 262 signals, 239 of them long, from a market that had not changed, and lost
on all four that became trades. What carries information is the *change* — option writers moving
from one side of the book to the other.

So: compare the ratio at this bar with where it was `lookback_hours` ago and signal only on a move
of at least `min_shift`. Rising PCR means puts are being written into: support -> buy. Falling ->
sell. One signal per shift instead of one a bar.

The comparison reads the engine's own stored series (options_history), not strategy memory: a
strategy runs on whichever Celery worker is free, so anything it remembers between bars is wrong.
The exchange publishes no options history, so this works only from what the engine has recorded —
there is no signal until it has been running for `lookback_hours`, and it says so.
"""

from __future__ import annotations

from typing import Any

from tradebuddy.strategies.base import Context, Signal, Strategy


class PcrOptions(Strategy):
    name = "pcr_options"
    version = 2  # v1 traded the level, which was a signal on almost every bar
    interval = "15m"
    symbols = ("BTCUSD", "ETHUSD")  # each reads its own chain: BTCUSD -> BTC options, ETHUSD -> ETH

    lookback_hours = 4.0
    options_history_hours = 8.0  # the window, with room for gaps in it
    min_shift = 0.15  # the ratio must have moved this much over the window
    max_age_seconds = 1800.0  # a stored row older than this does not describe the book now

    async def on_candle(self, ctx: Context) -> Signal | None:
        rows = [r for r in await ctx.market.option_history(ctx.symbol.removesuffix("USD"), self.options_history_hours) if _pcr(r)]
        if len(rows) < 2:
            return None  # the engine has not recorded enough of the options market yet
        latest = rows[-1]
        if ctx.bar_time - float(latest["ts"]) > self.max_age_seconds:
            return None  # absence over staleness: an old reading is no reading
        earlier = next((r for r in rows if float(latest["ts"]) - float(r["ts"]) >= self.lookback_hours * 3600), None)
        if earlier is None:
            return None
        now, then = _pcr(latest), _pcr(earlier)
        assert now is not None and then is not None  # rows were filtered on it
        shift = now - then
        if abs(shift) < self.min_shift:
            return None
        detail = f"put/call OI {then:.2f} -> {now:.2f} over {self.lookback_hours:g}h ({shift:+.2f})"
        return Signal("buy" if shift > 0 else "sell", detail)


def _pcr(row: dict[str, Any]) -> float | None:
    value = row.get("pcr_oi")
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) and value > 0 else None
