"""Zero-hero: the repricer, the five dimensions, the budget, and the exits.

The scanner is pure, so every test here builds the market it wants rather than waiting for one.
The captured chain supplies real quotes; the bars are synthetic because the fixture feed's are a
random walk with no break in them — which is itself the first test below.
"""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from app.analysis import fibonacci, orderflow
from app.analysis import pulse as pulse_mod
from app.analysis import structure as smc_mod
from app.market import options as chain
from app.market.delta import Candle
from app.options import zerohero as zh
from app.options.pricing import bs_price, intrinsic, multiple_at_move, reprice
from app.options.strategies import SELLER_CHECKS, ZERO_HERO, spec_for
from app.settings import Settings
from tests.conftest import load_quotes

SPOT = 80_000.0


def cfg(**changes: object) -> Settings:
    return Settings().model_copy(update=changes)


# -- the repricer ---------------------------------------------------------------------------


def test_put_call_parity_holds_exactly() -> None:
    call = bs_price("call", 80_000, 81_000, 0.45, 3.0)
    put = bs_price("put", 80_000, 81_000, 0.45, 3.0)
    assert call - put == pytest.approx(80_000 - 81_000, abs=1e-6)


def test_at_expiry_the_model_is_intrinsic_value() -> None:
    for spot in (79_000, 80_000, 82_000):
        assert bs_price("call", spot, 81_000, 0.45, 0.0) == intrinsic("call", 81_000, spot)
        assert bs_price("put", spot, 81_000, 0.45, 0.0) == intrinsic("put", 81_000, spot)


def test_repricing_advances_the_clock_as_well_as_the_spot() -> None:
    """The whole point: an OTM option held flat for an hour loses, and the same option on a 1%
    move gains far more than the move itself. A figure that moved the spot and not the clock
    would report the gain and hide the decay."""
    flat = reprice("call", 81_000, SPOT, 0.0, 0.45, 3.0, 60)
    moved = reprice("call", 81_000, SPOT, 1.0, 0.45, 3.0, 60)
    now = bs_price("call", SPOT, 81_000, 0.45, 3.0)
    assert flat < now  # theta took some of it
    assert moved > 4 * now  # gamma paid far more than the 1% move


def test_the_exit_haircut_lowers_the_multiple() -> None:
    args = ("call", 81_000.0, 50.0, SPOT, 1.0, 0.45, 3.0, 60.0)
    assert multiple_at_move(*args, 20.0) == pytest.approx(0.8 * multiple_at_move(*args, 0.0))
    assert multiple_at_move("call", 81_000, 0.0, SPOT, 1.0, 0.45, 3.0, 60) is None


# -- the gate is the strategy's own ------------------------------------------------------------


def test_a_long_buy_is_not_gated_on_a_sellers_checks() -> None:
    assert spec_for(ZERO_HERO).required_checks == ("liquidity",)
    assert spec_for("iron_condor").required_checks == SELLER_CHECKS


@pytest.mark.asyncio
async def test_a_zero_hero_plan_passes_assessment_on_a_low_probability(env) -> None:
    """Its POP is ~10% and its edge at realised vol is negative. Both are reported and neither is
    required, so the assessment is `ok` — a condor at the same POP would not be."""
    from app.options.strategies import single

    spot = env.desk.spot("BTC")
    otm = sorted(
        (q for q in env.feed.quotes("BTC") if q.kind == "call" and q.strike > spot * 1.004 and q.ask),
        key=lambda q: q.strike,
    )[0]
    out = await env.desk.assess(single(otm, "buy", spot, ZERO_HERO), 1)
    names = {c["name"]: c for c in out["checks"]}
    assert names["probability of profit"]["required"] is False
    assert names["probability of profit"]["ok"] is False  # it is still reported, and still false
    assert names["liquidity"]["required"] is True
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_a_zero_hero_buy_and_a_condor_do_not_take_each_others_slots(env) -> None:
    """Invariant 5 extended: each family holds its own slots. A condor at the per-underlying limit
    must not block a zero-hero buy, and the zero-hero buy must not consume the condor's slot."""
    from app.options.strategies import single

    env.configure(max_open_structures=1, max_per_underlying=1)
    spot = env.desk.spot("BTC")
    condor = env.desk.plan("BTC", "iron_condor")
    opened = await env.trading.open("paper", condor, 1, "manual", "r1")
    assert opened["status"] == "open"

    call = sorted(
        (q for q in env.feed.quotes("BTC") if q.kind == "call" and q.strike > spot * 1.004 and q.ask),
        key=lambda q: q.strike,
    )[0]
    zero = single(call, "buy", spot, ZERO_HERO)
    _broker, assessment = await env.trading.gate("paper", zero, 10, "zerohero")
    assert assessment["ok"]  # the condor in the book did not refuse it

    from app.errors import Refused

    with pytest.raises(Refused, match="max"):  # while a second condor still is refused
        await env.trading.gate("paper", condor, 1, "manual")


