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
    assert {r["strategy"]: r["action"] for r in signals} == {"best": "open", "slow": "skip"}
    assert "better trade" in next(r for r in signals if r["strategy"] == "slow")["reason"]
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
