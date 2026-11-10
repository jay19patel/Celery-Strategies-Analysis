"""Iron butterfly: sell the ATM straddle, buy wings. More credit than a condor; needs a pin."""

from __future__ import annotations

from typing import Any

from app.market.options import OptionQuote
from app.options import regime as r
from app.options.strategies import Picked, atm, wing
from app.strategies._options import Wings
from app.strategies.base import OptionStrategy


class IronButterfly(OptionStrategy):
    name = "iron_butterfly"
    label = "Iron Butterfly"
    view, premium, risk = "neutral", "credit", "defined"
    summary = "Sell the ATM straddle, buy wings. More credit than a condor, needs price to pin near the strike."
    # Its breakevens sit close to the strike, so its probability of profit is under 50% by
    # construction; the credit it takes for that is what credit / width and the edge judge.
    required_checks = ("liquidity", "credit / width", "edge at realised vol")
    default_on = True
    Params = Wings

    def pick(self, quotes: list[OptionQuote], spot: float) -> Picked:
        both = atm(quotes, spot)
        if not both:
            return [(None, "")]
        c, p = both
        width = spot * self.p.wing_width_pct / 100
        return [(wing(quotes, "put", p.strike, width), "buy"), (p, "sell"), (c, "sell"), (wing(quotes, "call", c.strike, width), "buy")]

    def score(self, regime: dict[str, Any]) -> tuple[int, list[str]]:
        m = regime["metrics"]
        return r.scored([
            *r.pinned(regime.get("gamma")),
            *r.premium(m, True),
            ("trend against a pinned structure", -abs(regime["direction"]) * 45),
            ("price near max pain", 8 if r.near_pain(m) else 0),
            ("volatility compressed", 6 if r.compressed(m) else 0),
            ("realised volatility expanding", -15 if r.expanding(m) else 0),
        ])
