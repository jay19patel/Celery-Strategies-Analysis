"""How an option structure is built from one expiry's chain: the strike pickers every structure
shares, the `Plan` they produce, and a structure hand-picked leg by leg.

Which structures exist is not decided here: each is one file in `app/strategies/` that picks its
strikes with these helpers. A plan's legs carry the quotes they were picked from, priced where they
would trade — a sale at the bid, a purchase at the ask, the mark only when that side of the book is
empty. Pure, so the dashboard, the auto-trader and the tests build exactly the same structure from
the same chain.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.market.options import OptionQuote
from app.options.pricing import LegSpec

# The checks in `assess` that a premium seller must pass. A strategy says which bind for it
# (`OptionStrategy.required_checks`); a hand-built structure is gated as a seller.
SELLER_CHECKS = ("liquidity", "credit / width", "probability of profit", "edge at realised vol")

Picked = list[tuple[OptionQuote | None, str]]


def describe(legs: Any) -> str:
    """A hand-built structure named by what it holds — "Buy 80,600 Call + Sell 82,000 Call" — since
    "Custom" says nothing about which option was taken."""
    parts = dict.fromkeys(f"{leg.side.title()} {leg.strike:,.0f} {leg.kind.title()}" for leg in legs)
    return " + ".join(parts) or "Custom"


@dataclass(frozen=True)
class PlanLeg:
    symbol: str
    kind: str
    strike: float
    side: str  # buy | sell
    ratio: int
    price: float  # expected fill per unit: bid for a sale, ask for a purchase, else the mark
    mark: float | None
    bid: float | None
    ask: float | None
    iv: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    contract_value: float
    spread_pct: float | None

    def spec(self, qty: int, price: float | None = None) -> LegSpec:
        return LegSpec(
            kind=self.kind, strike=self.strike, side=self.side, size=self.ratio * qty,
            price=self.price if price is None else price, contract_value=self.contract_value,
            delta=self.delta, gamma=self.gamma, theta=self.theta, vega=self.vega,
        )


@dataclass(frozen=True)
class Plan:
    strategy: str
    underlying: str
    expiry: float
    spot: float
    legs: tuple[PlanLeg, ...]

    def specs(self, qty: int) -> list[LegSpec]:
        return [leg.spec(qty) for leg in self.legs]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"legs": [asdict(leg) for leg in self.legs]}


def leg_from_quote(q: OptionQuote, side: str, ratio: int = 1) -> PlanLeg | None:
    price = (q.bid if side == "sell" else q.ask) or q.mark
    if not price or price <= 0:
        return None
    return PlanLeg(
        symbol=q.symbol, kind=q.kind, strike=q.strike, side=side, ratio=ratio, price=price,
        mark=q.mark, bid=q.bid, ask=q.ask, iv=q.iv, delta=q.delta, gamma=q.gamma, theta=q.theta, vega=q.vega,
        contract_value=q.contract_value, spread_pct=q.spread_pct,
    )


# -- strike pickers ---------------------------------------------------------------------------


def priced(quotes: list[OptionQuote], kind: str) -> list[OptionQuote]:
    return [q for q in quotes if q.kind == kind and q.mark and q.mark > 0]


def otm_by_delta(quotes: list[OptionQuote], kind: str, spot: float, target: float) -> OptionQuote | None:
    side = [q for q in priced(quotes, kind) if q.delta is not None and (q.strike > spot if kind == "call" else q.strike < spot)]
    return min(side, key=lambda q: abs(abs(q.delta or 0) - target)) if side else None


def atm(quotes: list[OptionQuote], spot: float) -> tuple[OptionQuote, OptionQuote] | None:
    calls = {q.strike: q for q in priced(quotes, "call")}
    puts = {q.strike: q for q in priced(quotes, "put")}
    both = set(calls) & set(puts)
    if not both:
        return None
    strike = min(both, key=lambda k: abs(k - spot))
    return calls[strike], puts[strike]


def wing(quotes: list[OptionQuote], kind: str, short_strike: float, width: float) -> OptionQuote | None:
    """The protective option `width` beyond the short strike: the nearest listed strike to that distance,
    and always at least one strike out."""
    if kind == "call":
        out = [q for q in priced(quotes, kind) if q.strike > short_strike]
        target = short_strike + width
    else:
        out = [q for q in priced(quotes, kind) if q.strike < short_strike]
        target = short_strike - width
    return min(out, key=lambda q: abs(q.strike - target)) if out else None


# -- plans ------------------------------------------------------------------------------------


def build_plan(strategy: str, picked: Picked, spot: float) -> Plan | None:
    """The structure from the strikes a strategy picked on one expiry, or None when one is missing."""
    if not picked or spot <= 0 or any(q is None for q, _ in picked):
        return None
    quotes = [q for q, _ in picked if q is not None]
    if len({q.expiry for q in quotes}) != 1:
        raise ValueError("a structure takes the quotes of one expiry")
    legs = [leg_from_quote(q, side) for q, side in picked if q is not None]
    if any(leg is None for leg in legs):
        return None
    return Plan(strategy=strategy, underlying=quotes[0].underlying, expiry=quotes[0].expiry, spot=spot, legs=tuple(legs))  # type: ignore[arg-type]


def custom(quotes: dict[str, OptionQuote], legs: list[dict[str, Any]], spot: float) -> Plan:
    """A structure the trader picked leg by leg from the chain: [{"symbol", "side", "ratio"}]."""
    if not legs:
        raise ValueError("pick at least one leg")
    if len(legs) > 6:
        raise ValueError("at most 6 legs")
    out: list[PlanLeg] = []
    for raw in legs:
        q = quotes.get(str(raw.get("symbol", "")))
        if q is None:
            raise ValueError(f"no fresh quote for {raw.get('symbol')!r}")
        side = raw.get("side")
        if side not in ("buy", "sell"):
            raise ValueError("each leg's side must be buy or sell")
        ratio = int(raw.get("ratio") or 1)
        if not 1 <= ratio <= 10:
            raise ValueError("a leg's ratio must be 1 to 10")
        leg = leg_from_quote(q, side, ratio)
        if leg is None:
            raise ValueError(f"{q.symbol} has no price to trade at")
        out.append(leg)
    # One expiry: the payoff, max loss and breakevens are all priced at a single settlement.
    expiries = {quotes[leg.symbol].expiry for leg in out}
    underlyings = {quotes[leg.symbol].underlying for leg in out}
    if len(underlyings) != 1 or len(expiries) != 1:
        raise ValueError("every leg must share one underlying and one expiry")
    if len({leg.symbol for leg in out}) != len(out):
        raise ValueError("each contract can appear once")
    return Plan(strategy="custom", underlying=underlyings.pop(), expiry=expiries.pop(), spot=spot, legs=tuple(out))
