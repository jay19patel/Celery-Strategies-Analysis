"""A built structure, priced and checked. The order ticket and the auto-trader both read this, so
what the dashboard shows before a click is exactly what the auto-trader decided on."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.options.pricing import analyze, distribution
from app.options.strategies import SELLER_CHECKS, Plan, spec_for
from app.settings import Settings

# Delta India's option fee: a rate on the underlying notional, capped at a share of the premium.
# The product spec carries the real figures; these are used only when it could not be read.
DEFAULT_FEE = (0.0005, 0.10)


@dataclass(frozen=True)
class FeeRate:
    taker: float  # on the underlying notional
    premium_cap: float  # never more than this share of the premium


def leg_fee(price: float, units: float, spot: float, rate: FeeRate) -> float:
    return min(rate.taker * spot * units, rate.premium_cap * price * units)


def widest_spread(plan: Plan) -> float | None:
    """The widest distance between a sold and a bought strike of the same kind: what a defined-risk
    credit structure can lose before its credit."""
    widths = [
        abs(b.strike - s.strike)
        for s in plan.legs if s.side == "sell"
        for b in plan.legs if b.side == "buy" and b.kind == s.kind
    ]
    return max(widths) if widths else None


def assess(
    plan: Plan, qty: int, iv: float | None, hours: float, s: Settings,
    rates: dict[str, FeeRate] | None = None, realised_vol: float | None = None,
) -> dict[str, Any]:
    specs = plan.specs(qty)
    analysis = analyze(specs, plan.spot, iv, hours)
    # Priced at the option market's IV, a fairly priced structure is worth its costs and nothing
    # more. Priced at the volatility the underlying has actually realised, the gap between the two
    # shows up: that is the edge a premium seller is paid for, or the one a buyer is not.
    pop_rv, ev_rv = distribution(specs, plan.spot, realised_vol, hours)
    analysis["pop_realised"] = None if pop_rv is None else round(100 * pop_rv, 1)
    analysis["expected_value_realised"] = None if ev_rv is None else round(ev_rv, 4)
    rates = rates or {}
    fees = sum(leg_fee(sp.price, sp.units, plan.spot, rates.get(leg.symbol, FeeRate(*DEFAULT_FEE))) for leg, sp in zip(plan.legs, specs, strict=True))
    analysis["entry_fees"] = round(fees, 4)
    analysis["round_trip_fees"] = round(2 * fees, 4)

    checks: list[dict[str, Any]] = []
    spec = spec_for(plan.strategy)
    # Which checks BIND is the strategy's own property. A seller must clear the lot; a long
    # momentum buy is gated on a two-sided book and nothing else, because its probability of
    # profit is low and its edge at realised vol negative by construction — refusing it on those
    # is refusing what it is, not catching a fault. Everything still runs and is still reported.
    required = spec.required_checks if spec else SELLER_CHECKS

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "required": name in required})

    spreads = [(leg.symbol, leg.spread_pct) for leg in plan.legs]
    missing = [sym for sym, sp in spreads if sp is None]
    wide = [f"{sym} {sp:.0f}%" for sym, sp in spreads if sp is not None and sp > s.max_spread_pct]
    check(
        "liquidity", not missing and not wide,
        f"no two-sided quote on {', '.join(missing)}" if missing
        else (f"bid/ask wider than {s.max_spread_pct:g}% on {', '.join(wide)}" if wide else "every leg has a two-sided quote"),
    )

    width = widest_spread(plan)
    unit_credit = analysis["net_premium"] / (qty * plan.legs[0].contract_value) if qty else 0.0
    if analysis["kind"] == "credit" and width:
        ratio = 100 * unit_credit / width
        check("credit / width", ratio >= s.min_credit_width_pct, f"credit {unit_credit:,.1f} on a {width:,.0f} wide spread = {ratio:.1f}% (min {s.min_credit_width_pct:g}%)")

    if analysis["pop"] is None:
        check("probability of profit", False, "no IV to price the settlement distribution with")
    else:
        check("probability of profit", analysis["pop"] >= s.min_pop_pct, f"{analysis['pop']:.1f}% at expiry from the ATM IV (min {s.min_pop_pct:g}%)")

    ev = analysis["expected_value_realised"]
    if ev is not None:
        net = ev - analysis["round_trip_fees"]
        check("edge at realised vol", net > 0, f"{net:+,.2f} expected after fees if volatility stays as realised")

    check(
        "risk", analysis["max_loss"] is not None,
        f"worst case {analysis['max_loss']:,.2f}" if analysis["max_loss"] is not None else "unlimited loss beyond the breakevens",
    )
    return {
        "strategy": plan.strategy,
        "label": spec.label if spec else "Custom",
        "plan": plan.to_dict(),
        "qty": qty,
        "hours_to_expiry": round(hours, 2),
        "iv": iv,
        "analysis": analysis,
        "checks": checks,
        "ok": all(c["ok"] for c in checks if c["required"]),
    }
