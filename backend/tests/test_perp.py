"""Positions on the perpetual: opened with their strategy's stop and target, closed by the guard at
those levels, sized so the stop costs the risk budget, and run by the strategy runner on closed bars."""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.analysis.service import MarketDesk
from app.errors import Refused
from app.strategies.base import Context, Decision, Strategy
from app.trading.guard import Guard
from app.trading.runner import StrategyRunner

PRICE = 80_000.0


@pytest.fixture
def perp(env: Any) -> Any:
    env.feed.set_perp(PRICE, bid=PRICE - 0.5, ask=PRICE + 0.5)
    return env


async def test_a_long_fills_at_the_ask_and_keeps_its_strategys_levels(perp: Any) -> None:
    start = await perp.paper.account()
    view = await perp.trading.open_directional("paper", "BTC", "buy", 10, 79_500, 81_000, "supertrend", "auto", "req-perp-1", "why")
    assert view["status"] == "open" and view["instrument"] == "perp" and view["kind"] == "long"
    leg = view["legs"][0]
    assert leg["kind"] == "perp" and leg["entry_price"] == PRICE + 0.5
    units = 10 * 0.001
    assert view["max_loss"] == pytest.approx((PRICE + 0.5 - 79_500) * units + 2 * leg["entry_fee"])
    after = await perp.paper.account()
    # The notional is not spent: the wallet shows only the fee, equity only the fee and the spread.
    assert after.cash == pytest.approx(start.cash - leg["entry_fee"])
    assert after.margin_used == pytest.approx((PRICE + 0.5) * units / 10)


async def test_the_guard_closes_at_the_stop_and_at_the_target(perp: Any) -> None:
    guard = Guard(perp.trading, perp.settings, perp.hub)
    stop = await perp.trading.open_directional("paper", "BTC", "buy", 5, 79_500, 81_000, "supertrend", "auto", "req-perp-sl", "")
    perp.feed.set_perp(79_400, bid=79_399.5, ask=79_400.5)
    await guard.check_structures()
    closed = perp.trading.structure(stop["id"])
    assert closed["status"] == "closed" and closed["close_reason"].startswith("stop loss") and closed["pnl"] < 0

    perp.feed.set_perp(PRICE, bid=PRICE - 0.5, ask=PRICE + 0.5)
    short = await perp.trading.open_directional("paper", "BTC", "sell", 5, 80_500, 79_000, "ema_vwap", "auto", "req-perp-tp", "")
    perp.feed.set_perp(78_990, bid=78_989.5, ask=78_990.5)
    await guard.check_structures()
    won = perp.trading.structure(short["id"])
    assert won["status"] == "closed" and won["close_reason"].startswith("target") and won["pnl"] > 0


async def test_the_guard_closes_after_the_maximum_hold(perp: Any) -> None:
    from app.core.db import session
    from app.core.models import Structure

    view = await perp.trading.open_directional("paper", "BTC", "buy", 5, 79_500, 81_000, "supertrend", "auto", "req-perp-hold", "")
    with session() as s:
        row = s.get(Structure, view["id"])
        row.expiry = time.time() - 1
        s.add(row)
        s.commit()
    await Guard(perp.trading, perp.settings, perp.hub).check_structures()
    assert perp.trading.structure(view["id"])["close_reason"].startswith("max hold")


async def test_levels_on_the_wrong_side_and_a_second_position_are_refused(perp: Any) -> None:
    with pytest.raises(Refused, match="stop below"):
        await perp.trading.open_directional("paper", "BTC", "buy", 5, 80_500, 81_000, "manual", "manual", "req-perp-bad", "")
    await perp.trading.open_directional("paper", "BTC", "buy", 5, 79_500, 81_000, "supertrend", "auto", "req-perp-one", "")
    with pytest.raises(Refused, match="one per symbol"):
        await perp.trading.open_directional("paper", "BTC", "sell", 5, 80_500, 79_000, "ema_vwap", "auto", "req-perp-two", "")


