"""One position per symbol means the strategies compete for one slot. Who should win it.

Routing used to be first-come, which is a race and a biased one: a strategy on a 5-minute bar gets
three times the chances of one on a 15-minute bar, so the FASTEST strategy took the slot whatever
its trade looked like. On the deployed system one strategy that fired on literally every bar took
78% of all signals that way.

That is why the two 5-minute strategies shipped switched off. Fixing the routing is what let them
be switched on, so these tests and `test_strategies.test_every_strategy_arrives_switched_on` are
two halves of one change.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from tradebuddy.settings import Settings
from tradebuddy.strategies import Context, Signal, Strategy
from tradebuddy.system import System

from .conftest import IdleStream
from .test_pipeline import close_bar, events, skipped


class Narrow(Strategy):
    """A tight stop against a near target: 1.5:1 before costs, less after."""

    name = "narrow"
    interval = "1m"
    symbols: ClassVar[tuple[str, ...]] = ("BTCUSD",)
    is_default_sl_tp = False

    async def on_candle(self, ctx: Context) -> Signal | None:
        return Signal("buy", "narrow", stop_loss_pct=1.0, take_profit_pct=1.5)


class Wide(Strategy):
    """The same risk against a target three times further away."""

    name = "wide"
    interval = "1m"
    symbols: ClassVar[tuple[str, ...]] = ("BTCUSD",)
    is_default_sl_tp = False

    async def on_candle(self, ctx: Context) -> Signal | None:
        return Signal("sell", "wide", stop_loss_pct=1.0, take_profit_pct=4.5)


async def build(cfg, exchange, order, **settings):
    """`order` is the order the strategies are evaluated in — i.e. who publishes first."""
    s = System(cfg, strategies=list(order), stream_factory=IdleStream, client_factory=exchange.client)
    s.executor.lookup_delays = (0,)
    s.bus.start()
    if settings:
        await s.update_settings(settings)
    from tradebuddy.events import Tick

    s.bus.publish(Tick(symbol="BTCUSD", price=100.0))
    await s.bus.drain()
    return s


@pytest.fixture
async def ranked(cfg, exchange):
    # Narrow is dispatched FIRST, so under first-come routing it would always win.
    s = await build(cfg, exchange, (Narrow(), Wide()))
    yield s
    await s.bus.stop()


async def test_the_better_trade_wins_not_the_faster_one(ranked):
    await close_bar(ranked)
    [p] = await ranked.paper.positions()
    assert p.strategy == "wide"  # published second, 4.5:1 against narrow's 1.5:1


async def test_the_loser_is_told_what_it_lost_to(ranked):
    await close_bar(ranked)
    [reason] = skipped(ranked, "narrow")
    assert "wide had the better trade" in reason
    assert "after costs" in reason  # the number it was ranked on, not a bare assertion


async def test_only_one_order_is_ever_sent(ranked):
    await close_bar(ranked)
    assert len(events(ranked, "OrderPlaced")) == 1


async def test_dispatch_order_does_not_decide_it(cfg, exchange):
    """The same two strategies the other way round reach the same answer. If they did not, the
    ranking would just be a slower race."""
    s = await build(cfg, exchange, (Wide(), Narrow()))
    try:
        await close_bar(s)
        [p] = await s.paper.positions()
        assert p.strategy == "wide"
    finally:
        await s.bus.stop()


async def test_a_lone_signal_is_not_held_up_by_the_comparison(cfg, exchange):
    s = await build(cfg, exchange, (Wide(),))
    try:
        await close_bar(s)
        assert len(await s.paper.positions()) == 1
        assert skipped(s, "wide") == []
    finally:
        await s.bus.stop()


async def test_zero_restores_first_come(cfg, exchange):
    """An escape hatch that genuinely escapes: at 0 the old behaviour is back, whichever strategy
    publishes first takes the slot."""
    s = await build(cfg, exchange, (Narrow(), Wide()), signal_settle_seconds=0)
    try:
        await close_bar(s)
        [p] = await s.paper.positions()
        assert p.strategy == "narrow"  # dispatched first, and nothing compared it with wide
        # Refused by the first-come gate (the slot is taken or its order is in flight), never by
        # the comparison — the comparison is what this setting turns off.
        assert skipped(s, "wide") and not any("better trade" in r for r in skipped(s, "wide"))
    finally:
        await s.bus.stop()


async def test_a_switched_off_strategy_never_competes(cfg, exchange):
    """A strategy that is off is never evaluated at all (`StrategyRunner.is_on`), so it produces
    no signal to rank — the comparison cannot resurrect it, and cannot be starved by it."""
    s = await build(cfg, exchange, (Narrow(), Wide()))
    try:
        s.set_toggle("strategy:wide", False)
        await close_bar(s)
        [p] = await s.paper.positions()
        assert p.strategy == "narrow"
        assert [e for e in events(s, "SignalGenerated") if e["strategy"] == "wide"] == []
    finally:
        await s.bus.stop()


def test_the_comparison_is_on_by_default_and_range_checked():
    assert Settings().signal_settle_seconds > 0
    from tradebuddy.settings import RANGES

    assert RANGES["signal_settle_seconds"][0] == 0.0  # 0 is allowed: it is the escape hatch
