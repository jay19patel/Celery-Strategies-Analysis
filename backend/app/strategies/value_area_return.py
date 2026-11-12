"""Value area return (the "80% rule"): the day opened outside yesterday's value and two 15m closes put
it back inside — the market rejected the new prices, so it travels to the other edge. Not in negative
gamma, where hedgers chase and value is not defended."""

from __future__ import annotations

from app.strategies._auction import AuctionParams, stop_buffer, trade
from app.strategies.base import Context, Decision, Strategy, none


class ValueAreaReturn(Strategy):
    name = "value_area_return"
    version = 2  # v2: a stop inside the costs is refused
    label = "Value area return (80%)"
    summary = "Opened outside yesterday's value, two 15m closes back inside: trade to the other edge."
    stop_rule = "beyond the two re-entry bars' extreme, plus 0.25 ATR"
    target_rule = "the other edge of yesterday's value area"
    interval = "15m"
    lookback = 60
    default_on = True
    wants_bias = True
    Params = AuctionParams

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
        buffer = stop_buffer(ctx)
        if opened > vah and inside and before.close >= vah:
            return trade(ctx, -1, max(x.high for x in back) + buffer, val, f"opened above value ({vah:,.2f}) and was pushed back in", self.p.min_rr)
        if opened < val and inside and before.close <= val:
            return trade(ctx, 1, min(x.low for x in back) - buffer, vah, f"opened below value ({val:,.2f}) and was bought back in", self.p.min_rr)
        return none("no re-entry into yesterday's value on this bar")