# -- the momentum trigger -----------------------------------------------------------------------


def quiet_bars(n: int = 200) -> list[Candle]:
    return [Candle(i * 300, 80_000.0, 80_020.0, 79_980.0, 80_000.0, 10.0) for i in range(n)]


def breakout_bars() -> list[Candle]:
    """A flat range, then one bar that closes far through the top of it on heavy volume."""
    bars = [
        Candle(i * 300, 80_000 + (i % 5) * 10, 80_100, 79_900, 80_000 + (i % 5) * 10, 10.0)
        for i in range(180)
    ]
    t = 180 * 300
    bars.append(Candle(t, 80_050, 81_200, 80_040, 81_150, 60.0))  # the displacement bar
    bars.append(Candle(t + 300, 81_150, 81_400, 81_100, 81_350, 40.0))
    return bars


def readings(bars: list[Candle]):
    return (smc_mod.read(bars), pulse_mod.read("BTCUSD", bars), orderflow.read(bars), fibonacci.read(bars))


def test_a_quiet_market_produces_no_direction_and_no_candidates() -> None:
    smc, pulse, flow, fib = readings(quiet_bars())
    out = zh.trigger(smc, pulse, flow, fib, 80_000.0, cfg())
    assert out.direction == 0 and not out.ok


def test_a_fresh_break_with_size_behind_it_arms_the_scan() -> None:
    smc, pulse, flow, fib = readings(breakout_bars())
    out = zh.trigger(smc, pulse, flow, fib, 81_350.0, cfg())
    assert out.direction == 1
    assert out.displacement >= 1.5
    assert {c["name"]: c["ok"] for c in out.checks}["fresh break"] is True


def test_a_break_that_has_gone_stale_is_not_an_entry() -> None:
    """A level that broke an hour ago is true of every bar since, and a condition true every bar
    is not a trigger."""
    bars = breakout_bars() + [
        Candle((182 + i) * 300, 81_350, 81_380, 81_320, 81_350, 10.0) for i in range(zh.MAX_BREAK_AGE + 3)
    ]
    smc, pulse, flow, fib = readings(bars)
    out = zh.trigger(smc, pulse, flow, fib, 81_350.0, cfg())
    assert out.direction == 0
    assert out.bars_ago is not None and out.bars_ago > zh.MAX_BREAK_AGE


# -- positioning is a hard reject ------------------------------------------------------------------


def test_a_trade_running_into_max_pain_is_refused() -> None:
    """Right on direction and still worthless: on expiry day price is pulled to the pin, and a
    call bought below it is fighting that pull with a decaying premium."""
    below = zh.positioning({"max_pain": 81_000}, 80_000, 1, cfg())
    assert below["away_from_pin"] is False
    above = zh.positioning({"max_pain": 79_000}, 80_000, 1, cfg())
    assert above["away_from_pin"] is True
    assert zh.positioning({"max_pain": 79_000}, 80_000, -1, cfg())["away_from_pin"] is False


def test_no_max_pain_is_not_treated_as_permission() -> None:
    assert zh.positioning({}, 80_000, 1, cfg())["away_from_pin"] is False


# -- the window ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("hours", "expected"), [(6.0, False), (4.0, True), (3.0, True), (2.0, True), (1.0, False)])
def test_the_window_opens_and_closes_on_the_clock(hours: float, expected: bool) -> None:
    assert zh.window(hours, cfg())[0] is expected


# -- the whole scan ---------------------------------------------------------------------------------


