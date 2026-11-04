"""Gamma exposure: how much the option book's hedgers must trade per 1% move, by strike.

    GEX(strike) = gamma x open interest x spot^2 x 1%       (USD of delta to rehedge per 1% move)

With the usual convention — the dealers who make these markets are long the calls customers sell
them and short the puts customers buy — call gamma is counted positive and put gamma negative.
That is an ASSUMPTION about who holds the other side, not something the exchange reports, and on
a venue this size the hedging it implies may be small next to the perpetual's own volume. Read it
as a regime, not a force:

    net > 0   hedgers sell rallies and buy dips: moves are damped, ranges and pins favoured
    net < 0   hedgers chase: moves are amplified, breaks run
    flip      the spot where net crosses zero; walls the strikes with the most of each sign

Gamma is recomputed here with Black-Scholes from each contract's own IV, not read from the feed,
so every strike and every hypothetical spot is in the same units. Pure: quotes in, numbers out.
"""

from __future__ import annotations

import math
from typing import Any

from app.market.options import OptionQuote
from app.options.pricing import YEAR_HOURS

MAX_DAYS = 30  # expiries further out barely move with spot today
FLIP_RANGE, FLIP_STEP = 0.10, 0.0025  # search ±10% of spot in 0.25% steps


def bs_gamma(spot: float, strike: float, iv: float, hours: float) -> float:
    """Change in delta per 1 of price, per 1 unit of the underlying."""
    if spot <= 0 or strike <= 0 or iv <= 0 or hours <= 0:
        return 0.0
    sigma = iv * math.sqrt(hours / YEAR_HOURS)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma) / sigma
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (spot * sigma)


def _book(quotes: list[OptionQuote], now: float) -> list[tuple[str, float, float, float, float]]:
    """(kind, strike, iv, hours, open interest) for every contract that can carry gamma."""
    out = []
    for q in quotes:
        hours = (q.expiry - now) / 3600
        if 0 < hours <= MAX_DAYS * 24 and q.iv and q.iv > 0 and q.oi > 0:
            out.append((q.kind, q.strike, q.iv, hours, q.oi))
    return out


def _net(book: list[tuple[str, float, float, float, float]], spot: float) -> float:
    return sum((1 if kind == "call" else -1) * bs_gamma(spot, k, iv, h) * oi * spot * spot * 0.01 for kind, k, iv, h, oi in book)


def exposure(quotes: list[OptionQuote], spot: float, now: float) -> dict[str, Any]:
    """Net GEX, the flip, the walls and GEX by strike near spot. Empty when nothing carries gamma."""
    book = _book(quotes, now)
    if not book or spot <= 0:
        return {"ready": False, "note": "no open interest with an IV to weigh"}
    by_strike: dict[float, list[float]] = {}
    for kind, k, iv, h, oi in book:
        g = bs_gamma(spot, k, iv, h) * oi * spot * spot * 0.01
        row = by_strike.setdefault(k, [0.0, 0.0])
        row[0 if kind == "call" else 1] += g if kind == "call" else -g
    net = sum(c + p for c, p in by_strike.values())
    # The flip: walk out from spot both ways and take the nearest sign change of the repriced net.
    flip = None
    steps = int(FLIP_RANGE / FLIP_STEP)
    here = math.copysign(1, net) if net else 0.0
    for i in range(1, steps + 1):
        for direction in (1, -1):
            s = spot * (1 + direction * i * FLIP_STEP)
            if here and math.copysign(1, _net(book, s)) != here:
                flip = round(s, 2)
                break
        if flip is not None:
            break
    calls = {k: c for k, (c, _) in by_strike.items() if c > 0}
    puts = {k: p for k, (_, p) in by_strike.items() if p < 0}
    near = sorted(k for k in by_strike if abs(k / spot - 1) <= FLIP_RANGE)
    return {
        "ready": True, "spot": spot, "net": round(net, 2), "regime": "positive" if net >= 0 else "negative",
        "flip": flip, "call_wall": max(calls, key=lambda k: calls[k]) if calls else None,
        "put_wall": min(puts, key=lambda k: puts[k]) if puts else None,
        "by_strike": [{"strike": k, "call": round(by_strike[k][0], 2), "put": round(by_strike[k][1], 2)} for k in near],
    }
