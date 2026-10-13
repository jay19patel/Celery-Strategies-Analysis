"""One strategy's own daily loss limit.

The broker-wide limit in guard.py only fires once the whole account is down. By then a strategy that
keeps stopping out has spent the budget every other strategy needed: in one live run pcr_options took
13 trades for -323 while the account's three better strategies were blocked behind it. This limit
stands a strategy down at its own share of the day, and leaves the others trading.
"""

from __future__ import annotations

from tradebuddy.events import PositionClosed
from tradebuddy.guard import trading_day

from .test_pipeline import close_bar, events, paper, skipped  # noqa: F401 - paper is a fixture

START = 1000.0


async def lost(system, pnl: float, strategy: str = "always_buy") -> None:
    """Book a closed trade on paper, the way the broker does: start the day, then log the close."""
    system.store.start_day("paper", trading_day(system.settings.day_timezone), START)
    system.bus.publish(PositionClosed(
        broker="paper", strategy=strategy, symbol="ETHUSD", side="long",
        entry_price=100.0, exit_price=90.0, pnl=pnl, reason="stop loss hit",
    ))
    await system.drain()


async def test_a_strategy_past_its_share_of_the_day_stops_entering(paper):  # noqa: F811
    await paper.update_settings({"strategy_loss_limit_pct": 2.0})
    await lost(paper, -25.0)  # 2.5% of the day's 1,000, past its 2%
    await close_bar(paper)
    assert await paper.paper.positions() == []
    [reason] = skipped(paper)
    assert "always_buy has lost 25.00 on paper today" in reason
    assert "resumes tomorrow" in reason


async def test_a_loss_inside_the_share_still_trades(paper):  # noqa: F811
    await paper.update_settings({"strategy_loss_limit_pct": 2.0})
    await lost(paper, -19.0)  # 1.9%, still under
    await close_bar(paper)
    assert len(await paper.paper.positions()) == 1


async def test_one_strategy_stood_down_does_not_block_another(paper):  # noqa: F811
    await paper.update_settings({"strategy_loss_limit_pct": 2.0})
    await lost(paper, -40.0, strategy="someone_else")
    await close_bar(paper)
    assert len(await paper.paper.positions()) == 1  # always_buy never lost anything; brokers and strategies are independent


async def test_zero_switches_the_limit_off(paper):  # noqa: F811
    await paper.update_settings({"strategy_loss_limit_pct": 0.0})
    await lost(paper, -500.0)
    await close_bar(paper)
    assert len(await paper.paper.positions()) == 1


async def test_profit_today_never_halts_a_strategy(paper):  # noqa: F811
    await paper.update_settings({"strategy_loss_limit_pct": 2.0})
    await lost(paper, 500.0)
    await close_bar(paper)
    assert len(await paper.paper.positions()) == 1


async def test_without_a_day_on_the_broker_nothing_is_halted(paper):  # noqa: F811
    """No daily_risk row means the guard has not opened the day, so there is no equity to measure against."""
    await paper.update_settings({"strategy_loss_limit_pct": 2.0})
    paper.bus.publish(PositionClosed(
        broker="paper", strategy="always_buy", symbol="ETHUSD", side="long",
        entry_price=100.0, exit_price=90.0, pnl=-900.0, reason="stop loss hit",
    ))
    await paper.drain()
    await close_bar(paper)
    assert len(await paper.paper.positions()) == 1