def scan_with(bars: list[Candle], hours: float, spot: float, **changes: object) -> zh.Scan:
    now = time.time()
    quotes = [replace(q, spot=spot) for q in load_quotes(now, hours)]
    smc, pulse, flow, fib = readings(bars)
    summary = chain.expiry_summary(quotes, spot, now)
    return zh.scan("BTC", quotes, spot, now, summary, smc, pulse, flow, fib, 0.60, cfg(**changes))


def test_a_quiet_chain_inside_the_window_yields_nothing_and_says_which_gate() -> None:
    out = scan_with(quiet_bars(), 3.0, SPOT)
    assert out.window_open and not out.ok and not out.candidates
    failed = [g["name"] for g in out.gates if not g["ok"]]
    assert "momentum" in failed


def test_outside_the_window_nothing_is_scored_at_all() -> None:
    out = scan_with(breakout_bars(), 9.0, 81_350.0)
    assert not out.window_open and not out.candidates
    assert next(g["name"] for g in out.gates if not g["ok"]) == "window"


def test_a_real_break_away_from_the_pin_produces_ranked_candidates() -> None:
    out = scan_with(breakout_bars(), 3.0, 81_350.0, zh_min_ev=-9.0, zh_min_multiple=1.5, zh_min_oi=0.0)
    assert out.trigger["direction"] == 1
    assert all(g["ok"] for g in out.gates), [g for g in out.gates if not g["ok"]]
    assert out.candidates, out.note
    assert all(c["kind"] == "call" for c in out.candidates)  # the direction decides the side
    evs = [c["ev"] for c in out.candidates]
    assert evs == sorted(evs, reverse=True)  # ranked by expected value, best first


def test_every_refusal_names_the_check_that_refused_it() -> None:
    out = scan_with(breakout_bars(), 3.0, 81_350.0, zh_min_ev=-9.0, zh_min_multiple=1.5, zh_min_oi=0.0)
    assert out.rejected
    allowed = {"distance", "payoff", "expected value", "spread", "open interest", "capacity"}
    assert {r["why"] for r in out.rejected} <= allowed


def test_a_far_strike_that_cannot_multiply_is_refused_on_payoff_not_on_price() -> None:
    """The reason to refuse a 3-dollar option two percent out is that it cannot reach its target
    in the time left, not that it is cheap."""
    out = scan_with(breakout_bars(), 3.0, 81_350.0, zh_min_ev=-9.0, zh_min_multiple=50.0, zh_min_oi=0.0)
    assert not out.candidates
    assert {r["why"] for r in out.rejected} <= {"distance", "payoff", "spread", "open interest", "capacity"}


# -- sizing ---------------------------------------------------------------------------------------------


def test_the_budget_is_the_days_and_is_split_across_the_attempts_left() -> None:
    s = cfg(zh_daily_budget_pct=20.0, zh_max_attempts_day=3, zh_max_lots=10**7)
    lots, why = zh.lots_for_budget(10_000.0, 0.0, 0, paid=8.0, contract_value=0.001, capacity_lots=10**9, s=s)
    # 20% of 10,000 is 2,000; the first of three attempts gets a third of it.
    assert lots == int(2_000 / 3 / (8 * 0.001))
    assert "daily budget" in why


def test_the_last_attempt_of_a_quiet_day_may_use_what_the_others_did_not() -> None:
    s = cfg(zh_daily_budget_pct=20.0, zh_max_attempts_day=3, zh_max_lots=10**7)
    first, _ = zh.lots_for_budget(10_000.0, 0.0, 0, 8.0, 0.001, 10**9, s)
    last, _ = zh.lots_for_budget(10_000.0, 0.0, 2, 8.0, 0.001, 10**9, s)
    assert last == pytest.approx(3 * first, rel=1e-4)  # floor rounding aside


def test_a_spent_budget_and_a_used_up_attempt_count_both_refuse() -> None:
    s = cfg(zh_daily_budget_pct=20.0, zh_max_attempts_day=3, zh_max_lots=10**7)
    spent, why = zh.lots_for_budget(10_000.0, 2_000.0, 1, 8.0, 0.001, 10**9, s)
    assert spent == 0 and "budget" in why
    used, why = zh.lots_for_budget(10_000.0, 0.0, 3, 8.0, 0.001, 10**9, s)
    assert used == 0 and "attempts" in why


