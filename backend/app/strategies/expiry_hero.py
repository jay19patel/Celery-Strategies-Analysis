"""Expiry hero (zero-hero): in the last hours before a daily expiry, buy a cheap out-of-the-money
option the way the trend points — only when it is priced below its fair value at realised volatility.

Near expiry an OTM option is almost all gamma: through its strike it multiplies, short of it theta
takes it to zero. Most of these lose, so the only thing that makes them pay over many attempts is
buying them below fair value. Fair value is the option's expected payoff at the LOWEST realised
volatility (a driftless lognormal, `bs_price`); the one bought is the one with the most edge per
unit of premium after fees both ways, and the assessment's "edge at realised vol" must agree.

The worst case is the premium and the size is set on it: no stop, take profit when it has doubled,
else it settles at intrinsic. The trend is a filter, not the edge: with no trend it does nothing.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from app.errors import Refused
from app.market import options as chain
from app.options.pricing import bs_price
from app.options.strategies import Plan, build_plan
from app.strategies.base import OptionContext, OptionStrategy, Params
from app.strategies.long_option import BUYER_CHECKS

SCORE = 60  # the edge scan is the gate, as for the expiry decay spread: it enters at the auto-trader's bar


class HeroParams(Params):
    window_start_hours: float = Field(4.0, ge=1, le=12, description="Start buying this long before expiry")
    window_end_hours: float = Field(1.0, ge=0.5, le=11, description="Stop buying this long before expiry")
    min_delta: float = Field(0.08, ge=0.02, le=0.3, description="Cheapest |delta| bought")
    max_delta: float = Field(0.30, ge=0.1, le=0.5, description="Dearest |delta| bought")
    min_direction: float = Field(0.3, ge=0.1, le=0.9, description="How strong the regime's trend must be (0-1)")

    @model_validator(mode="after")
    def _window(self) -> HeroParams:
        if self.window_end_hours >= self.window_start_hours:
            raise ValueError("window_end_hours must be below window_start_hours")
        if self.min_delta >= self.max_delta:
            raise ValueError("min_delta must be below max_delta")
        return self


class ExpiryHero(OptionStrategy):
    name = "expiry_hero"
    label = "Expiry Hero"
    view, premium, risk = "directional", "debit", "defined"
    summary = "In the last hours before expiry, buy a cheap OTM option with the trend when it is below fair value at realised vol."
    required_checks = BUYER_CHECKS
    default_on = True
    Params = HeroParams

    def expiry(self, ctx: OptionContext) -> float | None:
        return chain.pick_expiry(ctx.quotes, self.p.window_end_hours, self.p.window_start_hours, ctx.now)

    def expiry_note(self) -> str:
        return f"no expiry {self.p.window_end_hours:g}h to {self.p.window_start_hours:g}h away"

    def score(self, regime: dict[str, Any]) -> tuple[int, list[str]]:
        d = regime["direction"]
        if abs(d) < self.p.min_direction:
            return 0, [f"trend {d:+.2f} is weaker than {self.p.min_direction:g} either way (0)"]
        return SCORE, [f"trend {d:+.2f} inside the last {self.p.window_start_hours:g}h before expiry ({SCORE})"]

    def build(self, ctx: OptionContext, expiry: float) -> Plan | None:
        vol = ctx.realised.get("floor")
        if not vol:
            raise Refused("no realised volatility measured yet to price the edge with")
        kind = "call" if ctx.regime(expiry)["direction"] > 0 else "put"
        hours = (expiry - ctx.now) / 3600
        best, best_edge = None, 0.0
        for q in ctx.chain(expiry):
            if q.kind != kind or not q.ask or q.delta is None or q.spread_pct is None or q.spread_pct > ctx.max_spread_pct:
                continue
            if not self.p.min_delta <= abs(q.delta) <= self.p.max_delta:
                continue
            edge = (bs_price(kind, ctx.spot, q.strike, vol, hours) - q.ask - 2 * ctx.fee(q.ask)) / q.ask
            if edge > best_edge:
                best, best_edge = q, edge
        if best is None:
            raise Refused(f"no OTM {kind} is priced under its fair value at realised volatility {100 * vol:.0f}% after fees")
        return build_plan(self.name, [(best, "buy")], ctx.spot)
