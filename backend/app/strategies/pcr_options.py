"""A shift in the options book's put/call ratio of open interest, traded on the perpetual (from v2).

The level is not the signal: the ratio sits wherever the book sits for hours, and trading the level
fires on every bar. What carries information is the CHANGE — writers moving from one side of the
book to the other. Rising put/call (puts being written into: support) -> buy; falling -> sell.

Nor is a change that already happened. A shift past `MIN_SHIFT` stays past it for as long as the
reading `LOOKBACK_HOURS` back is still the low one — hours, a dozen bars — so only the bar the shift
CROSSES the threshold is a signal. Without that latch the strategy re-opens the same idea after
every stop and pays the round trip each time, which is how a range grinds an account down.

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
BAR_SECONDS = 900  # the strategy's own 15m bar
STOP_BUFFER_ATR = 0.1
MIN_STOP_PCT = 0.15
MAX_STOP_PCT = 2.0
REWARD_MULTIPLE = 2.0


def _pcr(row: dict[str, Any]) -> float | None:
    value = row.get("pcr_oi")
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) and value > 0 else None


def _window(rows: list[dict[str, Any]], at: float) -> tuple[float, float, float] | None:
    """The put/call ratio `LOOKBACK_HOURS` apart, measured as of the last reading at or before `at`:
    (then, now, the time of that reading). None when the history does not reach back that far.

    Taking the last reading at or before `at` rather than simply the newest one keeps the measurement
    the bar's own — the engine records every minute, so by the time a bar is evaluated there are
    readings after its close, and a backtest must not see them."""
    upto = [r for r in rows if float(r["ts"]) <= at]
    if not upto:
        return None
    last = upto[-1]
    first = next((r for r in reversed(upto) if float(last["ts"]) - float(r["ts"]) >= LOOKBACK_HOURS * 3600), None)
    if first is None:
        return None
    now, then = _pcr(last), _pcr(first)
    return None if now is None or then is None else (then, now, float(last["ts"]))


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
        window = _window(rows, bar.time + BAR_SECONDS)
        if window is None:
            return none(f"needs {LOOKBACK_HOURS:g}h of recorded options history")
        then, now, at = window
        if bar.time + BAR_SECONDS - at > MAX_AGE:
            return none("the latest options reading is stale")
        shift = now - then
        if abs(shift) < MIN_SHIFT:
            return none(f"put/call OI {then:.2f} -> {now:.2f} over {LOOKBACK_HOURS:g}h: a {shift:+.2f} move, under {MIN_SHIFT:g}")

        # The crossing is the signal, not the level. The same measurement one bar ago: already past
        # the threshold the same way, and this bar is not news — it is the bar after the news.
        before = _window(rows, bar.time)
        if before is not None:
            was = before[1] - before[0]
            if abs(was) >= MIN_SHIFT and (was > 0) == (shift > 0):
                return none(f"put/call was already {was:+.2f} on the previous bar — the crossing is the signal, not the level")

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
