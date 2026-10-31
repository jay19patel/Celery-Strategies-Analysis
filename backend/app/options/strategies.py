"""The option structures this desk trades, and how each is built from one expiry's chain.

A builder picks strikes, nothing else: sold strikes by |delta| (or at the money), protective wings
a fixed share of spot further out. It returns a `Plan` whose legs carry the quotes they were picked
from, priced where they would trade — a sale at the bid, a purchase at the ask, the mark only when
that side of the book is empty. Pure, so the dashboard, the auto-trader and the tests build
exactly the same structure from the same chain.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from app.market.options import OptionQuote
from app.options.pricing import LegSpec

# The checks in `assess` that a premium seller must pass. They are the right gate for everything
# in CATALOG and the wrong one for a long momentum buy, whose whole proposition is a low
# probability of a large payoff — so which checks bind is a property of the strategy, not a
# constant. A spec that does not say otherwise is gated as a seller.
SELLER_CHECKS = ("liquidity", "credit / width", "probability of profit", "edge at realised vol")

# The one strategy outside CATALOG that other modules name directly.
ZERO_HERO = "zero_hero"


@dataclass(frozen=True)
class StrategySpec:
    key: str
    label: str
    view: str  # neutral | bullish | bearish | volatile | momentum
    premium: str  # credit | debit
    risk: str  # defined | undefined
    summary: str
    required_checks: tuple[str, ...] = SELLER_CHECKS


CATALOG: dict[str, StrategySpec] = {s.key: s for s in (
    StrategySpec("iron_condor", "Iron Condor", "neutral", "credit", "defined",
                 "Sell an OTM call and put, buy further wings. Profits while price stays between the short strikes."),
    StrategySpec("iron_butterfly", "Iron Butterfly", "neutral", "credit", "defined",
                 "Sell the ATM straddle, buy wings. More credit than a condor, needs price to pin near the strike."),
    StrategySpec("bull_put_spread", "Bull Put Spread", "bullish", "credit", "defined",
                 "Sell an OTM put, buy a lower put. Profits if price holds above the short strike."),
    StrategySpec("bear_call_spread", "Bear Call Spread", "bearish", "credit", "defined",
                 "Sell an OTM call, buy a higher call. Profits if price stays below the short strike."),
    StrategySpec("short_strangle", "Short Strangle", "neutral", "credit", "undefined",
                 "Sell an OTM call and put with no wings. Wide profit zone, unlimited risk beyond it."),
    StrategySpec("short_straddle", "Short Straddle", "neutral", "credit", "undefined",
                 "Sell the ATM call and put. Maximum theta, unlimited risk; needs a very quiet market."),
    StrategySpec("long_straddle", "Long Straddle", "volatile", "debit", "defined",
                 "Buy the ATM call and put. Pays when price moves further than the premium, either way."),
)}

# Structures the system can price, hold and exit, but the options auto-trader does not pick and the
# Settings page does not offer. CATALOG is that menu; this is everything else with a payoff.
#
# zero_hero is deliberately out of CATALOG: it is not scored by `regime`, not built by a `build()`
# call, and its gate is its own (`options/zerohero.py`). Putting it in the menu would have the
# condor desk's regime score decide when to buy lottery tickets, which is not a question that
# score can answer.
EXTRA: dict[str, StrategySpec] = {s.key: s for s in (
    StrategySpec(
        ZERO_HERO, "Zero-Hero Buy", "momentum", "debit", "defined",
        "One OTM option bought on an expiry-day momentum break, out of a daily budget. Most expire "
        "worthless; the few that do not are what pays for them.",
        # A long buy cannot pass a seller's gate and should not be asked to: its POP is low by
        # construction and its expected value at realised vol is negative on a fairly priced chain.
        # What it must still pass is a two-sided book — an 8/10 quote is a 20% loss on contact.
        required_checks=("liquidity",),
    ),
)}


def spec_for(key: str) -> StrategySpec | None:
    """The spec of anything the system can hold, whether or not the auto-trader may pick it."""
    return CATALOG.get(key) or EXTRA.get(key)


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


def _priced(quotes: list[OptionQuote], kind: str) -> list[OptionQuote]:
    return [q for q in quotes if q.kind == kind and q.mark and q.mark > 0]


def _otm_by_delta(quotes: list[OptionQuote], kind: str, spot: float, target: float) -> OptionQuote | None:
    side = [q for q in _priced(quotes, kind) if q.delta is not None and (q.strike > spot if kind == "call" else q.strike < spot)]
    return min(side, key=lambda q: abs(abs(q.delta or 0) - target)) if side else None


def _atm(quotes: list[OptionQuote], spot: float) -> tuple[OptionQuote, OptionQuote] | None:
    calls = {q.strike: q for q in _priced(quotes, "call")}
    puts = {q.strike: q for q in _priced(quotes, "put")}
    both = set(calls) & set(puts)
    if not both:
        return None
    strike = min(both, key=lambda k: abs(k - spot))
    return calls[strike], puts[strike]


def _wing(quotes: list[OptionQuote], kind: str, short_strike: float, width: float) -> OptionQuote | None:
    """The protective option `width` beyond the short strike: the nearest listed strike to that distance,
    and always at least one strike out."""
    if kind == "call":
        out = [q for q in _priced(quotes, kind) if q.strike > short_strike]
        target = short_strike + width
    else:
        out = [q for q in _priced(quotes, kind) if q.strike < short_strike]
        target = short_strike - width
    return min(out, key=lambda q: abs(q.strike - target)) if out else None


# -- builders ---------------------------------------------------------------------------------

Picked = list[tuple[OptionQuote | None, str]]


def _iron_condor(qs: list[OptionQuote], spot: float, delta: float, width: float) -> Picked:
    sc, sp = _otm_by_delta(qs, "call", spot, delta), _otm_by_delta(qs, "put", spot, delta)
    if not sc or not sp:
        return [(None, "")]
    return [(_wing(qs, "put", sp.strike, width), "buy"), (sp, "sell"), (sc, "sell"), (_wing(qs, "call", sc.strike, width), "buy")]


def _iron_butterfly(qs: list[OptionQuote], spot: float, _delta: float, width: float) -> Picked:
    atm = _atm(qs, spot)
    if not atm:
        return [(None, "")]
    c, p = atm
    return [(_wing(qs, "put", p.strike, width), "buy"), (p, "sell"), (c, "sell"), (_wing(qs, "call", c.strike, width), "buy")]


def _short_strangle(qs: list[OptionQuote], spot: float, delta: float, _width: float) -> Picked:
    return [(_otm_by_delta(qs, "put", spot, delta), "sell"), (_otm_by_delta(qs, "call", spot, delta), "sell")]


def _short_straddle(qs: list[OptionQuote], spot: float, _delta: float, _width: float) -> Picked:
    atm = _atm(qs, spot)
    return [(atm[1], "sell"), (atm[0], "sell")] if atm else [(None, "")]


def _long_straddle(qs: list[OptionQuote], spot: float, _delta: float, _width: float) -> Picked:
    atm = _atm(qs, spot)
    return [(atm[1], "buy"), (atm[0], "buy")] if atm else [(None, "")]


def _bull_put_spread(qs: list[OptionQuote], spot: float, delta: float, width: float) -> Picked:
    sp = _otm_by_delta(qs, "put", spot, delta)
    return [(_wing(qs, "put", sp.strike, width), "buy"), (sp, "sell")] if sp else [(None, "")]


def _bear_call_spread(qs: list[OptionQuote], spot: float, delta: float, width: float) -> Picked:
    sc = _otm_by_delta(qs, "call", spot, delta)
    return [(sc, "sell"), (_wing(qs, "call", sc.strike, width), "buy")] if sc else [(None, "")]


BUILDERS: dict[str, Callable[[list[OptionQuote], float, float, float], Picked]] = {
    "iron_condor": _iron_condor,
    "iron_butterfly": _iron_butterfly,
    "short_strangle": _short_strangle,
    "short_straddle": _short_straddle,
    "long_straddle": _long_straddle,
    "bull_put_spread": _bull_put_spread,
    "bear_call_spread": _bear_call_spread,
}


def build(strategy: str, quotes: list[OptionQuote], spot: float, short_delta: float, wing_width_pct: float) -> Plan | None:
    """The structure from one expiry's quotes, or None when the chain cannot make it."""
    if strategy not in BUILDERS:
        raise ValueError(f"unknown strategy {strategy!r}")
    if not quotes or spot <= 0:
        return None
    expiry = quotes[0].expiry
    if any(q.expiry != expiry for q in quotes):
        raise ValueError("build() takes the quotes of one expiry")
    picked = BUILDERS[strategy](quotes, spot, short_delta, spot * wing_width_pct / 100)
    legs = [leg_from_quote(q, side) if q else None for q, side in picked]
    if not legs or any(leg is None for leg in legs):
        return None
    return Plan(strategy=strategy, underlying=quotes[0].underlying, expiry=expiry, spot=spot, legs=tuple(legs))  # type: ignore[arg-type]


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


def single(q: OptionQuote, side: str, spot: float, strategy: str) -> Plan | None:
    """A one-leg structure on a quote the caller already chose. The zero-hero scanner picks its own
    contract from the whole chain against five tests; there is no strike rule to put in BUILDERS."""
    leg = leg_from_quote(q, side)
    return None if leg is None else Plan(strategy=strategy, underlying=q.underlying, expiry=q.expiry, spot=spot, legs=(leg,))
