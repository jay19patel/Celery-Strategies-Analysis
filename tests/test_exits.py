"""Leaving a trade before its stop: the arithmetic, the four rules, and the guard that runs them.

The first test is the one that matters most, and it asserts that the module CANNOT do the thing
people expect of it: for a driftless walk, holding a bracketed position is worth exactly what
closing it is worth. Everything else here exists because that is true — an exit has to earn its
reason somewhere the coin cannot reach.
"""

from __future__ import annotations

import pytest

from tradebuddy import exits
from tradebuddy.events import Tick
from tradebuddy.guard import exit_params
from tradebuddy.settings import Settings

from .test_pipeline import close_bar, events, make

RV = 0.40  # 40% annualised, roughly what BTC has been doing on the stored bars
COST = 0.14  # 0.05% fee + 0.02% slippage, both ways


def price(**kw):
    base = {"side": "long", "entry": 100.0, "price": 100.0, "stop": 99.0, "target": 102.0,
            "rv_annual": RV, "hours_left": 8.0, "cost_pct": COST}
    return exits.price_position(**(base | kw))


# -- the arithmetic ---------------------------------------------------------------------


def test_holding_is_worth_exactly_what_closing_is_worth():
    """The martingale property, and the whole reason this module has four rules instead of one.

    If EV(hold) ever came out above EV(exit) for a driftless walk, the arithmetic would be wrong
    and the system would be holding on a rounding error it mistook for an edge."""
    p = price()
    assert p.ev_hold == pytest.approx(p.ev_exit, abs=1e-3)
    assert p.edge == pytest.approx(0.0, abs=1e-3)


def test_it_holds_at_every_horizon_and_on_both_sides():
    for hours in (0.5, 2.0, 8.0, 72.0):
        for side, stop, target in (("long", 99.0, 102.0), ("short", 101.0, 98.0)):
            p = price(side=side, stop=stop, target=target, hours_left=hours)
            assert p.ev_hold == pytest.approx(p.ev_exit, abs=1e-3), (side, hours)


def test_the_odds_are_the_ratio_not_the_reward():
    """A 1:3 ticket is a 75% chance of being stopped, and that is what the page was not saying."""
    p = price(stop=99.0, target=103.0, hours_left=72.0)  # 1% risk, 3% reward
    assert p.reward_risk == pytest.approx(3.0, abs=0.01)
    assert p.p_stop / (p.p_stop + p.p_target) == pytest.approx(0.75, abs=0.01)


def test_volatility_cancels_out_of_the_ratio():
    quiet, fast = price(rv_annual=0.10, hours_left=72.0), price(rv_annual=2.0, hours_left=72.0)
    share = lambda p: p.p_stop / (p.p_stop + p.p_target)  # noqa: E731
    assert share(quiet) == pytest.approx(share(fast), abs=0.01)


def test_a_short_horizon_is_where_the_time_barrier_lives():
    """At 72h a 15m position has no chance of timing out, which is why that barrier was decoration."""
    assert price(hours_left=72.0).p_timeout == pytest.approx(0.0, abs=0.01)
    assert price(hours_left=0.5).p_timeout > 0.3


def test_values_are_net_of_what_closing_costs():
    p = price()
    assert p.value_now == pytest.approx(-COST / 2, abs=1e-6)  # the entry half is already paid
    assert p.value_target == pytest.approx(2.0 - COST / 2, abs=1e-6)


def test_a_stop_trailed_into_profit_is_a_smaller_win_not_a_loss():
    """The guard has to tell "my worst case is +6" from "my worst case is -8" (invariant 7b)."""
    p = price(side="short", entry=100.0, price=98.0, stop=99.0, target=96.0)
    assert p.value_stop > 0
    assert p.value_stop < p.value_now < p.value_target


def test_an_unbracketed_position_cannot_be_priced():
    assert price(stop=None) is None and price(target=None) is None


# -- INVALIDATED ------------------------------------------------------------------------


def test_invalidation_closes_and_outranks_everything():
    d = exits.decide(side="long", entry_price=100.0, price=100.0, stop_loss=99.0, take_profit=102.0,
                     held_hours=0.1, rv_annual=RV, cost_pct=COST, invalidation="the 1h turned over")
    assert d.closing and d.rule == exits.INVALIDATED and d.reason == "the 1h turned over"


def test_the_outlook_read_turning_against_a_position_invalidates_it():
    view = {"timeframes": [{"interval": "15m", "direction": -1}, {"interval": "1h", "direction": -1}]}
    assert "turned against this long" in exits.outlook_invalidation("long", view)
    assert exits.outlook_invalidation("short", view) == ""


