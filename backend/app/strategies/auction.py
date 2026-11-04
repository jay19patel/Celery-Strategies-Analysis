"""Four strategies built on the auction: yesterday's value, the tape and the option book's gamma.

    value_area_return   80% rule. The day opened outside yesterday's value and two 15m closes put it
                        back inside: the market rejected the new prices, so it travels to the other
                        edge. Not in negative gamma, where hedgers chase and value is not defended.
    sweep_absorption    Stops under a pool were taken (a sweep) and the tape shows the selling was
                        absorbed right there — the passive side won. Long (or the mirror, short).
    exhaustion_fade     A climax at one of the day's levels (value edge, POC, a gamma wall or the
                        flip) that could not hold: fade it back to the point of control.
    flow_breakout       A trend day (negative gamma or an open outside value): a fresh close beyond
                        value with stacked imbalances and positive delta the same way — follow it.

Each one says why it did nothing, sets its stop where its idea stops being true, and refuses a
trade whose reward after the round trip is under `MIN_RR` times the risk. None is known to have an
edge yet: the tape is recorded from the day it was switched on, and these are measured on paper
before anything else is said about them.
"""

from __future__ import annotations

from typing import Any

from app.market.indicators import atr
from app.strategies.base import Context, Decision, Strategy, bracket, net_rr, next_level, none

MIN_RR = 1.5
NEAR_PCT = 0.3  # how close to a level an event counts as "at" it
STOP_BUFFER_ATR = 0.25
FRESH_BARS = 2  # a flow event older than this many bars is history, not a signal


def _atr(ctx: Context) -> float:
    c = ctx.candles
    series = atr([x.high for x in c], [x.low for x in c], [x.close for x in c], 14)
    return series[-1] if series and series[-1] else 0.0


def _trade(ctx: Context, side: int, stop: float, target: float, why: str) -> Decision:
    entry = ctx.candles[-1].close
    if (side > 0 and not stop < entry < target) or (side < 0 and not target < entry < stop):
        return none(f"{why}, but the target is not past the entry")
    rr = net_rr(100 * abs(target - entry) / entry, 100 * abs(entry - stop) / entry, ctx.cost_pct)
    if rr < MIN_RR:
        return none(f"{why}, but {rr:.2f}:1 after costs is under {MIN_RR:g}:1")
    return Decision("buy" if side > 0 else "sell", f"{why} ({rr:.2f}:1 after costs)", entry, round(stop, 6), round(target, 6))


def _fresh(ctx: Context, kind: str) -> list[dict[str, Any]]:
    events = (ctx.flow or {}).get("events") or []
    since = ctx.candles[-FRESH_BARS].time if len(ctx.candles) >= FRESH_BARS else 0
    return [e for e in events if e["kind"] == kind and e["time"] >= since]


def _near(price: float, levels: dict[str, Any]) -> tuple[str, float] | None:
    for name in ("vah", "val", "poc", "call_wall", "put_wall", "flip"):
        at = levels.get(name)
        if isinstance(at, int | float) and at > 0 and abs(price / at - 1) * 100 <= NEAR_PCT:
            return name, float(at)
    return None


class ValueAreaReturn(Strategy):
    name = "value_area_return"
    label = "Value area return (80%)"
    summary = "Opened outside yesterday's value, two 15m closes back inside: trade to the other edge."
    stop_rule = "beyond the two re-entry bars' extreme, plus 0.25 ATR"
    target_rule = "the other edge of yesterday's value area"
    interval = "15m"
    lookback = 60
    wants_bias = True

    def decide(self, ctx: Context) -> Decision:
        b = ctx.bias or {}
        if not b.get("ready"):
            return none(b.get("note") or "no day bias yet")
        if b.get("gamma") == "negative":
            return none("negative gamma: hedgers chase, and value is not defended")
        if len(ctx.candles) < 20:
            return none("not enough bars")
        lv, opened, c = b["levels"], float(b["open"]), ctx.candles
        vah, val = float(lv["vah"]), float(lv["val"])
        back, before = c[-2:], c[-3]
        inside = all(val < x.close < vah for x in back)
        buffer = STOP_BUFFER_ATR * _atr(ctx)
        if opened > vah and inside and before.close >= vah:
            return _trade(ctx, -1, max(x.high for x in back) + buffer, val, f"opened above value ({vah:,.2f}) and was pushed back in")
        if opened < val and inside and before.close <= val:
            return _trade(ctx, 1, min(x.low for x in back) - buffer, vah, f"opened below value ({val:,.2f}) and was bought back in")
        return none("no re-entry into yesterday's value on this bar")


