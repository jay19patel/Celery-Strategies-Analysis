"""Iron condor: sell an OTM call and put at the target |delta|, buy wings further out. Profits while
price stays between the short strikes; the worst case is the wider wing less the credit."""

from __future__ import annotations

from typing import Any

from app.market.options import OptionQuote
from app.options import regime as r
from app.options.strategies import Picked, otm_by_delta, wing
from app.strategies._options import ShortDeltaWings
from app.strategies.base import OptionStrategy


class IronCondor(OptionStrategy):
    name = "iron_condor"
    label = "Iron Condor"
    view, premium, risk = "neutral", "credit", "defined"
    summary = "Sell an OTM call and put, buy further wings. Profits while price stays between the short strikes."
    default_on = True
    Params = ShortDeltaWings

    def pick(self, quotes: list[OptionQuote], spot: float) -> Picked:
        sc, sp = otm_by_delta(quotes, "call", spot, self.p.short_delta), otm_by_delta(quotes, "put", spot, self.p.short_delta)
        if not sc or not sp:
            return [(None, "")]
        width = spot * self.p.wing_width_pct / 100
        return [(wing(quotes, "put", sp.strike, width), "buy"), (sp, "sell"), (sc, "sell"), (wing(quotes, "call", sc.strike, width), "buy")]

    def score(self, regime: dict[str, Any]) -> tuple[int, list[str]]:
        m = regime["metrics"]
        return r.scored([
            *r.pinned(regime.get("gamma")),
            *r.premium(m, True),
            ("trend against a neutral structure", -abs(regime["direction"]) * 35),
            ("price ranging", 10 if regime["label"] in ("range", "quiet range") else 0),
            ("realised volatility expanding", -10 if r.expanding(m) else 0),
        ])
