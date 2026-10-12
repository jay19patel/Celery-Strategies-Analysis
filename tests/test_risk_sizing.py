"""What decides the size of a trade, how far its stop sits, and when it is not worth taking.

The stop decides the size (risk_per_trade_pct), the market's volatility decides the stop
(atr_protection), and a target that cannot cover the round trip is refused (min_edge_multiple).
"""

from __future__ import annotations

import time

import pytest

from tradebuddy.settings import Settings
from tradebuddy.trading import ATR_MAX_AGE, atr_pct_from

from .test_pipeline import close_bar, events, paper, skipped  # noqa: F401 - paper is a fixture

# The paper account these tests start from: 1,000 at 10x, a contract of 0.001 BTC, price 100.
# So one contract is 0.1 of notional, and n contracts of notional N is N / 0.1.
CONTRACT_NOTIONAL = 0.1

FIXED = {"atr_protection": False}  # the fixed percentages, so sizes are arithmetic not market data


def order(system):
    [o] = events(system, "OrderRequested")
    return o


# -- the stop decides the size -------------------------------------------------------


async def test_the_stop_decides_the_size_not_the_available_margin(paper):  # noqa: F811
    await paper.update_settings(FIXED | {"risk_per_trade_pct": 1.0, "stop_loss_pct": 2.0})
    await close_bar(paper)
    # Risking 1% of 1,000 behind a 2% stop is a 500 position: the stop costs 10 either way.
    [pos] = await paper.paper.positions()
    assert pos.size == 500 / CONTRACT_NOTIONAL


async def test_a_wider_stop_buys_a_smaller_position(paper):  # noqa: F811
    await paper.update_settings(FIXED | {"risk_per_trade_pct": 1.0, "stop_loss_pct": 4.0})
    await close_bar(paper)
    [pos] = await paper.paper.positions()
    assert pos.size == 250 / CONTRACT_NOTIONAL  # half the stop distance, half the position


async def test_the_margin_ceiling_still_caps_the_size(paper):  # noqa: F811
    """trade_margin_pct is the ceiling, not the driver: risk sizing never passes it."""
    await paper.update_settings(FIXED | {"risk_per_trade_pct": 10.0, "stop_loss_pct": 0.5, "trade_margin_pct": 1.0})
    await close_bar(paper)
    [pos] = await paper.paper.positions()
    # By risk that is a 20,000 position; 1% of 1,000 at 10x allows 100 of notional.
    assert pos.size == 100 / CONTRACT_NOTIONAL


async def test_a_risk_too_small_to_buy_a_contract_is_refused_with_its_reason(paper):  # noqa: F811
    await paper.update_settings(FIXED | {"paper_starting_balance": 1.0, "risk_per_trade_pct": 0.05, "stop_loss_pct": 50.0})
    paper.paper.reset()
    await close_bar(paper)
    assert await paper.paper.positions() == []
    assert any("buys no whole contract" in r for r in skipped(paper))


# -- volatility decides the stop ------------------------------------------------------


def seen(system, symbol="BTCUSD", atr_pct=0.5, age=0.0):
    system.analysis_latest[symbol] = {"ts": time.time() - age, "context": {"atr_pct": atr_pct}}


async def test_the_levels_scale_with_the_atr_the_analyst_measured(paper):  # noqa: F811
    seen(paper, atr_pct=0.5)  # defaults: stop 2x ATR, target 3.5x ATR
    await close_bar(paper)
    o = order(paper)
    assert o["stop_loss"] == pytest.approx(100 * (1 - 0.01))
    assert o["take_profit"] == pytest.approx(100 * (1 + 0.0175))


async def test_a_quiet_market_tightens_the_stop_a_fast_one_widens_it(paper):  # noqa: F811
    seen(paper, atr_pct=0.2)
    await close_bar(paper)
    tight = order(paper)["stop_loss"]
    seen(paper, atr_pct=1.5)
    assert paper.trader.levels("BTCUSD", None, None, paper.settings)[0] > 100 - tight


async def test_a_stale_market_context_is_no_atr():
    """Absence over staleness: an old reading does not describe the market now."""
    now = time.time()
    fresh = {"ts": now, "context": {"atr_pct": 0.4}}
    assert atr_pct_from(fresh, now) == 0.4
    assert atr_pct_from({**fresh, "ts": now - ATR_MAX_AGE - 1}, now) is None
    assert atr_pct_from(None, now) is None
    assert atr_pct_from({"ts": now, "context": {"atr_pct": 0}}, now) is None  # a zero ATR is no reading


async def test_without_an_atr_the_settings_percentages_apply(paper):  # noqa: F811
    await paper.update_settings({"stop_loss_pct": 1.5, "take_profit_pct": 3.0})  # atr_protection stays on
    await close_bar(paper)  # no analyst has run, so there is no ATR
    o = order(paper)
    assert o["stop_loss"] == pytest.approx(98.5) and o["take_profit"] == pytest.approx(103.0)


async def test_atr_levels_are_clamped_to_the_settings_ranges(paper):  # noqa: F811
    seen(paper, atr_pct=30.0)  # 2x -> 60% stop and 3.5x -> 105% target, both past the allowed range
    sl, tp, basis = paper.trader.levels("BTCUSD", None, None, paper.settings)
    assert (sl, tp) == (50.0, 100.0) and "ATR" in basis


async def test_a_strategy_that_sets_its_own_levels_keeps_them(paper):  # noqa: F811
    seen(paper, atr_pct=0.5)
    sl, tp, basis = paper.trader.levels("BTCUSD", 0.8, 1.6, paper.settings)
    assert (sl, tp) == (0.8, 1.6) and basis == "the strategy's own levels"


# -- a trade that cannot pay for itself ------------------------------------------------


def test_the_round_trip_is_both_fees_and_both_slippages():
    from tradebuddy.brokers.paper import PaperBroker

    broker = PaperBroker.__new__(PaperBroker)
    broker.settings = lambda: Settings(paper_fee_pct=0.05, paper_slippage_pct=0.02)
    assert broker.round_trip_cost_pct() == pytest.approx(0.14)


async def test_a_target_inside_the_round_trip_is_refused(paper):  # noqa: F811
    # 0.14% round trip x 3 needs 0.42%; a 0.3% target cannot pay for itself.
    await paper.update_settings(FIXED | {"take_profit_pct": 0.3, "stop_loss_pct": 0.2})
    await close_bar(paper)
    assert await paper.paper.positions() == []
    assert any("cannot pay for itself" in r for r in skipped(paper))
    assert events(paper, "OrderRequested") == []  # refused before anything was reserved


async def test_the_edge_gate_can_be_switched_off(paper):  # noqa: F811
    await paper.update_settings(FIXED | {"take_profit_pct": 0.3, "stop_loss_pct": 0.2, "min_edge_multiple": 0})
    await close_bar(paper)
    assert len(await paper.paper.positions()) == 1
