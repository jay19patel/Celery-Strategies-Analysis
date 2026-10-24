"""A shift in the options book's put/call ratio of open interest, traded on the perpetual (from v2).

The level is not the signal: the ratio sits wherever the book sits for hours, and trading the level
fires on every bar. What carries information is the CHANGE — writers moving from one side of the
book to the other. Rising put/call (puts being written into: support) -> buy; falling -> sell.

The comparison reads the engine's own stored options history, minute by minute: the exchange keeps
none, so there is no signal until the engine has recorded `LOOKBACK_HOURS` of it, and it says so.

    STOP    beyond the signal candle: below its low for a long, above its high for a short, plus a
            tenth of an ATR. The bar the book moved on is the bar whose range the idea lives inside.
    TARGET  REWARD_MULTIPLE times that risk.
"""

from __future__ import annotations

from typing import Any

from app.market.indicators import atr
from app.strategies.base import Context, Decision, Strategy, bracket, min_stop_pct, net_rr, none

LOOKBACK_HOURS = 4.0
MIN_SHIFT = 0.15
MAX_AGE = 1800.0  # a stored row older than this does not describe the book now
STOP_BUFFER_ATR = 0.1
MIN_STOP_PCT = 0.15
MAX_STOP_PCT = 2.0
REWARD_MULTIPLE = 2.0


def _pcr(row: dict[str, Any]) -> float | None:
    value = row.get("pcr_oi")
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) and value > 0 else None


class PcrOptions(Strategy):
    name = "pcr_options"
    label = "Put/Call shift"
    summary = f"The put/call OI ratio moved {MIN_SHIFT:g}+ in {LOOKBACK_HOURS:g}h: rising buys the perpetual, falling sells it."
    stop_rule = "beyond the signal candle's low (long) or high (short), plus 0.1 ATR"
    target_rule = f"{REWARD_MULTIPLE:g}× the stop distance"
    interval = "15m"
    lookback = 40
    wants_options_history = True

    def decide(self, ctx: Context) -> Decision:
        rows = [r for r in ctx.options_history if _pcr(r)]
        if len(rows) < 2:
            return none("the engine has not recorded enough of the options book yet")
        if not ctx.candles:
            return none("no bars")
        bar = ctx.candles[-1]
        latest = rows[-1]
        if bar.time + 900 - float(latest["ts"]) > MAX_AGE:
            return none("the latest options reading is stale")
        earlier = next((r for r in reversed(rows) if float(latest["ts"]) - float(r["ts"]) >= LOOKBACK_HOURS * 3600), None)
        if earlier is None:
            return none(f"needs {LOOKBACK_HOURS:g}h of recorded options history")
        now, then = _pcr(latest), _pcr(earlier)
        assert now is not None and then is not None
        shift = now - then
        if abs(shift) < MIN_SHIFT:
            return none(f"put/call OI {then:.2f} -> {now:.2f} over {LOOKBACK_HOURS:g}h: a {shift:+.2f} move, under {MIN_SHIFT:g}")

        side = 1 if shift > 0 else -1
        closes = [c.close for c in ctx.candles]
        atr_now = (atr([c.high for c in ctx.candles], [c.low for c in ctx.candles], closes, 14) or [0.0])[-1]
        price = bar.close
        stop = (bar.low - STOP_BUFFER_ATR * atr_now) if side > 0 else (bar.high + STOP_BUFFER_ATR * atr_now)
        stop_pct = 100 * abs(price - stop) / price
        floor = min_stop_pct(ctx.cost_pct, MIN_STOP_PCT)
        if stop_pct < floor:
            stop = price - side * price * floor / 100  # a doji's range is not room to be wrong in
            stop_pct = floor
        if stop_pct > MAX_STOP_PCT:
            return none(f"the signal candle is {stop_pct:.2f}% tall, more risk than {MAX_STOP_PCT:g}%")
        stop = round(stop, 2)
        target = bracket(side, price, stop, REWARD_MULTIPLE)
        rr = net_rr(100 * abs(target - price) / price, stop_pct, ctx.cost_pct)
        return Decision(
            "buy" if side > 0 else "sell",
            f"put/call OI {then:.2f} -> {now:.2f} over {LOOKBACK_HOURS:g}h ({shift:+.2f}): "
            f"{'puts' if side > 0 else 'calls'} being written; stop beyond the signal candle ({stop_pct:.2f}%), "
            f"target {REWARD_MULTIPLE:g}R ({rr:.1f}:1 after costs)",
            entry=price, stop=stop, target=round(target, 2),
        )
