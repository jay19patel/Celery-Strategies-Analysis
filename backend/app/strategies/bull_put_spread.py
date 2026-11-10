"""Bull put spread: sell an OTM put at the target |delta|, buy a lower one. Profits above the short strike."""

from __future__ import annotations

from typing import Any

from app.market.options import OptionQuote
from app.options import regime as r
from app.options.strategies import Picked, otm_by_delta, wing
from app.strategies._options import ShortDeltaWings
from app.strategies.base import OptionStrategy


class BullPutSpread(OptionStrategy):
    name = "bull_put_spread"
    label = "Bull Put Spread"
    view, premium, risk = "bullish", "credit", "defined"
    summary = "Sell an OTM put, buy a lower put. Profits if price holds above the short strike."
    default_on = True
    Params = ShortDeltaWings

    def pick(self, quotes: list[OptionQuote], spot: float) -> Picked:
        sp = otm_by_delta(quotes, "put", spot, self.p.short_delta)
        return [(wing(quotes, "put", sp.strike, spot * self.p.wing_width_pct / 100), "buy"), (sp, "sell")] if sp else [(None, "")]

    def score(self, regime: dict[str, Any]) -> tuple[int, list[str]]:
        d = regime["direction"]
        return r.scored([
            *r.premium(regime["metrics"], True, 1.2),
            ("uptrend", d * 40 if d > 0 else 0),
            ("downtrend against a bullish spread", d * 45 if d < 0 else 0),
        ])
