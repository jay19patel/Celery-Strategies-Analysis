"""Builders over a real captured chain, and the assessment's checks."""

from __future__ import annotations

import pytest

from app.options.assess import assess
from app.options.strategies import Plan, build_plan, custom
from app.settings import Settings
from app.strategies import registry
from app.strategies.iron_condor import IronCondor
from tests.conftest import load_quotes

SPOT = 80_331.1
STRIKE_RULES = [n for n in registry.names("options") if n not in ("expiry_decay", "long_option", "expiry_hero")]  # these build from the context: test_decay, test_option_buyers


def build(key: str, quotes: list, spot: float = SPOT) -> Plan | None:
    strategy = registry.make(key)
    return build_plan(key, strategy.pick(quotes, spot), spot)


def check(a: dict, name: str) -> dict:
    return next(c for c in a["checks"] if c["name"] == name)


@pytest.mark.parametrize("key", STRIKE_RULES)
def test_every_strategy_builds_from_the_chain(key: str) -> None:
    plan = build(key, load_quotes())
    assert plan is not None and plan.strategy == key
    sides = [leg.side for leg in plan.legs]
    cls = registry.option(key)
    assert cls is not None
    if cls.premium == "credit":
        assert "sell" in sides
    if cls.risk == "defined" and cls.premium == "credit":
        # every sold option has a bought one of the same kind further out
        for s in [leg for leg in plan.legs if leg.side == "sell"]:
            wing = [b for b in plan.legs if b.side == "buy" and b.kind == s.kind]
            assert wing
            assert all((b.strike > s.strike) if s.kind == "call" else (b.strike < s.strike) for b in wing)


def test_condor_shorts_sit_at_the_target_delta_and_out_of_the_money() -> None:
    plan = build("iron_condor", load_quotes())
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
    plan = build("iron_condor", load_quotes())
    assert plan is not None
    calm = assess(plan, 1, 0.229, 17.6, 100, realised_vol=0.12, min_credit_width_pct=0)
    wild = assess(plan, 1, 0.229, 17.6, 100, realised_vol=0.45, min_credit_width_pct=0)
    assert check(calm, "edge at realised vol")["ok"] and calm["ok"]
    assert not check(wild, "edge at realised vol")["ok"] and not wild["ok"]


def test_no_realised_volatility_fails_the_edge_check_instead_of_skipping_it() -> None:
    """A fresh install has no stored bars: the seller's one real edge test must refuse, not vanish."""
    plan = build("iron_condor", load_quotes())
    assert plan is not None
    blind = assess(plan, 1, 0.229, 17.6, 100, realised_vol=None, min_credit_width_pct=0, min_pop_pct=0)
    edge = check(blind, "edge at realised vol")
    assert edge["required"] and not edge["ok"] and not blind["ok"]


def test_a_strategys_params_move_its_strikes() -> None:
    quotes = load_quotes()
    near = build_plan("iron_condor", IronCondor({"short_delta": 0.30}).pick(quotes, SPOT), SPOT)
    far = build_plan("iron_condor", IronCondor({"short_delta": 0.10}).pick(quotes, SPOT), SPOT)
    assert near is not None and far is not None
    sold = lambda p: next(leg.strike for leg in p.legs if leg.side == "sell" and leg.kind == "call")  # noqa: E731
    assert sold(near) < sold(far)  # a higher |delta| sells closer to the money
    with pytest.raises(ValueError, match="short_delta"):
        IronCondor({"short_delta": 0.9})


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


def test_an_iron_butterfly_is_gated_on_its_edge_not_on_a_sellers_probability_of_profit() -> None:
    """Its breakevens hug the strike, so its POP is under 50% by construction: still reported, not binding."""
    plan = build("iron_butterfly", load_quotes())
    assert plan is not None
    fly = registry.make("iron_butterfly")
    a = assess(plan, 1, 0.229, 17.6, 100, realised_vol=0.10, required=fly.required_checks, min_pop_pct=95, min_credit_width_pct=0)
    pop = check(a, "probability of profit")
    assert not pop["ok"] and not pop["required"]


