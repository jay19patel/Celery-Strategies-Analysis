"""The arithmetic of a multi-leg options position. Pure: legs in, numbers out.

Every leg is (kind, strike, side, size in contracts, premium per unit of the underlying, contract
value), so a leg's P&L at expiry is  ±(intrinsic − premium) × size × contract_value  — plus for a
seller, minus for a buyer. The payoff of any combination is piecewise linear with knots at the
strikes, which is why max profit, max loss and the breakevens are found exactly from the knots and
the slope beyond the highest strike, not by sampling a curve.

The probability of profit and the expected value are model numbers: the settlement price is taken
as lognormal with the option market's own ATM IV and no drift. They say what the prices imply, not
what will happen, and they ignore an exit before expiry.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

YEAR_HOURS = 365 * 24
NAKED_MARGIN_PCT = 0.10  # paper margin for an uncovered short: 10% of the underlying notional plus its premium


@dataclass(frozen=True)
class LegSpec:
    kind: str  # call | put
    strike: float
    side: str  # buy | sell
    size: int  # contracts
    price: float  # premium per unit of the underlying
    contract_value: float
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None

    @property
    def sign(self) -> int:
        return 1 if self.side == "buy" else -1

    @property
    def units(self) -> float:
        return self.size * self.contract_value


def intrinsic(kind: str, strike: float, s: float) -> float:
    return max(0.0, s - strike) if kind == "call" else max(0.0, strike - s)


def payoff(legs: Sequence[LegSpec], s: float) -> float:
    """P&L at expiry if the underlying settles at `s`, in dollars, before fees."""
    return sum(leg.sign * (intrinsic(leg.kind, leg.strike, s) - leg.price) * leg.units for leg in legs)


def net_premium(legs: Sequence[LegSpec]) -> float:
    """Cash at entry: positive is a credit received, negative a debit paid."""
    return sum(-leg.sign * leg.price * leg.units for leg in legs)


def _breakevens(points: list[tuple[float, float]]) -> list[float]:
    out: list[float] = []
    for (s0, v0), (s1, v1) in itertools.pairwise(points):
        if v0 == 0 and (not out or abs(out[-1] - s0) > 1e-9):
            out.append(s0)
        elif v0 * v1 < 0:
            out.append(s0 + (s1 - s0) * (-v0) / (v1 - v0))
    if points and points[-1][1] == 0 and (not out or abs(out[-1] - points[-1][0]) > 1e-9):
        out.append(points[-1][0])
    return [round(b, 2) for b in out]


def distribution(legs: Sequence[LegSpec], spot: float, iv: float | None, hours: float, steps: int = 1200) -> tuple[float | None, float | None]:
    """(probability the expiry P&L is positive, expected expiry P&L) under a driftless lognormal."""
    if not iv or iv <= 0 or hours <= 0 or spot <= 0:
        return None, None
    sigma = iv * math.sqrt(hours / YEAR_HOURS)
    mu = math.log(spot) - 0.5 * sigma * sigma
    lo, hi = -6.0, 6.0
    dz = (hi - lo) / steps
    p_win = ev = total = 0.0
    for i in range(steps):
        z = lo + (i + 0.5) * dz
        w = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi) * dz
        value = payoff(legs, math.exp(mu + sigma * z))
        total += w
        ev += w * value
        if value > 0:
            p_win += w
    return p_win / total, ev / total


def analyze(legs: Sequence[LegSpec], spot: float, iv: float | None = None, hours: float = 0.0, curve_points: int = 121) -> dict[str, Any]:
    if not legs:
        raise ValueError("a position needs at least one leg")
    strikes = sorted({leg.strike for leg in legs})
    far = max(strikes[-1], spot) * 4
    knots = [0.0, *strikes, far]
    values = [(s, payoff(legs, s)) for s in knots]

    # Beyond the highest strike only calls move: their net size is the slope to infinity.
    slope_up = sum(leg.sign * leg.units for leg in legs if leg.kind == "call")
    best = max(v for _, v in values)
    worst = min(v for _, v in values)
    max_profit = None if slope_up > 1e-12 else best
    max_loss = None if slope_up < -1e-12 else max(0.0, -worst)

    p_win, ev = distribution(legs, spot, iv, hours)
    move = spot * iv * math.sqrt(hours / YEAR_HOURS) if iv and hours > 0 else spot * 0.03
    half = max(3 * move, (strikes[-1] - strikes[0]) * 0.75, spot * 0.02)
    lo, hi = max(0.0, min(strikes[0], spot) - half), max(strikes[-1], spot) + half
    curve = [
        {"price": round(s, 2), "pnl": round(payoff(legs, s), 4)}
        for s in (lo + (hi - lo) * i / (curve_points - 1) for i in range(curve_points))
    ]

    def total(attr: str) -> float | None:
        vals = [getattr(leg, attr) for leg in legs]
        if any(v is None for v in vals):
            return None
        return round(sum(leg.sign * getattr(leg, attr) * leg.units for leg in legs), 6)

    premium = net_premium(legs)
    return {
        "net_premium": round(premium, 4),
        "kind": "credit" if premium > 0 else "debit",
        "max_profit": None if max_profit is None else round(max_profit, 4),
        "max_loss": None if max_loss is None else round(max_loss, 4),
        "breakevens": _breakevens(values),
        "pop": None if p_win is None else round(100 * p_win, 1),
        "expected_value": None if ev is None else round(ev, 4),
        "reward_risk": round(max_profit / max_loss, 3) if max_profit and max_loss else None,
        "margin": round(margin_required(legs, spot, max_loss), 4),
        "greeks": {g: total(g) for g in ("delta", "gamma", "theta", "vega")},
        "expected_move": round(move, 2),
        "curve": curve,
    }


def margin_required(legs: Sequence[LegSpec], spot: float, max_loss: float | None) -> float:
    """What the paper broker sets aside: the worst case when it is bounded, and for an uncovered
    short a share of the underlying it controls, like an exchange's initial margin."""
    if max_loss is not None:
        return max_loss
    return sum((NAKED_MARGIN_PCT * spot + leg.price) * leg.units for leg in legs if leg.side == "sell")
