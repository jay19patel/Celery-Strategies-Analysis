"""Shared by the four auction strategies (value, tape, gamma). Not a strategy (leading "_").

Each one says why it did nothing, sets its stop where its idea stops being true, and refuses a
trade whose stop sits inside the costs or whose reward after the round trip is under `min_rr` times the risk. None is known to have an
edge yet: the tape is recorded from the day it was switched on, and these are measured on paper
before anything else is said about them.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.market.indicators import atr
from app.strategies.base import Context, Decision, Params, min_stop_pct, net_rr, none

NEAR_PCT = 0.3  # how close to a level an event counts as "at" it
STOP_BUFFER_ATR = 0.25
FRESH_BARS = 2  # a flow event older than this many bars is history, not a signal
MIN_STOP_PCT = 0.15  # a stop closer than this, or than 3 round trips, is inside the noise and the fees


class AuctionParams(Params):
    min_rr: float = Field(1.5, ge=1.0, le=5.0, description="Least reward:risk after costs a trade must offer")


def stop_buffer(ctx: Context) -> float:
    c = ctx.candles
    series = atr([x.high for x in c], [x.low for x in c], [x.close for x in c], 14)
    return STOP_BUFFER_ATR * (series[-1] if series and series[-1] else 0.0)


def trade(ctx: Context, side: int, stop: float, target: float, why: str, min_rr: float) -> Decision:
    entry = ctx.candles[-1].close
    if (side > 0 and not stop < entry < target) or (side < 0 and not target < entry < stop):
        return none(f"{why}, but the target is not past the entry")
    stop_pct, floor = 100 * abs(entry - stop) / entry, min_stop_pct(ctx.cost_pct, MIN_STOP_PCT)
    if stop_pct < floor:
        return none(f"{why}, but the stop is only {stop_pct:.2f}% away, inside the {floor:.2f}% the costs need")
    rr = net_rr(100 * abs(target - entry) / entry, 100 * abs(entry - stop) / entry, ctx.cost_pct)
    if rr < min_rr:
        return none(f"{why}, but {rr:.2f}:1 after costs is under {min_rr:g}:1")
    return Decision("buy" if side > 0 else "sell", f"{why} ({rr:.2f}:1 after costs)", entry, round(stop, 6), round(target, 6))


def fresh(ctx: Context, kind: str) -> list[dict[str, Any]]:
    events = (ctx.flow or {}).get("events") or []
    since = ctx.candles[-FRESH_BARS].time if len(ctx.candles) >= FRESH_BARS else 0
    return [e for e in events if e["kind"] == kind and e["time"] >= since]


def near(price: float, levels: dict[str, Any]) -> tuple[str, float] | None:
    for name in ("vah", "val", "poc", "call_wall", "put_wall", "flip"):
        at = levels.get(name)
        if isinstance(at, int | float) and at > 0 and abs(price / at - 1) * 100 <= NEAR_PCT:
            return name, float(at)
    return None
