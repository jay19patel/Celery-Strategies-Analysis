"""Flow breakout: on a trend day (negative gamma or an open outside value), a fresh close beyond
yesterday's value with stacked imbalances and delta the same way — follow it."""

from __future__ import annotations

from app.strategies._auction import AuctionParams, stop_buffer, trade
from app.strategies.base import Context, Decision, Strategy, bracket, none


class FlowBreakout(Strategy):
    name = "flow_breakout"
    label = "Flow breakout"
    summary = "On a trend day, a fresh close beyond yesterday's value with stacked imbalances and delta the same way."
    stop_rule = "back inside value: the broken edge minus 0.25 ATR"
    target_rule = "the gamma wall that way if it pays, else 2x the risk"
    interval = "5m"
    lookback = 120
    default_on = True
    wants_flow = True
    wants_bias = True
    Params = AuctionParams

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
        buffer = stop_buffer(ctx)
        stacked = f.get("stacked") or []
        divergence = (f.get("divergence") or {}).get("side")
        delta = float(f.get("delta") or 0.0)
        if c[-1].close > vah >= c[-2].close:
            if not any(s["side"] == "buy" for s in stacked) or delta <= 0 or divergence == "bearish":
                return none(f"closed over value ({vah:,.2f}) without stacked buying and positive delta behind it")
            wall = lv.get("call_wall")
            stop = vah - buffer
            target = float(wall) if isinstance(wall, int | float) and wall > c[-1].close else bracket(1, c[-1].close, stop, 2.0)
            return trade(ctx, 1, stop, target, f"broke out over value ({vah:,.2f}) with stacked buying, delta {delta:+,.0f}", self.p.min_rr)
        if c[-1].close < val <= c[-2].close:
            if not any(s["side"] == "sell" for s in stacked) or delta >= 0 or divergence == "bullish":
                return none(f"closed under value ({val:,.2f}) without stacked selling and negative delta behind it")
            wall = lv.get("put_wall")
            stop = val + buffer
            target = float(wall) if isinstance(wall, int | float) and wall < c[-1].close else bracket(-1, c[-1].close, stop, 2.0)
            return trade(ctx, -1, stop, target, f"broke down under value ({val:,.2f}) with stacked selling, delta {delta:+,.0f}", self.p.min_rr)
        return none("no fresh close beyond yesterday's value")
