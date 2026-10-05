"""End to end through the bus: CandleClosed -> signal -> gate -> order, on both brokers."""

from __future__ import annotations

import time
from typing import ClassVar

import pytest

from tradebuddy.delta import DeltaError, DeltaTimeout
from tradebuddy.events import CandleClosed, OrderRequested, SignalGenerated, Tick
from tradebuddy.strategies import Context, Signal, Strategy
from tradebuddy.system import System
from tradebuddy.trading import client_order_id

from .conftest import IdleStream, bars

BAR = (int(time.time()) // 60 - 1) * 60  # the bar that just closed: results for old bars are stale


class AlwaysBuy(Strategy):
    name = "always_buy"
    interval = "1m"
    symbols = ("BTCUSD", "ETHUSD")

    async def on_candle(self, ctx: Context) -> Signal | None:
        return Signal("buy", "test")


class NeedsHistory(Strategy):
    name = "needs_history"
    interval = "1m"
    symbols = ("BTCUSD",)
    lookback = 3
    seen: ClassVar[list] = []

    async def on_candle(self, ctx: Context) -> Signal | None:
        NeedsHistory.seen.append([c.time for c in ctx.candles])
        return None


class Broken(Strategy):
    name = "broken"
    interval = "1m"
    symbols = ("BTCUSD",)

    async def on_candle(self, ctx: Context) -> Signal | None:
        raise ValueError("bad maths")


KEYS = {"delta_api_key": "key-123456789", "delta_api_secret": "secret"}


async def make(cfg, exchange, brokers=("paper",)):
    s = System(cfg, strategies=[AlwaysBuy(), NeedsHistory(), Broken()], stream_factory=IdleStream, client_factory=exchange.client)
    if "delta" in brokers:
        await s.update_settings({"delta_active": True, "paper_active": "paper" in brokers, **KEYS})
    s.executor.lookup_delays = (0,)
    s.bus.start()
    s.bus.publish(Tick(symbol="BTCUSD", price=100.0))
    await s.bus.drain()
    return s


@pytest.fixture
async def paper(cfg, exchange):
    s = await make(cfg, exchange)
    yield s
    await s.bus.stop()


@pytest.fixture
async def delta(cfg, exchange):
    s = await make(cfg, exchange, ("delta",))
    yield s
    await s.bus.stop()


def events(system, kind):
    return system.store.recent_events(500, types=[kind])


def skipped(system, strategy="always_buy"):
    return [e["reason"] for e in events(system, "TradeSkipped") if e["strategy"] == strategy]


async def close_bar(system, bar=BAR, symbol="BTCUSD"):
    system.bus.publish(CandleClosed(symbol=symbol, resolution="1m", bar_time=bar, source="clock"))
    await system.drain()


# -- gate -----------------------------------------------------------------------


async def test_paper_trades_by_default(paper):
    await close_bar(paper)
    assert len(await paper.paper.positions()) == 1


async def test_delta_is_off_by_default(delta, exchange):
    await close_bar(delta)
    assert events(delta, "SignalGenerated") and exchange.placed == []
    assert skipped(delta) == ["trading is switched off"]


async def test_paper_switched_off_records_signal_but_no_order(paper):
    paper.set_toggle("trading:paper", False)
    await close_bar(paper)
    assert events(paper, "SignalGenerated")
    assert skipped(paper) == ["trading is switched off"]
    assert await paper.paper.positions() == []


async def test_strategy_off_is_not_evaluated(paper):
    paper.set_toggle("strategy:always_buy", False)
    await close_bar(paper)
    assert not [e for e in events(paper, "SignalGenerated") if e["strategy"] == "always_buy"]


async def test_pair_off_is_not_evaluated(paper):
    paper.set_toggle("pair:always_buy:BTCUSD", False)
    await close_bar(paper)
    assert not [e for e in events(paper, "SignalGenerated") if e["strategy"] == "always_buy"]


async def test_no_fresh_price_blocks(paper):
    await close_bar(paper, symbol="ETHUSD")  # never ticked
    assert skipped(paper) == ["no fresh live price from the WebSocket"]


# -- paper ----------------------------------------------------------------------


async def test_paper_trade_opens_a_protected_position(paper):
    await close_bar(paper)
    [pos] = await paper.paper.positions()
    assert (pos.symbol, pos.side, pos.strategy) == ("BTCUSD", "long", "always_buy")
    assert pos.stop_loss == pytest.approx(99.0) and pos.take_profit == pytest.approx(102.0)
    order = paper.store.order(client_order_id("paper", "always_buy", "BTCUSD", BAR))
    assert (order["broker"], order["status"]) == ("paper", "filled")


async def test_open_paper_position_blocks_the_next_signal(paper):
    await close_bar(paper)
    await close_bar(paper, bar=BAR + 60)
    assert skipped(paper) == ["a BTCUSD position is already open"]


async def test_empty_account_is_refused_at_sizing(paper):
    await paper.update_settings({"paper_starting_balance": 1})
    paper.paper.reset()
    paper.store.db.execute("UPDATE paper_account SET balance = 0")
    await close_bar(paper)
    assert "buys no whole contract" in skipped(paper)[0]
    assert events(paper, "OrderRequested") == [] and await paper.paper.positions() == []


async def test_paper_rejection_is_an_order_failure(paper):
    paper.bus.publish(OrderRequested(client_order_id="c1", broker="paper", strategy="s", symbol="BTCUSD", side="buy",
                                     size=10**9, price=100.0, stop_loss=99.0, take_profit=None))
    paper.store.reserve_order(client_order_id="c1", broker="paper", strategy="s", symbol="BTCUSD", side="buy",
                              size=10**9, price=100.0, stop_loss=99.0, take_profit=None)
    await paper.drain()
    assert "insufficient margin" in events(paper, "OrderFailed")[0]["error"]
    assert paper.store.order("c1")["status"] == "rejected"


# -- delta ----------------------------------------------------------------------


async def test_delta_trade_sends_one_bracketed_order(delta, exchange):
    delta.set_toggle("trading:delta", True)
    await close_bar(delta)
    [order] = exchange.placed
    assert order["client_order_id"] == client_order_id("delta", "always_buy", "BTCUSD", BAR)
    assert order["stop_loss"] == pytest.approx(99.0) and order["take_profit"] == pytest.approx(102.0)
    assert delta.store.order(order["client_order_id"])["status"] == "filled"


async def test_delta_without_keys_is_blocked(cfg, exchange):
    s = await make(cfg, exchange)
    await s.update_settings({"delta_active": True, "paper_active": False})
    s.set_toggle("trading:delta", True)
    await close_bar(s)
    assert skipped(s) == ["Delta API key and secret are not set (Settings)"]
    await s.bus.stop()


async def test_same_bar_twice_sends_one_order(delta, exchange):
    delta.set_toggle("trading:delta", True)
    await close_bar(delta)
    delta.store.update_order(exchange.placed[0]["client_order_id"], "cancelled")  # clear the in-flight block
    await close_bar(delta)
    assert len(exchange.placed) == 1
    assert skipped(delta) == ["an order for this bar was already sent"]


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda x: x.open_positions.append({"product_symbol": "BTCUSD", "size": 1, "entry_price": 1, "mark_price": 1}), "a BTCUSD position is already open"),
        (lambda x: setattr(x, "positions_error", DeltaError("down")), "could not read positions: down"),
    ],
)
async def test_delta_gates(delta, exchange, setup, reason):
    delta.set_toggle("trading:delta", True)
    setup(exchange)
    await close_bar(delta)
    assert exchange.placed == []
    assert skipped(delta) == [reason]


