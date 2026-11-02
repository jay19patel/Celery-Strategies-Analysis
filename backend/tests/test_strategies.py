"""Builders over a real captured chain, and the assessment's checks."""

from __future__ import annotations

import pytest

from app.options.assess import assess
from app.options.strategies import CATALOG, build, custom
from app.settings import Settings
from tests.conftest import load_quotes

SPOT = 80_331.1


@pytest.mark.parametrize("key", list(CATALOG))
def test_every_strategy_builds_from_the_chain(key: str) -> None:
    quotes = load_quotes()
    plan = build(key, quotes, SPOT, 0.18, 2.0)
    assert plan is not None
    sides = [leg.side for leg in plan.legs]
    if CATALOG[key].premium == "credit":
        assert "sell" in sides
    if CATALOG[key].risk == "defined" and CATALOG[key].premium == "credit":
        # every sold option has a bought one of the same kind further out
        for s in [leg for leg in plan.legs if leg.side == "sell"]:
            wing = [b for b in plan.legs if b.side == "buy" and b.kind == s.kind]
            assert wing
            assert all((b.strike > s.strike) if s.kind == "call" else (b.strike < s.strike) for b in wing)


def test_condor_shorts_sit_at_the_target_delta_and_out_of_the_money() -> None:
    plan = build("iron_condor", load_quotes(), SPOT, 0.18, 2.0)
    assert plan is not None
    shorts = [leg for leg in plan.legs if leg.side == "sell"]
    for s in shorts:
        assert abs(abs(s.delta or 0) - 0.18) < 0.08
        assert (s.strike > SPOT) if s.kind == "call" else (s.strike < SPOT)
    # priced where they would trade: sold at the bid, bought at the ask
    for leg in plan.legs:
        assert leg.price == ((leg.bid if leg.side == "sell" else leg.ask) or leg.mark)


def test_custom_refuses_mixed_expiries_and_duplicates() -> None:
    quotes = {q.symbol: q for q in load_quotes()}
    later = {q.symbol + "-later": q.__class__(**{**q.__dict__, "symbol": q.symbol + "-later", "expiry": q.expiry + 86_400}) for q in quotes.values()}
    symbol = next(iter(quotes))
    with pytest.raises(ValueError, match="one expiry"):
        custom(quotes | later, [{"symbol": symbol, "side": "buy"}, {"symbol": symbol + "-later", "side": "sell"}], SPOT)
    with pytest.raises(ValueError, match="once"):
        custom(quotes, [{"symbol": symbol, "side": "buy"}, {"symbol": symbol, "side": "sell"}], SPOT)
    with pytest.raises(ValueError, match="no fresh quote"):
        custom(quotes, [{"symbol": "C-BTC-1-010101", "side": "buy"}], SPOT)


def test_selling_premium_needs_edge_at_realised_volatility() -> None:
    """IV here is 22.9%. Realised below it, a condor has edge; realised above it, the check refuses."""
    plan = build("iron_condor", load_quotes(), SPOT, 0.18, 2.0)
    assert plan is not None
    s = Settings(max_spread_pct=100, min_credit_width_pct=0)
    calm = assess(plan, 1, 0.229, 17.6, s, realised_vol=0.12)
    wild = assess(plan, 1, 0.229, 17.6, s, realised_vol=0.45)
    edge = lambda a: next(c for c in a["checks"] if c["name"] == "edge at realised vol")  # noqa: E731
    assert edge(calm)["ok"] and calm["ok"]
    assert not edge(wild)["ok"] and not wild["ok"]


def test_range_volatility_reads_the_same_market_as_closes_and_the_reference_is_the_highest() -> None:
    import math
    import random

    from app.market.indicators import parkinson_vol, realised_vol
    from app.options.regime import measure

    # A walk at 60% annualised, with bars whose high/low come from four sub-steps inside each bar.
    rng, sigma, price, bars = random.Random(3), 0.60 / math.sqrt(365 * 24 * 4 * 4), 80_000.0, []
    for i in range(700):
        path = [price]
        for _ in range(4):
            path.append(path[-1] * math.exp(rng.gauss(0, sigma)))
        bars.append({"time": i * 900, "open": price, "high": max(path), "low": min(path), "close": path[-1], "volume": 1.0})
        price = path[-1]
    closes = [b["close"] for b in bars]
    assert parkinson_vol(bars[-96:], 0.25) == pytest.approx(0.60, rel=0.35)  # sampled ranges read a little low
    assert realised_vol(closes, 0.25) == pytest.approx(0.60, rel=0.15)
    assert parkinson_vol(bars[:10], 0.25) is None  # too few bars to say

    m = measure(bars, price, {"atm_iv": 0.70})
    readings = [v for v in (m["rv_24h"], m["rv_7d"], m["rv_range_24h"]) if v]
    assert m["vol_premium"] == pytest.approx(0.70 - max(readings))


def test_a_long_straddle_is_gated_on_its_edge_not_on_a_sellers_probability_of_profit() -> None:
    """Its POP is under 50% by construction: a 55% floor refused it on every cycle, whatever the edge."""
    plan = build("long_straddle", load_quotes(), SPOT, 0.18, 2.0)
    assert plan is not None
    s = Settings(max_spread_pct=100, min_pop_pct=55)
    moving = assess(plan, 1, 0.229, 17.6, s, realised_vol=0.60)
    pop = next(c for c in moving["checks"] if c["name"] == "probability of profit")
    assert not pop["ok"] and not pop["required"]  # still reported, no longer binding
    assert moving["ok"]
    still = assess(plan, 1, 0.229, 17.6, s, realised_vol=0.10)
    assert not still["ok"]  # a buyer the market does not pay for is still refused, on its edge