async def test_an_options_structure_and_a_perp_position_do_not_block_each_other(perp: Any) -> None:
    perp.configure(max_spread_pct=100)
    await perp.trading.open_directional("paper", "BTC", "buy", 5, 79_500, 81_000, "supertrend", "auto", "req-mix-1", "")
    condor = await perp.trading.open("paper", perp.desk.plan("BTC", "iron_condor"), 1, "manual", "req-mix-2")
    assert condor["status"] == "open"


def test_the_stop_sizes_the_position(perp: Any) -> None:
    size = perp.trading.perp_size
    narrow = size(1000, 1000, PRICE, PRICE - 200, 0.001, 0.0005)
    wide = size(1000, 1000, PRICE, PRICE - 800, 0.001, 0.0005)
    assert narrow > wide > 0  # a wider stop buys a smaller position, never a bigger loss
    loss_at_stop = wide * (800 * 0.001 + 2 * 0.0005 * PRICE * 0.001)
    assert loss_at_stop <= 1000 * perp.settings().risk_per_trade_pct / 100


# -- the runner --------------------------------------------------------------------------------------


class Always(Strategy):
    """Fires on every bar with fixed levels around the last close."""

    label = summary = stop_rule = target_rule = "test"
    interval = "5m"
    lookback = 10

    def __init__(self, name: str, reward: float) -> None:
        self.name, self.reward = name, reward  # type: ignore[misc]
        self.calls = 0

    def decide(self, ctx: Context) -> Decision:
        self.calls += 1
        price = PRICE
        return Decision("buy", f"{self.name} fired", entry=price, stop=price - 200, target=price + 200 * self.reward)


