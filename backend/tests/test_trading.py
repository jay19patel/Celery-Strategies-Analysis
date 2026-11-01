"""Opening, closing and guarding structures on the paper broker, over a real captured chain."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import pytest
from sqlmodel import select

from app.brokers.base import Fill, LegOrder
from app.core.db import session
from app.core.models import OrderRecord, PaperAccount
from app.errors import BrokerError, BrokerTimeout, Refused
from app.trading.guard import Guard
from app.trading.risk import halt_reason

QTY = 3


class Recording:
    """Paper, with every order it is sent written down, and an optional way to fail."""

    name = "paper"

    def __init__(self, paper: Any, fail: Any = None, timeout: Any = None) -> None:
        self.paper, self.fail, self.timeout = paper, fail, timeout
        self.sent: list[LegOrder] = []

    def not_ready(self) -> str:
        return ""

    async def account(self) -> Any:
        return await self.paper.account()

    async def execute(self, order: LegOrder) -> Fill:
        self.sent.append(order)
        if self.fail and self.fail(order):
            raise BrokerError("rejected by the test")
        if self.timeout and self.timeout(order):
            raise BrokerTimeout("no answer")
        return await self.paper.execute(order)

    async def lookup(self, cid: str) -> Fill | None:
        if self.timeout:
            raise BrokerTimeout("still no answer")
        return await self.paper.lookup(cid)

    async def settle(self, order: LegOrder, price: float) -> Fill:
        return await self.paper.settle(order, price)


def condor(env: Any) -> Any:
    env.configure(max_spread_pct=100)
    return env.desk.plan("BTC", "iron_condor")


async def cash(env: Any) -> float:
    return (await env.paper.account()).cash


async def test_open_fills_every_leg_at_the_book_and_books_the_credit(env: Any) -> None:
    start = (await env.paper.account()).cash
    view = await env.trading.open("paper", condor(env), QTY, "manual", "req-open-1")
    assert view["status"] == "open"
    for leg in view["legs"]:
        q = env.feed.quote(leg["symbol"])
        assert leg["entry_price"] == (q.bid if leg["side"] == "sell" else q.ask)
        assert leg["size"] == QTY
    assert view["entry_cash"] > 0 and view["max_loss"] > 0
    assert await cash(env) == pytest.approx(start + view["entry_cash"] - view["fees"])
    account = await env.paper.account()
    assert account.margin_used == pytest.approx(view["max_loss"])


async def test_the_same_request_never_opens_twice(env: Any) -> None:
    plan = condor(env)
    a = await env.trading.open("paper", plan, QTY, "manual", "req-dup-1")
    b = await env.trading.open("paper", plan, QTY, "manual", "req-dup-1")
    assert a["id"] == b["id"]
    with session() as s:
        assert len(s.exec(select(OrderRecord)).all()) == 4


async def test_bought_legs_fill_first_and_sold_legs_close_first(env: Any) -> None:
    rec = Recording(env.paper)
    env.brokers["paper"] = rec
    view = await env.trading.open("paper", condor(env), QTY, "manual", "req-order-1")
    opening = [o.side for o in rec.sent]
    assert opening == sorted(opening)  # every "buy" before any "sell"
    rec.sent.clear()
    closed = await env.trading.close(view["id"], "test")
    assert closed["status"] == "closed"
    closing = [o.side for o in rec.sent]
    assert closing[:2] == ["buy", "buy"] and all(o.reduce_only for o in rec.sent)  # shorts bought back before longs are sold


async def test_closing_books_what_the_valuation_said(env: Any) -> None:
    view = await env.trading.open("paper", condor(env), QTY, "manual", "req-val-1")
    expected = view["live"]["pnl"]
    closed = await env.trading.close(view["id"], "test")
    assert closed["pnl"] == pytest.approx(expected, abs=1e-6)


async def test_a_failed_leg_unwinds_what_filled(env: Any) -> None:
    env.brokers["paper"] = Recording(env.paper, fail=lambda o: o.side == "sell" and not o.reduce_only)
    start = await cash(env)
    view = await env.trading.open("paper", condor(env), QTY, "manual", "req-fail-1")
    assert view["status"] == "failed"
    assert all(leg["entry_price"] is None or leg["exit_price"] is not None for leg in view["legs"])
    assert await cash(env) == pytest.approx(start + view["pnl"])  # the unwind cost is booked, nothing is left open
    assert (await env.paper.account()).margin_used == 0


async def test_an_unknown_outcome_blocks_the_broker_until_resolved(env: Any) -> None:
    import app.trading.service as service

    service.RESOLVE_DELAYS = (0.0,)
    env.brokers["paper"] = Recording(env.paper, timeout=lambda o: o.side == "sell")
    view = await env.trading.open("paper", condor(env), QTY, "manual", "req-unknown-1")
    assert view["status"] == "error" and "unknown" in view["error"]
    env.brokers["paper"] = env.paper
    with pytest.raises(Refused, match="unresolved"):
        await env.trading.open("paper", condor(env), QTY, "manual", "req-unknown-2")


async def test_gates_refuse_with_the_rule_named(env: Any) -> None:
    plan = condor(env)
    await env.trading.open("paper", plan, QTY, "manual", "req-gate-1")
    with pytest.raises(Refused, match="max per underlying"):
        await env.trading.open("paper", plan, QTY, "manual", "req-gate-2")
    env.configure(max_per_underlying=5)
    with pytest.raises(Refused, match="quantity"):
        await env.trading.open("paper", plan, 10_000, "manual", "req-gate-3")
    with pytest.raises(Refused, match="not an active broker"):
        await env.trading.open("delta", plan, QTY, "manual", "req-gate-4")
    env.feed.set_quote(plan.legs[0].symbol, at=time.time() - 3600)
    with pytest.raises(Refused, match="no fresh quote"):
        await env.trading.open("paper", plan, QTY, "manual", "req-gate-5")


async def test_auto_entries_respect_the_minimum_time_to_expiry(env: Any) -> None:
    env.feed.load(hours=6)
    with pytest.raises(Refused, match="minimum"):
        await env.trading.open("paper", condor(env), QTY, "auto", "req-auto-near")


async def test_guard_takes_profit_and_stops_out(env: Any) -> None:
    guard = Guard(env.trading, env.settings, env.hub)
    win = await env.trading.open("paper", condor(env), QTY, "manual", "req-tp-1")
    env.feed.scale_marks(0.2)  # the options decayed: most of the credit is kept
    await guard.check_structures()
    closed = env.trading.structure(win["id"])
    assert closed["status"] == "closed" and closed["close_reason"].startswith("take profit") and closed["pnl"] > 0

    env.feed.load()
    lose = await env.trading.open("paper", condor(env), QTY, "manual", "req-sl-1")
    env.feed.scale_marks(4.0)  # the book blew out with the spot standing still
    for _ in range(env.settings().stop_confirm_checks - 1):
        await guard.check_structures()
        assert env.trading.structure(lose["id"])["status"] == "open"  # not yet: one wide ask is not a loss
    await guard.check_structures()
    stopped = env.trading.structure(lose["id"])
    assert "held 3 checks" in stopped["close_reason"]
    assert stopped["status"] == "closed" and stopped["close_reason"].startswith("stop loss") and stopped["pnl"] < 0


async def test_guard_exits_before_expiry_and_settles_after_it(env: Any) -> None:
    guard = Guard(env.trading, env.settings, env.hub)
    env.feed.load(hours=0.6)
    near = await env.trading.open("paper", condor(env), QTY, "manual", "req-time-1")
    await guard.check_structures()
    assert env.trading.structure(near["id"])["close_reason"].startswith("time exit")

    env.feed.load()
    held = await env.trading.open("paper", condor(env), QTY, "manual", "req-exp-1")
    with session() as s:
        from app.core.models import Structure

        row = s.get(Structure, held["id"])
        row.expiry = time.time() - 60
        s.add(row)
        s.commit()
    await guard.check_structures()
    settled = env.trading.structure(held["id"])
    assert settled["status"] == "closed" and settled["close_reason"].startswith("settled at expiry")
    spot = env.feed.spot("BTC")
    for leg in settled["legs"]:
        intrinsic = max(0.0, spot - leg["strike"]) if leg["kind"] == "call" else max(0.0, leg["strike"] - spot)
        assert leg["exit_price"] == pytest.approx(intrinsic)


async def test_daily_loss_limit_halts_the_broker_and_closes_its_structures(env: Any) -> None:
    guard = Guard(env.trading, env.settings, env.hub)
    view = await env.trading.open("paper", condor(env), QTY, "manual", "req-day-1")
    await guard.check_daily_loss()  # the day starts here
    await cash(env)
    with session() as s:
        acct = s.get(PaperAccount, 1)
        acct.cash -= acct.cash * 0.10  # a 10% drop, over the 5% limit
        s.add(acct)
        s.commit()
    await guard.check_daily_loss()
    assert "equity down" in halt_reason("paper", env.settings().day_timezone)
    assert env.trading.structure(view["id"])["status"] == "closed"
    with pytest.raises(Refused, match="halted"):
        await env.trading.open("paper", condor(env), QTY, "manual", "req-day-2")


async def test_paper_never_fills_beyond_the_limit(env: Any) -> None:
    q = next(iter(env.feed.book.values()))
    q = replace(q, ask=100.0, bid=90.0, mark=95.0)
    env.feed.book[q.symbol] = q
    with pytest.raises(BrokerError, match="beyond the limit"):
        await env.paper.execute(LegOrder("x-1", q.symbol, "buy", 1, 99.0, 0.001))
    fill = await env.paper.execute(LegOrder("x-2", q.symbol, "sell", 1, 85.0, 0.001))
    assert fill.price == 90.0


async def test_the_guard_retries_a_close_that_did_not_finish(env: Any) -> None:
    """A close the broker refused leaves the structure in error; the guard keeps trying until it is flat,
    instead of leaving it outside every stop."""
    view = await env.trading.open("paper", condor(env), QTY, "manual", "req-retry-1")
    env.brokers["paper"] = Recording(env.paper, fail=lambda o: o.reduce_only)
    failed = await env.trading.close(view["id"], "test")
    assert failed["status"] == "error"

    guard = Guard(env.trading, env.settings, env.hub)
    await guard.check_structures()  # still refused: it stays in error, and the guard does not raise
    assert env.trading.structure(view["id"])["status"] == "error"

    env.brokers["paper"] = env.paper
    await guard.check_structures()  # inside the backoff: not retried yet
    assert env.trading.structure(view["id"])["status"] == "error"
    guard._retry.clear()
    await guard.check_structures()
    closed = env.trading.structure(view["id"])
    assert closed["status"] == "closed" and closed["close_reason"].startswith("retrying the exit")


async def test_a_late_settlement_uses_the_price_at_expiry_not_today(env: Any) -> None:
    """With the engine down at expiry, the structure settles on the stored bar at expiry; with no such
    bar yet it waits, and the guard goes on to check every other structure."""
    from app.core.models import Bar, Structure

    guard = Guard(env.trading, env.settings, env.hub)
    held = await env.trading.open("paper", condor(env), QTY, "manual", "req-late-1")
    expiry = int(time.time() - 3 * 3600) // 60 * 60
    with session() as s:
        row = s.get(Structure, held["id"])
        row.expiry = expiry
        s.add(row)
        s.commit()
    assert "1 failed" in await guard.check_structures()
    assert env.trading.structure(held["id"])["status"] == "open"

    then = env.feed.spot("BTC") * 0.9
    with session() as s:
        s.add(Bar(symbol="BTCUSD", interval="1m", time=expiry - 60, open=then, high=then, low=then, close=then))
        s.commit()
    guard._retry.clear()
    await guard.check_structures()
    settled = env.trading.structure(held["id"])
    assert settled["status"] == "closed" and f"{then:,.2f}" in settled["close_reason"]
    for leg in settled["legs"]:
        intrinsic = max(0.0, then - leg["strike"]) if leg["kind"] == "call" else max(0.0, leg["strike"] - then)
        assert leg["exit_price"] == pytest.approx(intrinsic)


async def test_a_hand_built_structure_is_named_by_its_legs(env: Any) -> None:
    """Not "Custom": the dashboard says which option was bought or sold."""
    legs = condor(env).legs[:2]
    plan = env.desk.plan("BTC", "custom", legs=[{"symbol": leg.symbol, "side": leg.side, "ratio": 1} for leg in legs])
    view = await env.trading.open("paper", plan, QTY, "manual", "req-named-1")
    expected = " + ".join(f"{leg.side.title()} {leg.strike:,.0f} {leg.kind.title()}" for leg in legs)
    assert view["label"] == expected
    assert env.trading.structures(status="open")[0]["label"] == expected
    assert (await env.desk.assess(plan, 1))["label"] == expected


async def test_paper_fills_no_more_than_the_touch_shows(env: Any) -> None:
    q = next(iter(env.feed.book.values()))
    env.feed.book[q.symbol] = replace(q, ask=100.0, bid=90.0, mark=95.0, ask_size=2.0, bid_size=0.0)
    fill = await env.paper.execute(LegOrder("d-1", q.symbol, "buy", 5, 101.0, 0.001))
    assert fill.size == 2  # an IOC takes what is shown; the rest of the book is past the limit
    with pytest.raises(BrokerError, match="nothing shown"):
        await env.paper.execute(LegOrder("d-2", q.symbol, "sell", 1, 85.0, 0.001))


async def test_an_entry_the_book_cannot_fill_whole_is_unwound(env: Any) -> None:
    plan = condor(env)
    short = next(leg for leg in plan.legs if leg.side == "sell")
    env.feed.set_quote(short.symbol, bid_size=1.0)
    start = await cash(env)
    view = await env.trading.open("paper", plan, QTY, "manual", "req-depth-1")
    assert view["status"] == "failed" and "filled 1 of 3" in view["close_reason"]
    assert await cash(env) == pytest.approx(start + view["pnl"])
    assert (await env.paper.account()).margin_used == 0


async def test_paper_equity_is_what_closing_would_get_not_the_mark(env: Any) -> None:
    view = await env.trading.open("paper", condor(env), QTY, "manual", "req-eq-1")
    account = await env.paper.account()
    # Equity = cash + the legs at bid (longs) / ask (shorts): the valuation before exit fees.
    live = env.trading.structure(view["id"])["live"]
    assert live["pnl"] < live["pnl_mid"]  # the book is worse than the marks
    assert account.equity == pytest.approx(1000 + live["pnl"] + _exit_fees(env, view), abs=1e-6)


def _exit_fees(env: Any, view: dict[str, Any]) -> float:
    from app.options.assess import leg_fee

    spot = env.feed.spot("BTC")
    total = 0.0
    for leg in view["legs"]:
        q = env.feed.quote(leg["symbol"])
        price = q.bid if leg["side"] == "buy" else q.ask
        total += leg_fee(price, leg["size"] * leg["contract_value"], spot, env.desk.cached_fee(leg["symbol"]))
    return total


async def test_a_stop_that_clears_starts_its_count_again(env: Any) -> None:
    guard = Guard(env.trading, env.settings, env.hub)
    view = await env.trading.open("paper", condor(env), QTY, "manual", "req-sl-reset")
    for _ in range(env.settings().stop_confirm_checks - 1):
        env.feed.scale_marks(4.0)
        await guard.check_structures()
        env.feed.load()  # the spike passes before the stop is confirmed
        await guard.check_structures()
    assert env.trading.structure(view["id"])["status"] == "open"


async def test_a_stop_with_the_spot_past_a_breakeven_closes_at_once(env: Any) -> None:
    guard = Guard(env.trading, env.settings, env.hub)
    view = await env.trading.open("paper", condor(env), QTY, "manual", "req-sl-move")
    upper = max(view["breakevens"]) if view.get("breakevens") else max(leg["strike"] for leg in view["legs"])
    env.feed.set_perp(upper * 1.01)
    env.feed.scale_marks(4.0)
    await guard.check_structures()
    stopped = env.trading.structure(view["id"])
    assert stopped["status"] == "closed" and "past a breakeven" in stopped["close_reason"]


def test_sharpe_and_sortino_of_the_daily_pnl() -> None:
    from app.trading.service import ratios

    assert ratios([5.0]) == {"sharpe": None, "sortino": None}  # one day says nothing
    r = ratios([10.0, -5.0, 10.0, -5.0])
    assert r["sharpe"] == pytest.approx(2.5 / 8.660254 * 365**0.5, rel=1e-3)
    assert r["sortino"] == pytest.approx(2.5 / (50 / 4) ** 0.5 * 365**0.5, rel=1e-3)
    assert ratios([1.0, 2.0])["sortino"] is None  # no losing day: no downside to divide by


async def test_results_are_split_by_the_score_they_were_opened_on(env: Any) -> None:
    view = await env.trading.open("paper", condor(env), QTY, "manual", "req-stats-1")
    await env.trading.close(view["id"], "test")
    stats = env.trading.stats()
    assert [g["score"] for g in stats["by_score"]] == ["manual"]
    assert {"sharpe", "sortino", "max_drawdown", "profit_factor"} <= set(stats["overall"])


async def test_an_unbounded_sale_is_sized_on_a_stress_move_not_its_stop(env: Any) -> None:
    from app.trading.autotrader import unit_risk

    env.configure(max_spread_pct=100)
    a = await env.desk.assess(env.desk.plan("BTC", "short_strangle"), 1)
    analysis = a["analysis"]
    at_stop = abs(analysis["net_premium"]) * env.settings().credit_stop_loss_pct / 100
    assert analysis["max_loss"] is None and analysis["stress_loss"] > at_stop
    assert unit_risk(a, env.settings()) == pytest.approx(analysis["stress_loss"] + analysis["round_trip_fees"])
    bounded = await env.desk.assess(condor(env), 1)
    assert unit_risk(bounded, env.settings()) == pytest.approx(bounded["analysis"]["max_loss"] + bounded["analysis"]["round_trip_fees"])


async def test_real_money_refuses_a_structure_without_a_bounded_loss(env: Any) -> None:
    env.brokers["delta"] = Recording(env.paper)
    env.configure(max_spread_pct=100, paper_active=False, delta_active=True, delta_env="live")
    with pytest.raises(Refused, match="not on real money"):
        await env.trading.gate("delta", env.desk.plan("BTC", "short_strangle"), 1, "manual")
    await env.trading.gate("delta", condor(env), 1, "manual")  # a defined-risk structure passes
    env.configure(delta_env="demo")
    await env.trading.gate("delta", env.desk.plan("BTC", "short_strangle"), 1, "manual")  # demo is not money


async def test_the_valuation_splits_fees_paid_exit_fees_and_the_spread(env: Any) -> None:
    view = await env.trading.open("paper", condor(env), QTY, "manual", "req-split-1")
    live = env.trading.structure(view["id"])["live"]
    assert live["fees_paid"] == pytest.approx(view["fees"], abs=1e-5)
    assert live["exit_fees"] == pytest.approx(_exit_fees(env, view), abs=1e-5)
    spread = live["pnl_mid"] - live["pnl"] - live["exit_fees"]
    assert spread > 0  # buying back shorts at the ask and selling longs at the bid costs the spread
