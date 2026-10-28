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


# -- repricing before expiry -------------------------------------------------------------------
#
# Everything above settles AT expiry. A contract bought to be sold again — the expiry-day momentum
# buy — asks the other question: what is it worth with `hours` still left, if the underlying moves?
#
# That is Black-Scholes with no rate and no drift. These options are cash-settled on an index with
# no carry, so the forward is the spot and the discount factor is one. The same model the chain's
# own mark IV came from, so repricing at that IV is self-consistent: it says what the market would
# pay at the new spot under its own assumptions, not what the contract is "really" worth.


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(kind: str, spot: float, strike: float, iv: float, hours: float) -> float:
    """One option's value with `hours` left, at `iv` as an annualised fraction (0.45 = 45%)."""
    if spot <= 0 or strike <= 0:
        return 0.0
    if hours <= 0 or iv <= 0:
        return intrinsic(kind, strike, spot)
    sigma = iv * math.sqrt(hours / YEAR_HOURS)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma) / sigma
    d2 = d1 - sigma
    if kind == "call":
        return spot * norm_cdf(d1) - strike * norm_cdf(d2)
    return strike * norm_cdf(-d2) - spot * norm_cdf(-d1)


def reprice(kind: str, strike: float, spot: float, move_pct: float, iv: float, hours_left: float, minutes_ahead: float) -> float:
    """What the contract is worth once the underlying has moved `move_pct` over `minutes_ahead`.

    BOTH halves of the trade: the move pays (gamma) and the clock takes (theta). A figure that
    moved the spot without also moving the clock is a brochure, not an estimate — and in the last
    hours before expiry the clock is the larger of the two terms on anything that does not move.
    """
    return bs_price(kind, spot * (1 + move_pct / 100), strike, iv, max(0.0, hours_left - minutes_ahead / 60))


def multiple_at_move(
    kind: str, strike: float, paid: float, spot: float, move_pct: float, iv: float,
    hours_left: float, minutes_ahead: float, exit_haircut_pct: float = 0.0,
) -> float | None:
    """`reprice` as a multiple of what was paid, after giving `exit_haircut_pct` back to the book on
    the way out. None when there is nothing to compare with.

    The haircut is not a detail. Buying at the ask and selling at the bid on a contract quoting
    8/10 is a 20% round trip before anything happens, and a scan that prices the exit at the mark
    reports a 10x where the book pays 8x.
    """
    if paid <= 0:
        return None
    value = reprice(kind, strike, spot, move_pct, iv, hours_left, minutes_ahead) * (1 - exit_haircut_pct / 100)
    return value / paid