async def test_timeout_is_looked_up_never_resent(delta, exchange):
    delta.set_toggle("trading:delta", True)
    exchange.place_error = DeltaTimeout("read timeout")
    cid = client_order_id("delta", "always_buy", "BTCUSD", BAR)
    exchange.lookup[cid] = {"id": 9, "state": "closed"}
    await close_bar(delta)
    for task in list(delta.executor._lookups):
        await task
    await delta.drain()
    assert len(exchange.placed) == 1
    assert events(delta, "OrderUnknown") and events(delta, "OrderPlaced")
    assert delta.store.order(cid)["status"] == "filled"


async def test_unresolved_timeout_keeps_symbol_blocked(delta, exchange):
    delta.set_toggle("trading:delta", True)
    exchange.place_error = DeltaTimeout("read timeout")
    exchange.lookup[client_order_id("delta", "always_buy", "BTCUSD", BAR)] = DeltaTimeout("still down")
    await close_bar(delta)
    for task in list(delta.executor._lookups):
        await task
    await close_bar(delta, bar=BAR + 60)
    assert len(exchange.placed) == 1
    assert "still unknown" in skipped(delta)[0]


async def test_delta_rejection_is_recorded(delta, exchange):
    delta.set_toggle("trading:delta", True)
    exchange.place_error = DeltaError("insufficient_margin")
    await close_bar(delta)
    assert events(delta, "OrderFailed")[0]["error"] == "insufficient_margin"


