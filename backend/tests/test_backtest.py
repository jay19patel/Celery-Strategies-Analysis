"""The perp backtest: entries on the next open, the worse exit when a bar touches both levels, gaps
filled where the market opened, and costs taken off every trade."""

from __future__ import annotations

from typing import Any

import pytest

from app.backtest.perp import monte_carlo, simulate, summary, walk_forward
from app.strategies.base import Context, Decision, Strategy


class LongOnce(Strategy):
    """Buys on the bar at `at` with a stop 1% and a target 2% from that bar's close."""

    name = label = summary = stop_rule = target_rule = "test"
    interval = "15m"
    lookback = 1

    def __init__(self, at: int = 0) -> None:
        self.at = at
        self.seen: list[int] = []

    def decide(self, ctx: Context) -> Decision:
        bar = ctx.candles[-1]
        self.seen.append(bar.time)
        if bar.time != self.at:
            return Decision(None, "no")
        return Decision("buy", "test", entry=bar.close, stop=bar.close * 0.99, target=bar.close * 1.02)


def bars(*rows: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    return [{"time": i * 900, "open": o, "high": h, "low": low, "close": c, "volume": 1.0} for i, (o, h, low, c) in enumerate(rows)]


def run(strategy: Strategy, data: list[dict[str, Any]], cost: float = 0.0, funding: float = 0.0) -> list[Any]:
    return simulate(strategy, data, "BTCUSD", "BTC", cost, funding)


def test_the_entry_is_the_next_open_and_the_target_pays_after_costs() -> None:
    s = LongOnce()
    data = bars((100, 100, 100, 100), (100.5, 101, 100.2, 100.8), (100.8, 102.5, 100.6, 102))
    [t] = run(s, data, cost=0.1)
    assert t.entry == 100.5 and t.reason == "target" and t.exit == pytest.approx(102)
    assert t.pnl_pct == pytest.approx((102 - 100.5) / 100.5 * 100 - 0.1)
    assert max(s.seen) == 0  # it decided on the first bar only; nothing after the entry was shown to it


def test_a_bar_that_touches_both_levels_is_a_stop() -> None:
    [t] = run(LongOnce(), bars((100, 100, 100, 100), (100, 103, 98, 101)))
    assert t.reason == "stop" and t.exit == pytest.approx(99)


def test_a_gap_through_the_stop_fills_at_the_open() -> None:
    [t] = run(LongOnce(), bars((100, 100, 100, 100), (100, 100.5, 99.5, 100.2), (97, 97.5, 96, 97)))
    assert t.reason == "gap through the stop" and t.exit == 97


def test_funding_is_charged_for_the_time_held() -> None:
    data = bars((100, 100, 100, 100), *[(100, 100.5, 99.5, 100)] * 40, (100, 102.5, 100, 102))
    [t] = run(LongOnce(), data, funding=0.01)
    hours = 41 * 0.25
    assert t.pnl_pct == pytest.approx(2.0 - 0.01 * hours / 8)


def test_a_trade_still_open_when_the_data_ends_is_not_counted() -> None:
    assert run(LongOnce(), bars((100, 100, 100, 100), (100, 100.5, 99.5, 100))) == []


def test_the_summary_folds_and_monte_carlo() -> None:
    from app.backtest.perp import Trade

    trades = [Trade(1, k * 3600, k * 3600 + 900, 100, 101, 1.0, p, "x") for k, p in enumerate([2.0, -1.0, -1.0, 2.0, -1.0, 2.0, -1.0, -1.0])]
    s = summary(trades)
    assert s["trades"] == 8 and s["profit_factor"] == pytest.approx(6 / 5) and s["expectancy_r"] == pytest.approx(1 / 8)
    assert s["max_drawdown_pct"] == pytest.approx(2.0)
    assert sum(f["trades"] for f in walk_forward(trades, 4)) == 8
    mc = monte_carlo(trades, runs=500)
    assert mc["drawdown_p95_pct"] >= mc["drawdown_p50_pct"] > 0


def test_the_backtest_trails_the_stop_as_the_live_guard_does() -> None:
    """Up 1.5R, then back: live, the stop has trailed to +0.5R and books a gain. A backtest without
    the trail books the full 1R loss, so it was testing a different strategy from the one trading."""
    data = bars((100, 100, 100, 100), (100, 100.2, 99.9, 100.1), (100.1, 101.5, 100.0, 101.4), (101.4, 101.4, 98.0, 98.5))
    [t] = run(LongOnce(), data)
    assert t.reason == "trailing stop" and t.exit == pytest.approx(100.5) and t.pnl_pct > 0


def test_a_stop_fills_past_its_trigger() -> None:
    [t] = simulate(LongOnce(), bars((100, 100, 100, 100), (100, 103, 98, 101)), "BTCUSD", "BTC", 0.0, stop_slippage_pct=0.1)
    assert t.reason == "stop" and t.exit == pytest.approx(99 * 0.999)
