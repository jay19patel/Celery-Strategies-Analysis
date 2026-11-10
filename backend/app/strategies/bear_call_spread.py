"""Bear call spread: sell an OTM call at the target |delta|, buy a higher one. Profits below the short strike."""

from __future__ import annotations

from typing import Any

from app.market.options import OptionQuote
from app.options import regime as r
from app.options.strategies import Picked, otm_by_delta, wing
from app.strategies._options import ShortDeltaWings
from app.strategies.base import OptionStrategy


class BearCallSpread(OptionStrategy):
    name = "bear_call_spread"
    label = "Bear Call Spread"
    view, premium, risk = "bearish", "credit", "defined"
    summary = "Sell an OTM call, buy a higher call. Profits if price stays below the short strike."
    default_on = True
    Params = ShortDeltaWings

    def pick(self, quotes: list[OptionQuote], spot: float) -> Picked:
        sc = otm_by_delta(quotes, "call", spot, self.p.short_delta)
        return [(sc, "sell"), (wing(quotes, "call", sc.strike, spot * self.p.wing_width_pct / 100), "buy")] if sc else [(None, "")]

    def score(self, regime: dict[str, Any]) -> tuple[int, list[str]]:
        d = regime["direction"]
        return r.scored([
            *r.premium(regime["metrics"], True, 1.2),
            ("downtrend", -d * 40 if d < 0 else 0),
            ("uptrend against a bearish spread", -d * 45 if d > 0 else 0),
        ])