def test_capacity_clamps_the_budget_not_the_other_way_round() -> None:
    """20% of capital on an eight-dollar option is tens of thousands of lots. The strike does
    not trade tens of thousands of lots, and the budget is not the binding constraint."""
    s = cfg(zh_daily_budget_pct=20.0, zh_max_attempts_day=1, zh_max_lots=10**7)
    lots, why = zh.lots_for_budget(10_000.0, 0.0, 0, 8.0, 0.001, capacity_lots=500, s=s)
    assert lots == 500 and "capacity" in why


def test_the_lot_cap_is_the_last_word() -> None:
    s = cfg(zh_daily_budget_pct=20.0, zh_max_attempts_day=1, zh_max_lots=100)
    lots, why = zh.lots_for_budget(10_000.0, 0.0, 0, 8.0, 0.001, 10**9, s)
    assert lots == 100 and "lot cap" in why


def test_capacity_is_a_share_of_what_the_contract_actually_trades() -> None:
    q = load_quotes()[0]
    q = replace(q, oi=100.0, volume=40.0, contract_value=0.001)
    # The smaller of OI and volume, at the configured share, in lots.
    assert zh.capacity(q, cfg(zh_capacity_share_pct=10.0)) == int(40.0 * 0.1 / 0.001)
    assert zh.capacity(replace(q, oi=0.0, volume=0.0), cfg()) == 0


def test_premium_this_cheap_needs_far_more_lots_than_a_spread_does() -> None:
    """One lot of an eight-dollar BTC option is 0.008 dollars. Deploying a 20% daily budget on a
    10,000 account therefore takes about 83,000 lots — which is why this strategy has its own cap
    and why the ordinary `max_lots` of 50 would make it undeployable without ever saying so."""
    s = cfg(zh_daily_budget_pct=20.0, zh_max_attempts_day=3, zh_max_lots=10**7)
    lots, _ = zh.lots_for_budget(10_000.0, 0.0, 0, 8.0, 0.001, 10**9, s)
    assert lots == pytest.approx(83_333, rel=0.01)
    assert lots * 8.0 * 0.001 == pytest.approx(10_000 * 0.20 / 3, rel=0.01)
    assert Settings().max_lots < lots  # the seller's cap could never reach it


# -- exits ------------------------------------------------------------------------------------------------


class FakeZeroHero:
    """Stands in for the real one in the guard: `invalidated` is the only method it calls."""

    def __init__(self, answer: str = "") -> None:
        self.answer = answer

    def invalidated(self, underlying: str, kind: str) -> str:
        return self.answer


def zh_structure(expiry: float, **changes: object):
    from app.core.models import Leg, Structure

    st = Structure(
        id="szh1", broker="paper", underlying="BTC", strategy=ZERO_HERO, source="zerohero",
        status="open", expiry=expiry, qty=100, entry_spot=SPOT, entry_cash=-10.0,
        take_profit_pct=300.0, stop_loss_pct=50.0, opened_at=time.time(),
    )
    for k, v in changes.items():
        setattr(st, k, v)
    leg = Leg(structure_id="szh1", idx=0, symbol="C-BTC-81000-251026", kind="call", strike=81_000,
              side="buy", size=100, contract_value=0.001, entry_price=10.0)
    return st, [leg]


def test_a_zero_hero_buy_leaves_at_whichever_time_stop_comes_first(env) -> None:
    from app.trading.guard import Guard

    now = time.time()
    guard = Guard(env.trading, env.settings, env.hub, FakeZeroHero())
    s = env.settings().model_copy(update={"exit_before_expiry_minutes": 30, "zh_time_stop_minutes": 90,
                                          "zh_window_end_hours": 2.0})
    assert s.zh_exit_minutes == 90
    st, legs = zh_structure(now + 80 * 60)
    assert "time exit" in (guard.exit_reason(st, legs, s, now) or "")
    # And an ordinary structure is still governed by the global one, not by this strategy's.
    st.strategy = "iron_condor"
    assert guard.exit_reason(st, legs, s, now) is None