def test_gold_is_an_underlying_and_its_options_settle_at_its_own_hour() -> None:
    """XAUT options settle at 16:00 UTC, BTC's at 12:00: one hour for every chain put gold's expiry
    four hours early — wrong time left, wrong exits."""
    from datetime import UTC, datetime

    from app.market.options import PERPETUALS, parse_symbol

    assert PERPETUALS["XAUT"] == "XAUTUSD"
    assert Settings(underlyings=["BTC", "ETH", "XAUT"]).underlyings == ["BTC", "ETH", "XAUT"]
    _, underlying, strike, expiry = parse_symbol("C-XAUT-4120-051126")
    assert (underlying, strike) == ("XAUT", 4120.0)
    assert datetime.fromtimestamp(expiry, UTC) == datetime(2026, 11, 5, 16, tzinfo=UTC)
    assert datetime.fromtimestamp(parse_symbol("C-BTC-80000-051126")[3], UTC).hour == 12


def test_a_falling_market_scores_the_bear_call_spread_and_a_rising_one_the_bull_put() -> None:
    m = {"atm_iv": 0.45, "vol_premium": 0.04, "vol_premium_floor": 0.10, "rv_ratio": 1.0, "max_pain_distance_pct": 5.0, "implied_move_pct": 2.0}
    falling = {"metrics": m, "direction": -0.6, "label": "trending down", "gamma": None}
    rising = {"metrics": m, "direction": 0.6, "label": "trending up", "gamma": None}
    bear, bull = registry.make("bear_call_spread"), registry.make("bull_put_spread")
    assert bear.score(falling)[0] > bull.score(falling)[0]
    assert bull.score(rising)[0] > bear.score(rising)[0]
    condor = registry.make("iron_condor")
    assert condor.score(falling)[0] < condor.score(falling | {"direction": 0.0, "label": "range"})[0]  # a trend hurts a neutral structure


def test_a_buyer_is_judged_on_the_lowest_realised_reading_and_a_seller_on_the_highest() -> None:
    """IV 29.5% against realised 25% (7d) and 250% (one day's range): read the wrong way, one wild day
    made a straddle look 220 vol points cheap."""
    from app.options import regime

    bars = [{"time": i * 900, "open": 2_000.0, "high": 2_001.0, "low": 1_999.0, "close": 2_000.0 * (1.0008 if i % 2 else 1.0)} for i in range(700)]
    bars[-40]["high"] = 2_400.0  # one junk wick in the last day
    m = regime.measure(bars, 2_000.0, {"atm_iv": 0.295})
    assert m["rv_range_24h"] > 1.0 > m["rv_7d"]
    [(buyer, buy_pts)] = regime.premium(m, sell=False)
    [(_seller, sell_pts)] = regime.premium(m, sell=True)
    assert "premium" in buyer and buy_pts < 0  # IV above the quiet reading: no discount to buy
    assert sell_pts < 0  # and far below the wild one: nothing to sell either


def test_the_har_forecast_leans_a_quiet_day_back_towards_the_month() -> None:
    """Volatility clusters and mean-reverts: after a month at ~60% and one quiet day, tomorrow is
    forecast well above the quiet day — which is why a seller is judged against it too."""
    import math
    import random

    from app.market.indicators import har_forecast, realised_vol

    rng = random.Random(7)
    bars, price = [], 2_000.0
    for i in range(96 * 120):
        quiet = i >= 96 * 119
        price *= math.exp(rng.gauss(0, (0.10 if quiet else 0.60) / math.sqrt(365 * 96)))
        bars.append({"time": i * 900, "close": price})
    last_day = realised_vol([b["close"] for b in bars[-97:]], 0.25)
    forecast = har_forecast(bars, 0.25)
    assert last_day is not None and forecast is not None
    assert last_day < 0.2 < forecast < 0.8
    assert har_forecast(bars[: 96 * 30], 0.25) is None  # a month is too little to fit on