async def test_one_signal_trades_on_every_active_broker(cfg, exchange):
    s = await make(cfg, exchange, ("paper", "delta"))
    s.set_toggle("trading:delta", True)
    await close_bar(s)
    [paper_pos] = await s.paper.positions()
    [delta_order] = exchange.placed
    assert paper_pos.symbol == delta_order["symbol"] == "BTCUSD"
    assert delta_order["client_order_id"] == client_order_id("delta", "always_buy", "BTCUSD", BAR)
    assert s.store.order(client_order_id("paper", "always_buy", "BTCUSD", BAR))["broker"] == "paper"
    await s.bus.stop()


async def test_brokers_gate_independently(cfg, exchange):
    s = await make(cfg, exchange, ("paper", "delta"))
    s.set_toggle("trading:delta", True)
    exchange.place_error = DeltaTimeout("read timeout")  # Delta stuck unresolved
    exchange.lookup[client_order_id("delta", "always_buy", "BTCUSD", BAR)] = DeltaTimeout("still down")
    await close_bar(s)
    for task in list(s.executor._lookups):
        await task
    await s.paper.close_position("BTCUSD")
    await close_bar(s, bar=BAR + 60)
    assert len(await s.paper.positions()) == 1  # paper is not blocked by Delta's unknown order
    assert len(exchange.placed) == 1  # Delta is
    await s.bus.stop()


async def test_strategy_off_skips_before_any_broker(cfg, exchange):
    s = await make(cfg, exchange, ("paper", "delta"))
    s.set_toggle("strategy:always_buy", False)  # switched off after the signal was generated
    await s.trader.on_signal(SignalGenerated(strategy="always_buy", version=1, symbol="BTCUSD", side="buy", reason="", bar_time=BAR, size=1))
    await s.bus.drain()
    [skip] = events(s, "TradeSkipped")
    assert (skip["broker"], skip["reason"]) == ("", "strategy is switched off")
    await s.bus.stop()


# -- runner ---------------------------------------------------------------------


async def test_history_is_closed_bars_ending_at_the_event(paper, exchange):
    exchange.history = bars([1, 2, 3, 4, 5], step=60, start=BAR - 4 * 60)
    NeedsHistory.seen.clear()
    await close_bar(paper)
    assert NeedsHistory.seen == [[BAR - 120, BAR - 60, BAR]]


async def test_missing_bar_is_an_error_not_a_guess(paper, exchange):
    exchange.history = bars([1, 2, 3], step=60, start=BAR - 4 * 60)  # ends one bar early
    NeedsHistory.seen.clear()
    await close_bar(paper)
    assert NeedsHistory.seen == []
    assert any(e["strategy"] == "needs_history" for e in events(paper, "StrategyError"))


async def test_strategy_exception_is_contained(paper):
    await close_bar(paper)
    errors = [e for e in events(paper, "StrategyError") if e["strategy"] == "broken"]
    assert "bad maths" in errors[0]["error"]
    assert paper.runner.stats["broken"].errors == 1
    assert paper.runner.stats["always_buy"].signals == 1