@pytest.fixture(autouse=True)
def no_day_bias(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake feed's "today" depends on the clock; the runner tests pin the bias to none."""
    from app.analysis.service import MarketDesk

    async def unready(self: Any, underlying: str) -> dict[str, Any]:
        return {"ready": False}

    monkeypatch.setattr(MarketDesk, "bias", unready)


async def test_a_signal_against_a_strong_day_bias_is_shown_and_not_taken(perp: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.trading.runner as runner_mod
    from app.analysis.service import MarketDesk

    async def sold_hard(self: Any, underlying: str) -> dict[str, Any]:
        return {"ready": True, "label": "bearish", "score": -0.7}

    monkeypatch.setattr(MarketDesk, "bias", sold_hard)
    monkeypatch.setattr(runner_mod, "REGISTRY", {"long": Always("long", 3.0)})
    s = perp.settings().model_copy(update={"directional_strategies": ["long"]})
    runner = StrategyRunner(perp.trading, MarketDesk(perp.feed, lambda: s, perp.hub), lambda: s, perp.hub)
    assert await runner._symbol("BTC", s) == "BTCUSD: no signal"
    assert not perp.trading.structures("open") and runner.signals() == []
    assert "bearish (-0.70)" in runner.latest[("long", "BTCUSD")]["reason"]


async def test_the_runner_trades_the_best_signal_once_per_bar(perp: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.trading.runner as runner_mod

    slow, best = Always("slow", 1.5), Always("best", 3.0)
    monkeypatch.setattr(runner_mod, "REGISTRY", {"slow": slow, "best": best})
    # model_copy, not validation: the setting's type only admits the real strategies' names
    s = perp.settings().model_copy(update={"directional_strategies": ["slow", "best"]})
    runner = StrategyRunner(perp.trading, MarketDesk(perp.feed, lambda: s, perp.hub), lambda: s, perp.hub)
    await runner._symbol("BTC", s)
    held = perp.trading.structures("open")
    assert len(held) == 1 and held[0]["strategy"] == "best"
    signals = runner.signals()
    assert {r["strategy"]: r["action"] for r in signals} == {"best": "open"}  # the loser is not stored...
    assert "better trade" in runner.latest[("slow", "BTCUSD")]["reason"]  # ...only shown live
    await runner._symbol("BTC", s)  # the same closed bar is never decided twice
    assert slow.calls == best.calls == 1


async def test_a_strategy_switched_off_for_a_symbol_is_not_evaluated_there(perp: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.trading.runner as runner_mod

    only_eth = Always("only_eth", 3.0)
    monkeypatch.setattr(runner_mod, "REGISTRY", {"only_eth": only_eth})
    s = perp.settings().model_copy(update={"directional_strategies": ["only_eth"], "strategy_symbols": {"only_eth": ["ETH"]}})
    runner = StrategyRunner(perp.trading, MarketDesk(perp.feed, lambda: s, perp.hub), lambda: s, perp.hub)
    assert await runner._symbol("BTC", s) == "BTCUSD: no signal"
    assert only_eth.calls == 0 and not perp.trading.structures("open")


async def test_a_long_lookback_is_folded_from_the_15m_store_and_held_for_its_own_time(perp: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 1m base reaches back ~56 hours; a 4h strategy needs weeks. It gets 4h bars folded from the
    stored 15m series, and its position is held for the strategy's own time, not the setting's."""
    import app.trading.runner as runner_mod

    class Slow(Always):
        interval = "4h"
        lookback = 40  # the fake store's 800 15m bars hold 50 4h ones
        max_hold_hours = 240.0

        def decide(self, ctx: Context) -> Decision:
            self.seen = ctx.candles
            return super().decide(ctx)

    slow = Slow("slow", 3.0)
    monkeypatch.setattr(runner_mod, "REGISTRY", {"slow": slow})
    s = perp.settings().model_copy(update={"directional_strategies": ["slow"]})
    runner = StrategyRunner(perp.trading, MarketDesk(perp.feed, lambda: s, perp.hub), lambda: s, perp.hub)
    await runner._symbol("BTC", s)
    assert len(slow.seen) == 40 and all(c.time % 14_400 == 0 for c in slow.seen)
    held = perp.trading.structures("open")
    assert len(held) == 1
    assert held[0]["expiry"] - held[0]["opened_at"] == pytest.approx(240 * 3600, abs=5)


async def test_the_funding_rate_is_part_of_a_signals_cost(perp: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.trading.runner as runner_mod

    monkeypatch.setattr(runner_mod, "REGISTRY", {"costs": Always("costs", 3.0)})
    s = perp.settings().model_copy(update={"directional_strategies": ["costs"]})
    runner = StrategyRunner(perp.trading, MarketDesk(perp.feed, lambda: s, perp.hub), lambda: s, perp.hub)
    strategy = runner_mod.REGISTRY["costs"]
    calm = (await runner._context(strategy, "BTC", "BTCUSD", [{"time": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])).cost_pct
    perp.feed.perps["BTCUSD"].stats["funding_rate_pct"] = -0.05  # either sign: the side is not known yet
    paid = (await runner._context(strategy, "BTC", "BTCUSD", [{"time": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])).cost_pct
    assert paid == pytest.approx(calm + 0.05)


# -- stops held by the exchange ----------------------------------------------------------------------


class Exchange:
    """Paper's fills, plus stops held the way an exchange holds them: placed, cancelled, or filled."""

    name = "delta"

    def __init__(self, paper: Any, refuse: bool = False) -> None:
        self.paper, self.refuse = paper, refuse
        self.stops: dict[str, dict[str, Any]] = {}  # client id -> the stop
        self.cancelled: list[str] = []
        self.sent: list[Any] = []

    def not_ready(self) -> str:
        return ""

    async def account(self) -> Any:
        return await self.paper.account()

    async def execute(self, order: Any) -> Any:
        self.sent.append(order)
        return await self.paper.execute(order)

    async def lookup(self, cid: str) -> Any:
        stop = self.stops.get(cid)
        if stop is not None:
            return stop.get("fill")
        return await self.paper.lookup(cid)

    async def settle(self, order: Any, price: float) -> Any:
        return await self.paper.settle(order, price)

    async def protect(self, symbol: str, side: str, size: int, stop_price: float, cid: str) -> str:
        from app.errors import BrokerError

        if self.refuse:
            raise BrokerError("stop orders are not accepted")
        self.stops[cid] = {"symbol": symbol, "side": side, "size": size, "stop": stop_price, "id": f"ex-{len(self.stops)}"}
        return self.stops[cid]["id"]

    async def unprotect(self, symbol: str, order_id: str) -> None:
        self.cancelled.append(order_id)

    def trigger(self, price: float, fee: float = 0.1) -> None:
        from app.brokers.base import Fill

        for cid, stop in self.stops.items():
            stop["fill"] = Fill(cid, stop["size"], price, fee, "ex-fill")


@pytest.fixture
def exchange(perp: Any) -> Exchange:
    ex = Exchange(perp.paper)
    perp.brokers["delta"] = ex
    perp.configure(delta_active=True)
    return ex


async def test_an_entry_leaves_its_stop_with_the_exchange(perp: Any, exchange: Exchange) -> None:
    view = await perp.trading.open_directional("delta", "BTC", "buy", 5, 79_500, 81_000, "supertrend", "auto", "req-ex-1", "")
    [stop] = exchange.stops.values()
    assert stop == {"symbol": "BTCUSD", "side": "sell", "size": 5, "stop": 79_500, "id": "ex-0"}
    assert perp.trading.structure(view["id"])["stop_order_id"] == "ex-0"


async def test_a_close_cancels_the_exchange_stop_first(perp: Any, exchange: Exchange) -> None:
    view = await perp.trading.open_directional("delta", "BTC", "buy", 5, 79_500, 81_000, "supertrend", "auto", "req-ex-2", "")
    closed = await perp.trading.close(view["id"], "test")
    assert closed["status"] == "closed" and exchange.cancelled == ["ex-0"]
    assert closed["stop_order_id"] == ""


async def test_a_stop_the_exchange_filled_is_booked_not_sold_again(perp: Any, exchange: Exchange) -> None:
    guard = Guard(perp.trading, perp.settings, perp.hub)
    view = await perp.trading.open_directional("delta", "BTC", "buy", 5, 79_500, 81_000, "supertrend", "auto", "req-ex-3", "")
    exchange.trigger(79_480.0)  # the process was away; the mark is back above the stop by now
    sent = len(exchange.sent)
    await guard.check_structures()
    closed = perp.trading.structure(view["id"])
    assert closed["status"] == "closed" and "exchange stop filled at 79,480.00" in closed["close_reason"]
    assert closed["legs"][0]["exit_price"] == 79_480.0
    assert len(exchange.sent) == sent and not exchange.cancelled  # nothing was sold twice


async def test_a_position_the_exchange_will_not_protect_is_closed(perp: Any) -> None:
    ex = Exchange(perp.paper, refuse=True)
    perp.brokers["delta"] = ex
    perp.configure(delta_active=True)
    view = await perp.trading.open_directional("delta", "BTC", "buy", 5, 79_500, 81_000, "supertrend", "auto", "req-ex-4", "")
    assert view["status"] == "closed" and "exchange stop could not be placed" in view["close_reason"]


# -- the trailing stop ------------------------------------------------------------------------------


async def test_the_stop_trails_from_one_r_never_loosens_and_locks_the_gain_in(perp: Any) -> None:
    guard = Guard(perp.trading, perp.settings, perp.hub)
    view = await perp.trading.open_directional("paper", "BTC", "buy", 5, 79_500, 82_000, "supertrend", "auto", "req-trail-1", "")
    entry = view["legs"][0]["entry_price"]
    risk = entry - 79_500

    def stop() -> float:
        return perp.trading.structure(view["id"])["stop_price"]

    perp.feed.set_perp(entry + 0.8 * risk, bid=entry + 0.8 * risk - 0.5, ask=entry + 0.8 * risk + 0.5)
    await guard.check_structures()
    assert stop() == 79_500  # under 1R in profit: the strategy's stop stands
    perp.feed.set_perp(entry + 1.2 * risk, bid=entry + 1.2 * risk - 0.5, ask=entry + 1.2 * risk + 0.5)
    await guard.check_structures()
    assert stop() == max(entry + 1.2 * risk - risk, entry)  # at least break-even
    perp.feed.set_perp(entry + 2.5 * risk, bid=entry + 2.5 * risk - 0.5, ask=entry + 2.5 * risk + 0.5)
    await guard.check_structures()
    assert stop() == pytest.approx(entry + 1.5 * risk)  # 1R behind the best mark
    shown = perp.trading.structure(view["id"])
    assert shown["trail"]["active"] and shown["trail"]["moved_r"] == pytest.approx(2.5, abs=0.01)  # from -1R to +1.5R
    assert shown["stop_pct"] > 0 and shown["target_pct"] > 0  # the stop is now on the profit side
    perp.feed.set_perp(entry + 2.0 * risk, bid=entry + 2.0 * risk - 0.5, ask=entry + 2.0 * risk + 0.5)
    await guard.check_structures()
    assert stop() == pytest.approx(entry + 1.5 * risk)  # a pullback never loosens it
    back = entry + 1.4 * risk
    perp.feed.set_perp(back, bid=back - 0.5, ask=back + 0.5)
    await guard.check_structures()
    closed = perp.trading.structure(view["id"])
    assert closed["status"] == "closed" and closed["close_reason"].startswith("trailing stop") and closed["pnl"] > 0


async def test_a_trailed_exchange_stop_is_placed_before_the_old_one_is_cancelled(perp: Any, exchange: Exchange) -> None:
    view = await perp.trading.open_directional("delta", "BTC", "buy", 5, 79_500, 82_000, "supertrend", "auto", "req-trail-2", "")
    entry = view["legs"][0]["entry_price"]
    up = entry + 1.5 * (entry - 79_500)
    perp.feed.set_perp(up, bid=up - 0.5, ask=up + 0.5)
    exchange.refuse = True  # the new stop is refused: the old, wider one stays and nothing is cancelled
    await Guard(perp.trading, perp.settings, perp.hub).check_structures()
    held = perp.trading.structure(view["id"])
    assert held["stop_price"] == 79_500 and held["stop_order_id"] == "ex-0" and exchange.cancelled == []
    exchange.refuse = False
    await Guard(perp.trading, perp.settings, perp.hub).check_structures()
    moved = perp.trading.structure(view["id"])
    assert moved["stop_price"] > entry and moved["stop_order_id"] == "ex-1"
    assert list(exchange.stops) == [f"{view['id']}0k", f"{view['id']}1k"]  # placed under a new id...
    assert exchange.cancelled == ["ex-0"]  # ...and only then the old one went


async def test_a_stop_that_just_filled_is_not_moved(perp: Any, exchange: Exchange) -> None:
    view = await perp.trading.open_directional("delta", "BTC", "buy", 5, 79_500, 82_000, "supertrend", "auto", "req-trail-3", "")
    st, legs = perp.trading._load(view["id"])
    exchange.trigger(79_480.0)
    up = 81_500.0
    perp.feed.set_perp(up, bid=up - 0.5, ask=up + 0.5)
    await perp.trading.trail(st, legs)
    assert len(exchange.stops) == 1 and exchange.cancelled == []


def test_a_pulled_perpetual_book_is_no_price() -> None:
    """The ETH perpetual has traded at 0.05: a close priced off an emptied book would sell there."""
    from app.market.feed import parse_perp

    msg = {"symbol": "ETHUSD", "mark_price": "2240", "quotes": {"best_bid": "0.05", "best_ask": "2240.5"}}
    q = parse_perp(msg, time.time())
    assert q is not None and q.stats["bid"] is None and q.stats["ask"] == 2240.5


async def test_a_strategy_stopped_out_today_is_not_re_entered_on_that_symbol(perp: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """4 Nov: the 9/15 cross on BTC was stopped at 21:40 and bought back fifteen minutes later."""
    import app.trading.runner as runner_mod

    monkeypatch.setattr(runner_mod, "REGISTRY", {"again": Always("again", 3.0)})
    s = perp.settings().model_copy(update={"directional_strategies": ["again"]})
    first = await perp.trading.open_directional("paper", "BTC", "buy", 1, 79_500, 82_000, "again", "auto", "req-again-1", "")
    await perp.trading.close(first["id"], "stop loss: mark 79,500.00 reached the strategy's stop 79,500.00")
    runner = StrategyRunner(perp.trading, MarketDesk(perp.feed, lambda: s, perp.hub), lambda: s, perp.hub)
    assert await runner._symbol("BTC", s) == "BTCUSD: no signal"
    assert not perp.trading.structures("open")
    assert "stopped out on BTCUSD earlier today" in runner.latest[("again", "BTCUSD")]["reason"]
