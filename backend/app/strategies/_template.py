"""A template for a new perpetual strategy. Copy this file to `app/strategies/<your_name>.py`, rename
the class, set `name`, and fill in `decide()`. This copy is never registered: modules whose name
starts with "_" are skipped. See docs/ADDING_A_STRATEGY.md.

The rules a strategy keeps:
  - `decide()` is pure: closed bars in (`ctx.candles`, oldest first, the last one just closed), a
    `Decision` out. No clock, no network, no database, no orders, no state between bars.
  - It returns the reason it did nothing (`none("...")`) as carefully as the reason it trades.
  - Its stop sits where the idea is wrong; its target is a price; both on the right side of entry.
  - Its numbers are `Params`: defaults, bounds and a description. The dashboard draws the form.
  - It starts off (`default_on = False`) and on paper; a measured edge earns it size.
"""

from __future__ import annotations

from pydantic import Field

from app.market.indicators import atr
from app.strategies.base import Context, Decision, Params, Strategy, bracket, min_stop_pct, net_rr, none


class TemplateParams(Params):
    lookback: int = Field(55, ge=10, le=200, description="Bars whose high a close must clear")
    stop_atr: float = Field(2.0, ge=0.5, le=5, description="Stop this many ATRs below the entry")
    reward_r: float = Field(3.0, ge=1, le=10, description="Target this many times the risk")
    max_hold_hours: float = Field(96.0, ge=4, le=720, description="Closed at the market after this long")


class DonchianBreakout(Strategy):
    name = "template_donchian"  # stable forever: settings, trades and signals are keyed by it
    version = 1  # bump when decide() changes what it trades
    label = "Donchian breakout (template)"
    summary = "A close above the highest high of the last `lookback` bars: buy, stop a few ATRs below."
    stop_rule = "stop_atr x ATR(14) below the entry"
    target_rule = "reward_r x the stop distance"
    interval = "4h"  # 5m, 15m, 1h, 4h or 1d: folded from the stored bars (`analysis.resample`)
    lookback = 260  # closed bars handed to decide()
    Params = TemplateParams

    def decide(self, ctx: Context) -> Decision:
        bars = ctx.candles
        if len(bars) < self.p.lookback + 15:
            return none(f"needs {self.p.lookback + 15} bars, has {len(bars)}")
        high = max(b.high for b in bars[-self.p.lookback - 1:-1])
        price = bars[-1].close
        if price <= high:
            return none(f"no close above the {self.p.lookback}-bar high {high:,.2f}")
        risk = self.p.stop_atr * atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars], 14)[-1]
        stop_pct = 100 * risk / price
        if stop_pct < min_stop_pct(ctx.cost_pct, 0.3):
            return none(f"the stop is only {stop_pct:.2f}% away: the costs would eat it")
        stop = round(price - risk, 2)
        target = bracket(1, price, stop, self.p.reward_r)
        rr = net_rr(100 * (target - price) / price, stop_pct, ctx.cost_pct)
        return Decision("buy", f"closed above the {self.p.lookback}-bar high {high:,.2f}; {rr:.1f}:1 after costs",
                        entry=price, stop=stop, target=round(target, 2))