def test_an_outlook_with_nothing_in_it_invalidates_nothing():
    """Absence over staleness: no reading is not a reading that says close."""
    assert exits.outlook_invalidation("long", {}) == ""
    assert exits.outlook_invalidation("long", {"timeframes": []}) == ""
    assert exits.outlook_invalidation("long", {"timeframes": [{"interval": "1h", "direction": 0}]}) == ""


# -- STALLED ----------------------------------------------------------------------------


def stall(**kw):
    base = {"side": "long", "entry_price": 100.0, "price": 99.5, "stop_loss": 99.0, "take_profit": 102.0,
            "held_hours": 3.0, "rv_annual": RV, "cost_pct": COST, "mae_pct": -0.8, "mfe_pct": 0.1}
    return exits.decide(**(base | kw))


def test_risk_spent_and_no_ground_made_closes_it():
    d = stall()
    assert d.closing and d.rule == exits.STALLED
    assert "has not happened" in d.reason


def test_both_halves_are_needed():
    assert not stall(mae_pct=-0.2).closing  # barely any risk spent
    assert not stall(mfe_pct=1.5).closing  # it did make ground


def test_the_rule_is_scale_free():
    """The same shape of trade on a 0.2% scalp stop and a 4% swing stop is the same decision.

    A fixed percentage would fire on one and not the other, which is how a threshold fitted to BTC
    ends up silently different on ETH."""
    tight = stall(stop_loss=99.8, take_profit=100.4, mae_pct=-0.16, mfe_pct=0.08)
    wide = stall(stop_loss=96.0, take_profit=108.0, mae_pct=-3.2, mfe_pct=1.6)
    assert tight.rule == wide.rule == exits.STALLED


def test_it_waits_before_judging_a_position():
    assert not stall(held_hours=0.2).closing


def test_without_the_excursions_it_abstains():
    """Delta does not record them. Abstaining is not the same as deciding to hold, and the
    detail says which happened so the page can tell them apart."""
    d = stall(mae_pct=None)
    assert not d.closing
    assert d.detail["stalled"]["available"] is False


# -- TIMEOUT ----------------------------------------------------------------------------


def test_the_horizon_running_out_closes_it():
    d = exits.decide(side="long", entry_price=100.0, price=100.0, stop_loss=99.0, take_profit=102.0,
                     held_hours=9.0, rv_annual=RV, cost_pct=COST, mae_pct=-0.1, mfe_pct=0.1)
    assert d.closing and d.rule == exits.TIMEOUT


def test_a_target_out_of_reach_in_the_time_left_closes_it():
    d = exits.decide(side="long", entry_price=100.0, price=100.0, stop_loss=90.0, take_profit=110.0,
                     held_hours=7.9, rv_annual=0.05, cost_pct=COST, mae_pct=-0.1, mfe_pct=0.1)
    assert d.closing and d.rule == exits.TIMEOUT and "capital standing still" in d.reason


def test_a_position_with_room_and_time_is_held():
    d = exits.decide(side="long", entry_price=100.0, price=100.0, stop_loss=99.0, take_profit=102.0,
                     held_hours=1.0, rv_annual=RV, cost_pct=COST, mae_pct=-0.1, mfe_pct=0.3)
    assert not d.closing and d.rule == "" and d.reason


# -- CARRY ------------------------------------------------------------------------------


def test_funding_is_charged_to_the_side_that_pays_it():
    assert exits.carry_cost_pct(0.01, "long", 8.0) == pytest.approx(0.01)
    assert exits.carry_cost_pct(0.01, "short", 8.0) == pytest.approx(-0.01)


def test_a_short_being_paid_to_hold_is_never_closed_for_carry():
    """A rule that treated carry as always adverse would close the one position the market is
    subsidising."""
    d = exits.decide(side="short", entry_price=100.0, price=100.0, stop_loss=101.0, take_profit=98.0,
                     held_hours=1.0, rv_annual=RV, cost_pct=COST, mae_pct=-0.1, mfe_pct=0.3,
                     funding_rate_pct=0.5)
    assert not d.closing


def test_carry_that_eats_the_target_closes_it():
    d = exits.decide(side="long", entry_price=100.0, price=100.0, stop_loss=99.0, take_profit=102.0,
                     held_hours=1.0, rv_annual=RV, cost_pct=COST, mae_pct=-0.1, mfe_pct=0.3,
                     funding_rate_pct=1.5)  # 7h left -> 1.31%, over half the 2% still to win
    assert d.closing and d.rule == exits.CARRY


