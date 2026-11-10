"""Expiry decay spread: in the last hours before a daily expiry, sell an option still bid far out of
the money and buy the next strike beyond it, held to settlement (`options/decay.py` finds them).

Defined risk: the gap between the strikes caps the loss. Not scored by the regime — the scan itself
demands the distance at realised volatility and a positive expected value after fees — so it enters
at the auto-trader's bar and an option structure that fits the regime better wins the slot.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from app.errors import Refused
from app.market import options as chain
from app.options import decay
from app.options.strategies import Plan, custom
from app.strategies.base import OptionContext, OptionStrategy, Params

SCORE = 60


class DecayParams(Params):
    window_start_hours: float = Field(4.0, ge=0.5, le=12, description="Start selling this long before expiry")
    window_end_hours: float = Field(2.0, ge=0.25, le=11, description="Stop selling this long before expiry")

    @model_validator(mode="after")
    def _window(self) -> DecayParams:
        if self.window_end_hours >= self.window_start_hours:
            raise ValueError("window_end_hours must be below window_start_hours")
        return self


class ExpiryDecay(OptionStrategy):
    name = "expiry_decay"
    label = "Expiry Decay Spread"
    view, premium, risk = "neutral", "credit", "defined"
    summary = "Sell an option still bid far out of the money in the last hours, buy the next strike beyond it, hold to settlement."
    # The scan has already demanded a bid on the sold leg; the bought wing near expiry often has no
    # bid at all, so a two-sided-quote rule would refuse the very contract that caps the loss.
    required_checks = ("edge at realised vol",)
    Params = DecayParams

    def expiry(self, ctx: OptionContext) -> float | None:
        return chain.pick_expiry(ctx.quotes, self.p.window_end_hours, self.p.window_start_hours, ctx.now)

    def expiry_note(self) -> str:
        return f"no expiry {self.p.window_end_hours:g}h to {self.p.window_start_hours:g}h away"

    def score(self, regime: dict[str, Any]) -> tuple[int, list[str]]:
        return SCORE, [f"inside the last {self.p.window_start_hours:g}h before expiry ({SCORE})"]

    def build(self, ctx: OptionContext, expiry: float) -> Plan | None:
        quotes = ctx.chain(expiry)
        found = decay.candidates(quotes, ctx.spot, ctx.now, ctx.realised.get("reference"), ctx.fee, ctx.max_spread_pct)
        if not found:
            raise Refused("no far out-of-the-money option is bid enough to pay after fees at realised volatility")
        best = found[0]
        plan = custom({q.symbol: q for q in quotes}, [{"symbol": best["sell"], "side": "sell"}, {"symbol": best["buy"], "side": "buy"}], ctx.spot)
        return Plan(self.name, plan.underlying, plan.expiry, plan.spot, plan.legs)
