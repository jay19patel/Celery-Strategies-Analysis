"""Scalper: trades with the trend, off structure, with the stop where the idea dies.

What a person actually does at this timeframe, in the order they do it:

    BIAS        the 15m and 1h reads agree. A scalp against the hour works four times and gives
                it all back on the fifth.
    TRIGGER     the bar closed hard into its own range on more volume than usual — a bar somebody
                took, not one that drifted.
    FLOW        the estimated delta is not pushing the other way. Badged APPROX because it is
                inferred from candles, so it VETOES a trade and never justifies one.
    ROOM        the level price is heading into is far enough away to be worth the trip. Buying
                into the point of control is paying for the last point of a move.
    STOP        under the swing the market actually turned at — or the trigger bar's own low if
                that is closer. Not a fixed percentage: a percentage is a number about the
                account, and the stop is a statement about the market. If price trades there the
                reason for the trade is gone, which is the only thing a stop should mean.
    TARGET      the next level price would have to get through: a pool of resting stops, a zone
                edge, the value-area edge. Refused if that is nearer than the stop — a trade that
                cannot make 1:1 is not worth its costs.

Everything it reads was measured by the engine and is on the page: `ctx.outlook` for the bias,
`ctx.structure` for the levels, the pools, the flow and the profile. It works nothing out for
itself, so the chart and the trade can never disagree.

`decide()` is pure — bars and the two readings in, a Decision out — so a backtest, a Celery worker
and the dashboard all get the same answer, and every refusal carries a reason you can read back
from the event log.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tradebuddy.delta import Candle
from tradebuddy.strategies.base import Context, Signal, Strategy, min_stop_pct, net_rr
from tradebuddy.strategies.indicators import atr

LOOKBACK = 60  # 5 hours of 5m bars: enough for an ATR, a volume average and a swing
VOLUME_WINDOW = 20
MIN_VOLUME_RATIO = 1.15  # the bar carried more than its recent average
PUSH = 0.65  # the close sat this far up (or down) its own range
SWING_BARS = 10  # how far back to look for the low (or high) the move came from
STOP_BUFFER_ATR = 0.15  # beyond the swing, so the wick that made it does not take the trade out
# After the round trip, not before it. The gross ratio was passing trades that could not pay:
# at a 0.14% round trip a 0.12% stop against a 0.12% target reads 1.0 gross and is negative net.
MIN_NET_RR = 1.0
# A stop has to clear the spread and the costs, and stay small enough that this is still a scalp.
# The floor is the wider of this and three round trips (`min_stop_pct`), so it tracks the fee
# instead of being a number that was right when it was typed.
MIN_STOP_PCT = 0.12
MAX_STOP_PCT = 0.80
MIN_ROOM_ATR = 1.0  # the target must be at least this far away, in ATRs
FLOW_VETO = -0.15  # estimated delta this far against the trade refuses it


@dataclass(frozen=True)
class Decision:
    side: str | None  # "buy" | "sell" | None
    reason: str
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None

    def to_signal(self) -> Signal | None:
        if not self.side:
            return None
        return Signal(side=self.side, reason=self.reason,
                      stop_loss_pct=self.stop_loss_pct, take_profit_pct=self.take_profit_pct)


def _way(view: dict[str, Any], interval: str) -> int:
    """+1, -1 or 0 for one timeframe's read, from the engine's own view."""
    for row in view.get("summary") or []:
        if str(row.get("interval")) == interval:
            return int(row.get("direction") or 0)
    for frame in view.get("timeframes") or []:
        if str(frame.get("interval")) == interval:
            return {"bullish": 1, "bearish": -1}.get(str((frame.get("structure") or {}).get("bias")), 0)
    return 0


def swing(candles: list[Candle], side: int, bars: int = SWING_BARS) -> float | None:
    """The low the move came from (or the high, short). Where the idea stops being true.

    The trigger bar is included: on a strong push it IS the swing, and excluding it would put the
    stop further away than the move that justified the trade."""
    window = candles[-bars:]
    if not window:
        return None
    return min(c.low for c in window) if side > 0 else max(c.high for c in window)


def target(structure: dict[str, Any], side: int, price: float) -> tuple[float | None, str]:
    """The next level price has to get through, and what makes it one.

    `nearby` has already merged every source — pools of resting stops, zone edges, value-area
    edges, previous-period levels — and ranked them by distance, so the nearest one the trade is
    heading into is the first honest target. A level more than one source names is better still,
    and that is what `confluence` counts."""
    near = (structure or {}).get("nearby") or {}
    ahead = near.get("above") if side > 0 else near.get("below")
    for level in ahead or []:
        at = level.get("price")
        if isinstance(at, int | float) and ((at > price) if side > 0 else (at < price)):
            agree = level.get("confluence") or 1
            what = str(level.get("label") or level.get("kind_label") or "level")
            return float(at), f"{what}{f' ({agree} sources agree)' if agree > 1 else ''}"
    return None, ""


def flow_against(structure: dict[str, Any], side: int) -> float | None:
    """Estimated delta, signed the way this trade wants it. Negative is against.

    APPROX: inferred from where bars closed in their range, not from a trade tape. It is allowed
    to refuse a trade and never to justify one — an estimate can say "not this one" honestly, and
    cannot carry a decision on its own."""
    flow = (structure or {}).get("flow") or {}
    ratio = flow.get("cvd_move_pct")
    if not isinstance(ratio, int | float) or isinstance(ratio, bool):
        return None
    return float(ratio) * (1 if side > 0 else -1)


def decide(candles: list[Candle], view: dict[str, Any], structure: dict[str, Any] | None = None, cost_pct: float = 0.0) -> Decision:
    """Pure. The whole rule, so a backtest runs exactly what trades."""
    structure = structure or {}
    if len(candles) < VOLUME_WINDOW + 2:
        return Decision(None, f"needs {VOLUME_WINDOW + 2} bars, has {len(candles)}")

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    atr_now = (atr(highs, lows, closes, 14) or [0.0])[-1]
    price = closes[-1]
    if atr_now <= 0 or price <= 0:
        return Decision(None, "no ATR on this timeframe yet")
    atr_pct = 100 * atr_now / price

    short, medium = _way(view, "15m"), _way(view, "1h")
    if short == 0 or short != medium:
        return Decision(None, f"15m and 1h do not agree ({short:+d} vs {medium:+d}) — a scalp against the hour is not worth taking")
    side = short

    bar = candles[-1]
    span = bar.high - bar.low
    if span <= 0:
        return Decision(None, "the bar had no range")
    push = (bar.close - bar.low) / span if side > 0 else (bar.high - bar.close) / span
    if push < PUSH:
        return Decision(None, f"the close sat {push:.0%} of the way {'up' if side > 0 else 'down'} the bar, under the {PUSH:.0%} this needs")

    volumes = [c.volume for c in candles[-VOLUME_WINDOW - 1 : -1]]
    average = sum(volumes) / len(volumes) if volumes else 0.0
    ratio = bar.volume / average if average > 0 else 0.0
    if ratio < MIN_VOLUME_RATIO:
        return Decision(None, f"volume was {ratio:.2f}x its recent average, under the {MIN_VOLUME_RATIO:.2f}x this needs")

    if (pressure := flow_against(structure, side)) is not None and pressure < FLOW_VETO:
        return Decision(None, f"estimated flow is {pressure:+.2f}% against this trade (APPROX, and it only ever refuses)")

    # The stop: where the idea dies.
    turn = swing(candles, side)
    if turn is None:
        return Decision(None, "no swing to put a stop behind")
    buffer_ = STOP_BUFFER_ATR * atr_now
    stop_at = (turn - buffer_) if side > 0 else (turn + buffer_)
    stop_pct = 100 * abs(price - stop_at) / price
    floor = min_stop_pct(cost_pct, MIN_STOP_PCT)
    if stop_pct < floor:
        return Decision(None, f"the swing is only {stop_pct:.2f}% away, inside the {floor:.2f}% the spread and the fees need")
    if stop_pct > MAX_STOP_PCT:
        return Decision(None, f"the swing is {stop_pct:.2f}% away, wider than the {MAX_STOP_PCT:g}% this calls a scalp")

    # The target: the next level, and it has to pay for the stop.
    level, what = target(structure, side, price)
    if level is None:
        return Decision(None, "no level above to aim at" if side > 0 else "no level below to aim at")
    target_pct = 100 * abs(level - price) / price
    if target_pct < MIN_ROOM_ATR * atr_pct:
        return Decision(None, f"the next level is {target_pct:.2f}% away, inside the {MIN_ROOM_ATR:g} ATR ({atr_pct:.2f}%) this wants")
    rr = net_rr(target_pct, stop_pct, cost_pct)
    if rr < MIN_NET_RR:
        return Decision(None, f"{what} is only {rr:.2f}:1 after costs against a {stop_pct:.2f}% stop — under {MIN_NET_RR:g}:1 it cannot pay for its costs")

    return Decision(
        side="buy" if side > 0 else "sell",
        reason=(f"5m close {push:.0%} {'up' if side > 0 else 'down'} its range on {ratio:.2f}x volume, 15m and 1h both "
                f"{'up' if side > 0 else 'down'}; stop behind the {SWING_BARS}-bar {'low' if side > 0 else 'high'} "
                f"({stop_pct:.2f}%), target {what} at {rr:.1f}:1 after costs"),
        stop_loss_pct=round(stop_pct, 3),
        take_profit_pct=round(target_pct, 3),
    )


class Scalper(Strategy):
    name = "scalper"
    version = 2  # levels come from structure now, not from a multiple of ATR
    interval = "5m"
    symbols = ("BTCUSD", "ETHUSD")
    lookback = LOOKBACK
    is_default_sl_tp = False  # its own levels; Settings' percentages are a swing trade here
    wants_outlook = True
    wants_structure = True
    # Only the cap, never the risk. A swing-based stop on a 5m bar is a fraction of a percent, so
    # `trade_margin_pct` would decide the size before `risk_per_trade_pct` got a say; raising the
    # global cap would widen every other strategy's positions. Sizing stays min(by_risk, by_margin).
    margin_pct = 60.0
    # On by default, like every other strategy here. It was held off because it runs on a
    # 5-minute bar and would take the one slot per symbol away from the 15-minute strategies by
    # sheer frequency — but that was the ROUTING being first-come, not this strategy being
    # untrustworthy. Signals are ranked on reward-to-risk after costs now
    # (`Trader._settle`), so being fast no longer wins the slot and there is nothing left to
    # protect the others from. Measured over the stored bars it signals on 1.7-3.6% of bars.
    default_on = True

    async def on_candle(self, ctx: Context) -> Signal | None:
        return decide(ctx.candles, ctx.outlook or {}, ctx.structure or {}, ctx.round_trip).to_signal()
