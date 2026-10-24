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
    env.feed.scale_marks(4.0)
    await guard.check_structures()
    stopped = env.trading.structure(lose["id"])
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
