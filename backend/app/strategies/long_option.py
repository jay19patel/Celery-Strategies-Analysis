"""Long option: buy one call in an uptrend or one put in a downtrend, only when options are cheap.

The mirror of the premium sellers. A seller is paid when implied volatility sits above what the
underlying actually moves; a buyer has the edge when it sits BELOW it. Judged on the lowest realised
measure (`Desk.realised()["floor"]`), the one least kind to a buyer, and only bought when the
assessment's expected value at that volatility, after fees both ways, is positive.

Exits (`assess.DEBIT_*`, the guard): the premium is the worst case and the size is set on it, so
there is no premium stop for theta to trip — the deleted long straddle lost 6 of 6 that way. It
takes profit when the option has doubled, or settles at intrinsic at expiry.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.errors import Refused
from app.options import regime as r
from app.options.strategies import Plan, build_plan, otm_by_delta
from app.strategies.base import OptionContext, OptionParams, OptionStrategy

BUYER_CHECKS = ("liquidity", "edge at realised vol", "risk")


class LongOptionParams(OptionParams):
    delta: float = Field(0.35, ge=0.15, le=0.55, description="|delta| of the option bought")
    min_direction: float = Field(0.3, ge=0.1, le=0.9, description="How strong the regime's trend must be (0-1)")


class LongOption(OptionStrategy):
    name = "long_option"
    label = "Long Option"
    view, premium, risk = "directional", "debit", "defined"
    summary = "Buy a call in an uptrend or a put in a downtrend when implied volatility is below realised."
    required_checks = BUYER_CHECKS
    default_on = True
    Params = LongOptionParams

    def score(self, regime: dict[str, Any]) -> tuple[int, list[str]]:
        d = regime["direction"]
        return r.scored([
            *r.premium(regime["metrics"], False),
            ("trend to ride", abs(d) * 40 if abs(d) >= self.p.min_direction else -30),
            ("realised volatility expanding", 10 if r.expanding(regime["metrics"]) else 0),
        ])

    def build(self, ctx: OptionContext, expiry: float) -> Plan | None:
        d = ctx.regime(expiry)["direction"]
        if abs(d) < self.p.min_direction:
            raise Refused(f"trend {d:+.2f} is weaker than {self.p.min_direction:g} either way")
        kind = "call" if d > 0 else "put"
        q = otm_by_delta(ctx.chain(expiry), kind, ctx.spot, self.p.delta)
        return build_plan(self.name, [(q, "buy")], ctx.spot)