class SweepAbsorption(Strategy):
    name = "sweep_absorption"
    label = "Sweep + absorption"
    summary = "A liquidity pool was swept and the tape shows the aggressors absorbed right there: trade the reversal."
    stop_rule = "past the sweep's extreme, plus 0.25 ATR"
    target_rule = "the next level the market desk sees, else 2x the risk"
    interval = "5m"
    lookback = 120
    wants_flow = True
    wants_structure = True

    def decide(self, ctx: Context) -> Decision:
        if not (ctx.flow or {}).get("ready"):
            return none((ctx.flow or {}).get("note") or "no tape yet")
        absorbed = _fresh(ctx, "absorption")
        if not absorbed:
            return none("no absorption on the last bars")
        sweeps = ((ctx.structure or {}).get("liquidity") or {}).get("sweeps") or []
        recent = [s for s in sweeps if s.get("bar_time", 0) >= ctx.candles[-1].time - 7_200]
        buffer = STOP_BUFFER_ATR * _atr(ctx)
        entry = ctx.candles[-1].close
        for event in reversed(absorbed):
            want = "low" if event["side"] == "bullish" else "high"
            match = next((s for s in recent if s["direction"] == want and abs(s["extreme"] / event["price"] - 1) * 100 <= NEAR_PCT), None)
            if match is None:
                continue
            side = 1 if event["side"] == "bullish" else -1
            stop = (min if side > 0 else max)(match["extreme"], event["price"]) - side * buffer
            level, what = next_level(ctx.structure, side, entry)
            target = level if level else bracket(side, entry, stop, 2.0)
            return _trade(ctx, side, stop, target, f"{want}s swept at {match['level']:,.2f} and {event['detail']}{f'; target {what}' if level else ''}")
        return none("absorption on the tape, but not at a level that was just swept")


class ExhaustionFade(Strategy):
    name = "exhaustion_fade"
    label = "Exhaustion fade"
    summary = "A climax at one of the day's levels that could not hold: fade it back to the point of control."
    stop_rule = "past the climax extreme, plus 0.25 ATR"
    target_rule = "yesterday's point of control, else the tape's own, else 2x the risk"
    interval = "5m"
    lookback = 120
    wants_flow = True
    wants_bias = True

    def decide(self, ctx: Context) -> Decision:
        f, b = ctx.flow or {}, ctx.bias or {}
        if not f.get("ready"):
            return none(f.get("note") or "no tape yet")
        if not b.get("ready"):
            return none(b.get("note") or "no day bias yet")
        exhausted = _fresh(ctx, "exhaustion")
        if not exhausted:
            return none("no exhaustion on the last bars")
        event = exhausted[-1]
        at = _near(event["price"], b["levels"])
        if at is None:
            return none(f"{event['detail']}, but not at one of the day's levels")
        side = 1 if event["side"] == "bullish" else -1
        entry = ctx.candles[-1].close
        stop = event["price"] - side * STOP_BUFFER_ATR * _atr(ctx)
        targets = [b["levels"].get("poc"), (f.get("profile") or {}).get("poc")]
        target = next((float(t) for t in targets if isinstance(t, int | float) and (t - entry) * side > 0), bracket(side, entry, stop, 2.0))
        return _trade(ctx, side, stop, target, f"{event['detail']} at {at[0].replace('_', ' ')} {at[1]:,.2f}")


class FlowBreakout(Strategy):
    name = "flow_breakout"
    label = "Flow breakout"
    summary = "On a trend day, a fresh close beyond yesterday's value with stacked imbalances and delta the same way."
    stop_rule = "back inside value: the broken edge minus 0.25 ATR"
    target_rule = "the gamma wall that way if it pays, else 2x the risk"
    interval = "5m"
    lookback = 120
    wants_flow = True
    wants_bias = True

    def decide(self, ctx: Context) -> Decision:
        f, b = ctx.flow or {}, ctx.bias or {}
        if not f.get("ready"):
            return none(f.get("note") or "no tape yet")
        if not b.get("ready"):
            return none(b.get("note") or "no day bias yet")
        if b.get("style") != "trend":
            return none(f"a {b.get('style')} day: breaks out of value are faded, not followed")
        c, lv = ctx.candles, b["levels"]
        if len(c) < 2:
            return none("not enough bars")
        vah, val = float(lv["vah"]), float(lv["val"])
        buffer = STOP_BUFFER_ATR * _atr(ctx)
        stacked = f.get("stacked") or []
        divergence = (f.get("divergence") or {}).get("side")
        delta = float(f.get("delta") or 0.0)
        if c[-1].close > vah >= c[-2].close:
            if not any(s["side"] == "buy" for s in stacked) or delta <= 0 or divergence == "bearish":
                return none(f"closed over value ({vah:,.2f}) without stacked buying and positive delta behind it")
            wall = lv.get("call_wall")
            stop = vah - buffer
            target = float(wall) if isinstance(wall, int | float) and wall > c[-1].close else bracket(1, c[-1].close, stop, 2.0)
            return _trade(ctx, 1, stop, target, f"broke out over value ({vah:,.2f}) with stacked buying, delta {delta:+,.0f}")
        if c[-1].close < val <= c[-2].close:
            if not any(s["side"] == "sell" for s in stacked) or delta >= 0 or divergence == "bullish":
                return none(f"closed under value ({val:,.2f}) without stacked selling and negative delta behind it")
            wall = lv.get("put_wall")
            stop = val + buffer
            target = float(wall) if isinstance(wall, int | float) and wall < c[-1].close else bracket(-1, c[-1].close, stop, 2.0)
            return _trade(ctx, -1, stop, target, f"broke down under value ({val:,.2f}) with stacked selling, delta {delta:+,.0f}")
        return none("no fresh close beyond yesterday's value")
