"""The options arithmetic, checked against closed forms rather than against itself."""

from __future__ import annotations

import math

import pytest

from app.options.pricing import LegSpec, analyze, distribution, net_premium, payoff

CV = 0.001


def leg(kind: str, strike: float, side: str, price: float, size: int = 1) -> LegSpec:
    return LegSpec(kind, strike, side, size, price, CV)


def iron_condor() -> list[LegSpec]:
    return [leg("put", 78_000, "buy", 14), leg("put", 79_400, "sell", 88), leg("call", 81_000, "sell", 80), leg("call", 82_600, "buy", 7)]


def test_iron_condor_profit_loss_and_breakevens_are_exact() -> None:
    legs = iron_condor()
    credit = (88 + 80 - 14 - 7) * CV
    a = analyze(legs, 80_000, 0.25, 18)
    assert net_premium(legs) == pytest.approx(credit)
    assert a["max_profit"] == pytest.approx(credit)
    assert a["max_loss"] == pytest.approx(1_600 * CV - credit)  # the wider wing minus the credit
    assert a["breakevens"] == [pytest.approx(79_400 - 147), pytest.approx(81_000 + 147)]
    assert payoff(legs, 80_000) == pytest.approx(credit)


def test_unbounded_risk_is_reported_as_none() -> None:
    strangle = [leg("put", 79_000, "sell", 50), leg("call", 81_000, "sell", 50)]
    long_straddle = [leg("put", 80_000, "buy", 300), leg("call", 80_000, "buy", 300)]
    assert analyze(strangle, 80_000)["max_loss"] is None
    assert analyze(long_straddle, 80_000)["max_profit"] is None
    assert analyze(long_straddle, 80_000)["max_loss"] == pytest.approx(600 * CV)


def test_expected_payoff_matches_black_scholes() -> None:
    """A long call priced at zero premium is worth its Black-Scholes value (no rates, no drift)."""
    spot, strike, iv, hours = 80_000.0, 81_000.0, 0.4, 48.0
    t = hours / (365 * 24)
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    bs_value = (spot * n(d1) - strike * n(d2)) * CV
    pop, ev = distribution([leg("call", strike, "buy", 0.0)], spot, iv, hours)
    assert ev == pytest.approx(bs_value, rel=0.01)
    assert pop == pytest.approx(n(d2), abs=0.005)  # P(S_T > K) under the same lognormal


def test_no_iv_means_no_probability() -> None:
    assert distribution(iron_condor(), 80_000, None, 18) == (None, None)
    assert analyze(iron_condor(), 80_000)["pop"] is None
