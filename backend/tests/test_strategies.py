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