def test_the_premise_is_checked_before_the_pnl(env) -> None:
    """A position whose break has been undone is closed on that fact, whether it is green or red."""
    from app.trading.guard import Guard

    now = time.time()
    st, legs = zh_structure(now + 3 * 3600)
    reason = Guard(env.trading, env.settings, env.hub, FakeZeroHero("momentum invalidated: broke down")).exit_reason(
        st, legs, env.settings(), now)
    assert reason == "momentum invalidated: broke down"
    # With the premise intact, the ordinary rules decide again.
    assert Guard(env.trading, env.settings, env.hub, FakeZeroHero()).exit_reason(st, legs, env.settings(), now) != reason


def test_a_debit_takes_profit_at_a_multiple_and_stops_at_a_share_of_what_it_cost() -> None:
    from app.trading.service import exit_thresholds

    st, _ = zh_structure(time.time() + 3600)
    tp, sl = exit_thresholds(st)  # entry_cash -10 means a 10-dollar debit
    assert tp == pytest.approx(30.0)  # +300% of the debit: the contract at 4x
    assert sl == pytest.approx(-5.0)  # -50% of the debit


def test_the_window_cannot_be_set_to_open_inside_its_own_time_stop() -> None:
    from app.errors import SettingsError
    from app.settings import apply_changes

    with pytest.raises(SettingsError, match="time stop"):
        apply_changes(Settings(), {"zh_window_end_hours": 0.5})  # 30 min, inside the 60 min exit


# -- the cycle ------------------------------------------------------------------------------------------------


def zero_hero(env):
    from app.trading.zerohero import ZeroHero

    return ZeroHero(env.desk, env.trading, env.settings, env.hub)


@pytest.mark.asyncio
async def test_the_cycle_does_nothing_at_all_while_it_is_switched_off(env) -> None:
    assert await zero_hero(env).cycle() == "zero-hero is off"


@pytest.mark.asyncio
async def test_every_scan_is_written_down_even_when_it_refuses(env) -> None:
    """Invariant 12: 'why did it not trade?' always has an answer."""
    env.configure(zh_enabled=True)
    zhx = zero_hero(env)
    await zhx.cycle()
    rows = zhx.decisions()
    assert len(rows) == 1
    assert rows[0]["strategy"] == ZERO_HERO
    assert rows[0]["action"] == "skip" and rows[0]["reason"]
    assert rows[0]["regime"]["gates"]  # which of the five refused it


@pytest.mark.asyncio
async def test_zero_hero_decisions_do_not_appear_on_the_auto_traders_page(env) -> None:
    from app.trading.autotrader import AutoTrader

    env.configure(zh_enabled=True)
    await zero_hero(env).cycle()
    assert zero_hero(env).decisions()  # on its own page
    assert not AutoTrader.decisions()  # and not on the condor desk's


@pytest.mark.asyncio
async def test_shadow_mode_records_the_size_it_would_have_bought_and_sends_no_order(env, monkeypatch) -> None:
    env.configure(zh_enabled=True, zh_shadow=True)
    zhx = zero_hero(env)

    async def never(*args, **kwargs):
        raise AssertionError("shadow mode must not open anything")

    monkeypatch.setattr(env.trading, "open", never)
    # Force a decision past the scan by handing it a candidate directly.
    quotes = env.feed.quotes("BTC")
    spot = env.desk.spot("BTC")
    call = sorted((q for q in quotes if q.kind == "call" and q.strike > spot and q.ask), key=lambda q: q.strike)[0]
    best = {
        "symbol": call.symbol, "kind": "call", "strike": call.strike, "paid": call.ask,
        "contract_value": call.contract_value, "capacity_lots": 10**9, "multiple": 9.0,
        "target_pct": 1.2, "hold_minutes": 60.0, "p_touch": 0.2, "ev": 0.6,
    }
    from app.core.models import Decision
    from app.options.strategies import single

    dec = Decision(ts=time.time(), underlying="BTC", action="skip", strategy=ZERO_HERO)
    plan = single(call, "buy", spot, ZERO_HERO)
    await zhx._open(dec, plan, best, ["paper"], "test", env.settings())
    assert dec.action == "shadow"
    assert dec.orders["paper"]["shadow"] is True
    assert dec.orders["paper"]["qty"] > 0
    assert "SHADOW" in dec.reason


def test_the_record_counts_only_closed_trades_and_never_guesses_a_shadow_outcome(env) -> None:
    out = zero_hero(env).record()
    assert out["opened"] == 0 and out["hit_rate_pct"] is None
    assert "shadow proposals have no outcome" in out["note"]
