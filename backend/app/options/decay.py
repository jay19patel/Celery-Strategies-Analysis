"""Expiry-day decay: premium still sitting on options the market is unlikely to reach.

In the last hours before expiry an out-of-the-money option is nothing but time value, and time value
goes to zero by the settlement. When one far enough from spot still bids for something, selling it
collects that decay — the mirror of the zero-hero buy, which pays for exactly such an option.

Sold as a SPREAD, never alone: the option is sold and the next listed strike beyond it bought, which
on an expiring chain costs next to nothing and caps the loss at the gap between them. That keeps a
gap-through-the-strike to a known amount and lets it trade on real money.

A candidate must be all of:
    far        at least `MIN_SIGMAS` standard deviations away at REALISED volatility over the
               hours left — distance measured in what the market is actually moving, not in its IV
    paid       positive expected value after fees under a driftless lognormal at that volatility,
               held to settlement: the credit, less what the spread is worth at expiry, less fees
    tradeable  a bid on the sold leg within the spread limit, an ask on the bought one

Ranked by expected value per unit of maximum loss. Pure: quotes in, candidates out.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from app.market.options import OptionQuote
from app.options.pricing import YEAR_HOURS, bs_price, norm_cdf

MIN_SIGMAS = 1.5
MAX_CANDIDATES = 6


def _beyond(quotes: list[OptionQuote], q: OptionQuote) -> OptionQuote | None:
    """The next listed strike further out of the money than `q`, of the same kind, with an ask."""
    further = [x for x in quotes if x.kind == q.kind and x.ask and ((x.strike > q.strike) if q.kind == "call" else (x.strike < q.strike))]
    return min(further, key=lambda x: abs(x.strike - q.strike)) if further else None


def candidates(
    quotes: list[OptionQuote], spot: float, now: float, vol: float | None, fee: Callable[[float], float], max_spread_pct: float,
) -> list[dict[str, Any]]:
    """Every decay spread on one expiry worth selling now, best first. `vol` is realised volatility
    (annualised fraction); `fee(price)` is one leg's fee per unit, paid on the way in and out."""
    if not vol or vol <= 0 or spot <= 0:
        return []
    out = []
    for q in quotes:
        hours = q.hours_left(now)
        if hours <= 0 or not q.bid or q.spread_pct is None or q.spread_pct > max_spread_pct:
            continue
        if (q.kind == "call" and q.strike <= spot) or (q.kind == "put" and q.strike >= spot):
            continue
        sigma = vol * math.sqrt(hours / YEAR_HOURS)
        sigmas = abs(math.log(q.strike / spot)) / sigma
        if sigmas < MIN_SIGMAS:
            continue
        wing = _beyond(quotes, q)
        if wing is None or not wing.ask:
            continue
        width = abs(wing.strike - q.strike)
        fees = 2 * (fee(q.bid) + fee(wing.ask))
        credit = q.bid - wing.ask
        if credit <= fees:
            continue
        # What the spread is worth at expiry, on average, at the volatility the market is showing.
        owed = bs_price(q.kind, spot, q.strike, vol, hours) - bs_price(q.kind, spot, wing.strike, vol, hours)
        ev = credit - owed - fees
        if ev <= 0:
            continue
        breakeven = q.strike + credit if q.kind == "call" else q.strike - credit
        z = (math.log(breakeven / spot) + 0.5 * sigma * sigma) / sigma
        pop = norm_cdf(z) if q.kind == "call" else 1 - norm_cdf(z)
        max_loss = width - credit + fees
        cv = q.contract_value
        out.append({
            "sell": q.symbol, "buy": wing.symbol, "kind": q.kind, "strike": q.strike, "wing": wing.strike, "expiry": q.expiry,
            "hours_left": round(hours, 2), "sigmas": round(sigmas, 2), "credit": round(credit, 4), "width": width,
            "pop": round(pop, 4), "ev": round(ev, 4), "max_loss": round(max_loss, 4),
            "ev_per_lot": round(ev * cv, 6), "max_loss_per_lot": round(max_loss * cv, 6),
            "ev_on_risk_pct": round(100 * ev / max_loss, 2) if max_loss > 0 else None,
            "why": (f"sell the {q.strike:,.0f} {q.kind} at {q.bid:,.2f}, buy the {wing.strike:,.0f} at {wing.ask:,.2f}: "
                    f"{sigmas:.1f} sigma away at realised vol with {hours:.1f}h left, wins {100 * pop:.0f}% held to settlement"),
        })
    out.sort(key=lambda c: c["ev_on_risk_pct"] or 0.0, reverse=True)
    return out[:MAX_CANDIDATES]