# -- settings -> params ------------------------------------------------------------------


def test_every_exit_setting_reaches_the_rules():
    s = Settings(exit_horizon_hours=3.0, exit_stalled_adverse=0.6, exit_stalled_progress=0.1,
                 exit_stalled_min_hours=2.0, exit_timeout_min_resolve=0.4, exit_carry_max_share=0.25)
    assert exit_params(s) == exits.Params(3.0, 0.6, 0.1, 2.0, 0.4, 0.25)


# -- the guard ---------------------------------------------------------------------------


@pytest.fixture
async def paper(cfg, exchange):
    exchange.spec = {"id": 27, "contract_value": 0.001, "tick_size": 0.01}
    s = await make(cfg, exchange)
    await s.update_settings({"paper_slippage_pct": 0})
    s.guard.vol_of = lambda _symbol: RV  # the outlook cache is empty in a test engine
    yield s
    await s.bus.stop()


async def guard(system):
    await system.guard.check()
    await system.drain()


async def test_it_is_off_until_switched_on(paper):
    """Invariant 1. It overrides the protection the entry was placed with, so it waits to be asked."""
    assert Settings().early_exit_enabled is False
    await close_bar(paper)  # long at 100, SL 99, TP 102
    paper.guard.view_of = lambda _s: _view_against()
    await guard(paper)
    assert len(await paper.paper.positions()) == 1
    assert events(paper, "PositionExitEarly") == []


async def test_the_odds_are_read_even_when_nothing_acts(paper):
    """A 1:3 ticket that is really a 75% chance of being stopped is worth showing whether or not
    anything is armed to act on it."""
    await close_bar(paper)
    await guard(paper)
    reading = paper.guard.exits[("paper", "BTCUSD")]
    assert reading["armed"] is False and reading["action"] == exits.HOLD
    assert 0 < reading["pricing"]["p_stop"] < 1


async def test_an_armed_guard_closes_and_says_why(paper):
    await paper.update_settings({"early_exit_enabled": True})
    await close_bar(paper)
    await guard(paper)
    paper.guard.view_of = lambda _s: _view_against()
    await guard(paper)
    assert await paper.paper.positions() == []
    [e] = events(paper, "PositionExitEarly")
    assert e["rule"] == exits.INVALIDATED and e["symbol"] == "BTCUSD"
    assert e["pricing"]["p_stop"] > 0  # the odds it fired on, kept so it can be rechecked


async def test_the_exit_is_asked_before_the_trail(paper):
    """Trailing a stop on a position about to close would log a protection change nobody acted on,
    immediately before the exit that overrode it."""
    await paper.update_settings({"early_exit_enabled": True})
    await close_bar(paper)
    await guard(paper)
    paper.guard.view_of = lambda _s: _view_against()
    paper.bus.publish(Tick(symbol="BTCUSD", price=101.7))  # well past the trailing trigger
    await paper.drain()
    await guard(paper)
    assert await paper.paper.positions() == []
    assert events(paper, "ProtectionTrailed") == []


async def test_no_measured_volatility_means_no_reading(paper):
    """Invariant 8: a horizon priced on a guessed volatility is worse than no price at all."""
    await paper.update_settings({"early_exit_enabled": True})
    paper.guard.vol_of = lambda _symbol: None
    await close_bar(paper)
    await guard(paper)
    assert len(await paper.paper.positions()) == 1
    assert paper.guard.exits == {}


async def test_a_reading_that_blows_up_never_stops_the_rest_of_the_guard(paper):
    await paper.update_settings({"early_exit_enabled": True})
    await close_bar(paper)

    def boom(_symbol):
        raise RuntimeError("outlook exploded")

    paper.guard.view_of = boom
    await guard(paper)
    assert len(await paper.paper.positions()) == 1  # still there, still protected
    assert paper.guard.status["paper"]["error"] == ""


async def test_a_closed_position_leaves_no_stale_reading(paper):
    await paper.update_settings({"early_exit_enabled": True})
    await close_bar(paper)
    await guard(paper)
    assert ("paper", "BTCUSD") in paper.guard.exits
    paper.guard.view_of = lambda _s: _view_against()
    await guard(paper)
    assert ("paper", "BTCUSD") not in paper.guard.exits


async def _view_against():
    """The engine's read turned against a long — the permission the entry needed is gone."""
    return {"timeframes": [{"interval": "15m", "direction": -1}, {"interval": "1h", "direction": -1}]}
