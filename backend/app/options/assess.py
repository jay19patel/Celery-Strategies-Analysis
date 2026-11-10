"""A built structure, priced and checked. The order ticket and the auto-trader both read this, so
what the dashboard shows before a click is exactly what the auto-trader decided on."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.options.pricing import analyze, distribution, payoff
from app.options.strategies import SELLER_CHECKS, Plan, describe

# Delta India's option fee: a rate on the underlying notional, capped at a share of the premium.
# The product spec carries the real figures; these are used only when it could not be read.
DEFAULT_FEE = (0.0005, 0.10)
GST = 0.18  # India's GST, charged on every exchange fee on top of it
# The move an unbounded structure is sized for: this many of the expected moves to expiry. A stop
# is a hope on a gap, so the worst case is read here instead of at the stop.
STRESS_MOVES = 3.0
# Exits a structure opens with (each position's own can be changed on the Positions page). A
# defined-risk credit is held to settlement and uses none of them.
CREDIT_TAKE_PROFIT_PCT = 50.0  # of the maximum profit
CREDIT_STOP_LOSS_PCT = 100.0  # of the credit received
DEBIT_TAKE_PROFIT_PCT = 60.0  # of the debit paid
DEBIT_STOP_LOSS_PCT = 50.0  # of the debit paid


@dataclass(frozen=True)
class FeeRate:
    """What a trade is actually charged, GST included."""

    taker: float  # on the underlying notional
    premium_cap: float  # never more than this share of the premium

    @classmethod
    def with_gst(cls, taker: float, premium_cap: float) -> FeeRate:
        """The exchange's rates, which the product spec states before GST, as charged."""
        return cls(taker * (1 + GST), premium_cap * (1 + GST))


def leg_fee(price: float, units: float, spot: float, rate: FeeRate) -> float:
    return min(rate.taker * spot * units, rate.premium_cap * price * units)


def unit_risk(assessment: dict[str, Any]) -> float:
    """The worst case of the assessed size: the max loss when it is bounded. Unbounded, the larger of
    the loss at the stop and the loss after `STRESS_MOVES` expected moves — a stop on a gap fills
    where the market is, not where it was set."""
    a = assessment["analysis"]
    if a["max_loss"] is not None:
        return a["max_loss"] + a["round_trip_fees"]
    at_stop = abs(a["net_premium"]) * CREDIT_STOP_LOSS_PCT / 100
    return max(at_stop, a.get("stress_loss") or 0.0) + a["round_trip_fees"]


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
    plan: Plan, qty: int, iv: float | None, hours: float, max_spread_pct: float,
    rates: dict[str, FeeRate] | None = None, realised_vol: float | None = None, *,
    label: str = "", required: tuple[str, ...] = SELLER_CHECKS, min_pop_pct: float = 55.0, min_credit_width_pct: float = 5.0,
) -> dict[str, Any]:
    """The plan priced at `qty` and checked. `required` names the checks that bind for this
    strategy (`OptionStrategy.required_checks`); a hand-built structure is gated as a seller."""
    specs = plan.specs(qty)
    analysis = analyze(specs, plan.spot, iv, hours)
    # Priced at the option market's IV, a fairly priced structure is worth its costs and nothing
    # more. Priced at the volatility the underlying has actually realised, the gap between the two
    # shows up: that is the edge a premium seller is paid for, or the one a buyer is not.
    pop_rv, ev_rv = distribution(specs, plan.spot, realised_vol, hours)
    analysis["pop_realised"] = None if pop_rv is None else round(100 * pop_rv, 1)
    analysis["expected_value_realised"] = None if ev_rv is None else round(ev_rv, 4)
    move = STRESS_MOVES * analysis["expected_move"]
    probes = [plan.spot - move, plan.spot + move, *(k for k in {leg.strike for leg in plan.legs} if abs(k - plan.spot) <= move)]
    analysis["stress_loss"] = round(max(0.0, -min(payoff(specs, max(p, 0.0)) for p in probes)), 4)
    rates = rates or {}
    fees = sum(leg_fee(sp.price, sp.units, plan.spot, rates.get(leg.symbol, FeeRate.with_gst(*DEFAULT_FEE))) for leg, sp in zip(plan.legs, specs, strict=True))
    analysis["entry_fees"] = round(fees, 4)
    analysis["round_trip_fees"] = round(2 * fees, 4)

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "required": name in required})

    spreads = [(leg.symbol, leg.spread_pct) for leg in plan.legs]
    missing = [sym for sym, sp in spreads if sp is None]
    wide = [f"{sym} {sp:.0f}%" for sym, sp in spreads if sp is not None and sp > max_spread_pct]
    check(
        "liquidity", not missing and not wide,
        f"no two-sided quote on {', '.join(missing)}" if missing
        else (f"bid/ask wider than {max_spread_pct:g}% on {', '.join(wide)}" if wide else "every leg has a two-sided quote"),
    )

    width = widest_spread(plan)
    unit_credit = analysis["net_premium"] / (qty * plan.legs[0].contract_value) if qty else 0.0
    if analysis["kind"] == "credit" and width:
        ratio = 100 * unit_credit / width
        check("credit / width", ratio >= min_credit_width_pct, f"credit {unit_credit:,.1f} on a {width:,.0f} wide spread = {ratio:.1f}% (min {min_credit_width_pct:g}%)")

    if analysis["pop"] is None:
        check("probability of profit", False, "no IV to price the settlement distribution with")
    else:
        check("probability of profit", analysis["pop"] >= min_pop_pct, f"{analysis['pop']:.1f}% at expiry from the ATM IV (min {min_pop_pct:g}%)")

    ev = analysis["expected_value_realised"]
    if ev is None:  # absence is a refusal, never a pass: without it a seller trades on POP alone
        check("edge at realised vol", False, "no realised volatility measured yet to price the edge with")
    else:
        net = ev - analysis["round_trip_fees"]
        check("edge at realised vol", net > 0, f"{net:+,.2f} expected after fees if volatility stays as realised")

    check(
        "risk", analysis["max_loss"] is not None,
        f"worst case {analysis['max_loss']:,.2f}" if analysis["max_loss"] is not None else "unlimited loss beyond the breakevens",
    )
    return {
        "strategy": plan.strategy,
        "label": label or describe(plan.legs),
        "plan": plan.to_dict(),
        "qty": qty,
        "hours_to_expiry": round(hours, 2),
        "iv": iv,
        "analysis": analysis,
        "checks": checks,
        "ok": all(c["ok"] for c in checks if c["required"]),
    }
