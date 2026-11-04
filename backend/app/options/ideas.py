"""Single-option ideas: every liquid contract of one expiry, priced as a buy at its ask and as a sell
at its bid against what the market is actually moving, with the exit the desk would use.

The arithmetic is the desk's own. Under a driftless lognormal with no carry, an option's expected
payoff at expiry IS its Black-Scholes value at that volatility — so the fair value at realised vol
is `bs_price` at realised vol, and the edge is the gap between that and the price the book offers,
less the fees both ways. Each side is judged on the realised measure least kind to it: a buyer on
the lowest, a seller on the highest (`Desk.realised`).

The hold time is an estimate with its assumption named, never a promise:
  buy   the move that lifts the option to its take profit (repriced at its own IV with the time
        left now: decay on the way makes the real move somewhat larger), and how long a typical
        one-sigma move at realised vol takes to cover it
  sell  how long, if price does not move, time decay takes to reach the take profit
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from app.analysis.outlook import p_touch
from app.market.options import OptionQuote
from app.options.pricing import YEAR_HOURS, bs_price, intrinsic

MIN_ABS_DELTA, MAX_ABS_DELTA = 0.05, 0.65  # lottery tickets and deep in-the-money stock substitutes are not ideas
TREND = 0.15  # |direction| under this is no lean either way
MAX_IDEAS = 12


def _bisect(f: Callable[[float], float], lo: float, hi: float, steps: int = 60) -> float | None:
    """The x in [lo, hi] where f crosses zero, when it does."""
    flo, fhi = f(lo), f(hi)
    if flo == 0:
        return lo
    if flo * fhi > 0:
        return None
    for _ in range(steps):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def _pop(kind: str, side: str, strike: float, price: float, spot: float, vol: float, hours: float) -> float | None:
    """Chance the position is in profit at expiry, before fees."""
    if vol <= 0 or hours <= 0:
        return None
    sigma = vol * math.sqrt(hours / YEAR_HOURS)
    breakeven = strike + price if kind == "call" else strike - price
    if breakeven <= 0:
        return 1.0 if side == "buy" else 0.0
    above = 1 - 0.5 * (1 + math.erf(((math.log(breakeven / spot) + 0.5 * sigma * sigma) / sigma) / math.sqrt(2)))
    long_wins = above if kind == "call" else 1 - above
    return long_wins if side == "buy" else 1 - long_wins


def _buy(q: OptionQuote, spot: float, hours: float, vol: float, fee: float, take_profit_pct: float) -> dict[str, Any] | None:
    price, iv = q.ask or 0.0, q.iv or 0.0
    if price <= 0:
        return None
    edge = bs_price(q.kind, spot, q.strike, vol, hours) - price - 2 * fee
    target = price * (1 + take_profit_pct / 100)
    up = q.kind == "call"
    needed = _bisect(lambda s: bs_price(q.kind, s, q.strike, iv, hours) - target, spot, spot * (3 if up else 1 / 3)) if iv > 0 else None
    move = None if needed is None else abs(math.log(needed / spot))
    hold = None if move is None or vol <= 0 else (move / vol) ** 2 * YEAR_HOURS
    reach = None if move is None else p_touch(move, vol * math.sqrt(hours / YEAR_HOURS))
    return {
        "side": "buy", "price": price, "edge": edge, "risk": price + 2 * fee, "max_loss": price + 2 * fee,
        "target_price": target, "needed_move_pct": None if needed is None else 100 * (needed / spot - 1),
        "p_target": reach, "hold_hours": hold if hold is not None and hold <= hours else None,
        "exit": (
            f"take profit at {target:,.2f} (+{take_profit_pct:g}%), which needs spot at {needed:,.0f}" if needed
            else "the take profit is out of reach before expiry"
        ),
        "pop": _pop(q.kind, "buy", q.strike, price, spot, vol, hours),
    }


def _sell(q: OptionQuote, spot: float, hours: float, vol: float, fee: float, take_profit_pct: float) -> dict[str, Any] | None:
    price, iv = q.bid or 0.0, q.iv or 0.0
    if price <= 0:
        return None
    edge = price - bs_price(q.kind, spot, q.strike, vol, hours) - 2 * fee
    target = price * (1 - take_profit_pct / 100)
    # Time decay alone, price held where it is: when does the contract fall to the take profit?
    elapsed = _bisect(lambda t: bs_price(q.kind, spot, q.strike, iv, hours - t) - target, 0.0, hours) if iv > 0 else None
    # A two-sigma move against it is the risk it is ranked on; a sold call's loss has no ceiling.
    sigma = vol * math.sqrt(hours / YEAR_HOURS)
    adverse = spot * math.exp(2 * sigma) if q.kind == "call" else spot * math.exp(-2 * sigma)
    stress = max(price, intrinsic(q.kind, q.strike, adverse) - price) + 2 * fee
    return {
        "side": "sell", "price": price, "edge": edge, "risk": stress,
        "max_loss": None if q.kind == "call" else q.strike - price + 2 * fee,
        "target_price": target, "needed_move_pct": None, "p_target": None,
        "hold_hours": elapsed,
        "exit": (
            f"buy back at {target:,.2f} (-{take_profit_pct:g}%): about {elapsed:.1f}h of decay if price stays" if elapsed is not None
            else "time decay alone does not reach the take profit: hold to expiry or to the stop"
        ),
        "pop": _pop(q.kind, "sell", q.strike, price, spot, vol, hours),
    }


def ideas(
    quotes: list[OptionQuote], spot: float, now: float, vol_buyer: float | None, vol_seller: float | None,
    fee: Callable[[float], float], direction: float, max_spread_pct: float,
    buy_take_profit_pct: float, sell_take_profit_pct: float,
) -> dict[str, Any]:
    """Every liquid contract as a buy and as a sell, only those with an edge, the likeliest to win first
    (then the larger edge per unit of risk).

    `fee(price)` is one leg's fee per unit at that premium. `direction` is the desk's -1..+1 read.
    """
    out, considered = [], 0
    lean = "bullish" if direction >= TREND else "bearish" if direction <= -TREND else "neutral"
    for q in quotes:
        hours = q.hours_left(now)
        spread = q.spread_pct
        if hours <= 0 or spread is None or spread > max_spread_pct:
            continue
        if q.delta is not None and not MIN_ABS_DELTA <= abs(q.delta) <= MAX_ABS_DELTA:
            continue
        considered += 1
        for side, vol, build in (("buy", vol_buyer, _buy), ("sell", vol_seller, _sell)):
            if not vol:
                continue
            idea = build(q, spot, hours, vol, fee((q.ask if side == "buy" else q.bid) or 0.0), buy_take_profit_pct if side == "buy" else sell_take_profit_pct)
            if idea is None or idea["edge"] <= 0:
                continue
            bullish = (q.kind == "call") == (side == "buy")  # a bought call or a sold put
            trend = "neutral" if lean == "neutral" else ("with" if bullish == (lean == "bullish") else "against")
            cv = q.contract_value
            out.append(idea | {
                "symbol": q.symbol, "kind": q.kind, "strike": q.strike, "expiry": q.expiry, "hours_left": hours,
                "iv": q.iv, "delta": q.delta, "bid": q.bid, "ask": q.ask, "mark": q.mark, "spread_pct": spread, "view": "bullish" if bullish else "bearish", "trend": trend,
                "fair": bs_price(q.kind, spot, q.strike, vol, hours), "vol_used": vol,
                "edge_per_lot": idea["edge"] * cv, "max_loss_per_lot": None if idea["max_loss"] is None else idea["max_loss"] * cv,
                "edge_on_risk_pct": 100 * idea["edge"] / idea["risk"],
            })
    out.sort(key=lambda i: (i["pop"] or 0.0, i["edge_on_risk_pct"]), reverse=True)
    return {
        "spot": spot, "lean": lean, "direction": direction, "vol_buyer": vol_buyer, "vol_seller": vol_seller,
        "considered": considered, "ideas": out[:MAX_IDEAS],
        "note": "" if out else f"none of {considered} liquid contracts has an edge after fees at realised volatility",
    }
