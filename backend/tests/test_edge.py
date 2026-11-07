"""Size follows a measured edge, and every fee carries India's GST."""

from __future__ import annotations

from typing import Any

import pytest

from app.options.assess import FeeRate, leg_fee
from app.trading import edge


def test_every_fee_is_charged_with_gst() -> None:
    """Delta's product spec states 0.05% of notional; the account is charged 0.059%."""
    rate = FeeRate.with_gst(0.0005, 0.10)
    assert leg_fee(1_000.0, 1.0, 80_000.0, rate) == pytest.approx(0.0005 * 80_000 * 1.18)
    assert leg_fee(10.0, 1.0, 80_000.0, rate) == pytest.approx(0.10 * 10 * 1.18)  # the premium cap too


def test_an_edge_is_measured_only_once_there_are_enough_trades_and_its_range_says_so() -> None:
    few = edge.measure([1.0] * 10)
    assert few["verdict"] == "unproven" and few["expectancy_r"] == 1.0
    winner = edge.measure([1.0, -0.5, 0.8, 0.6] * 10)
    assert winner["verdict"] == "positive" and 0 < winner["ci_low_r"] < winner["expectancy_r"] < winner["ci_high_r"]
    assert edge.measure([-1.0, 0.3, -0.8, -0.5] * 10)["verdict"] == "negative"
    assert edge.measure([1.0, -1.0] * 20)["verdict"] == "unclear"
    assert edge.measure([1.0, -1.0] * 200)["ci_low_r"] < 0 < edge.measure([1.0, -1.0] * 200)["ci_high_r"]  # the closed form


def test_size_follows_the_measurement_and_real_money_waits_for_proof() -> None:
    assert edge.scale(edge.measure([1.0] * 5), 2.0, real_money=False) == (1.0, "")
    x, why = edge.scale(edge.measure([1.0] * 5), 2.0, real_money=True)
    assert x == 0 and "not proven on paper" in why
    assert edge.scale(edge.measure([-1.0, 0.3, -0.8, -0.5] * 10), 2.0, False)[0] == 0
    assert edge.scale(edge.measure([0.9, -1.0, 1.0, -1.0] * 10), 2.0, False)[0] == 0.5  # losing a little, unproven
    x, _ = edge.scale(edge.measure([1.0, -0.5, 0.8, 0.6] * 10), 2.0, real_money=True)
    assert 1.0 <= x <= edge.MAX_SCALE  # proven: a quarter Kelly, capped


def test_a_drawdown_from_the_peak_cuts_the_risk_but_never_stops_it() -> None:
    assert edge.drawdown(1_000.0, [50.0, 50.0]) == 0.0
    assert edge.drawdown(880.0, [100.0, 20.0, -240.0]) == pytest.approx(240 / 1_120)
    assert edge.drawdown_scale(0.05)[0] == 1.0
    assert edge.drawdown_scale(0.12)[0] == 0.5
    assert edge.drawdown_scale(0.30)[0] == 0.25


def _closed(i: int, strategy: str, pnl: float, broker: str = "paper", source: str = "auto") -> None:
    from app.core.db import session
    from app.core.models import Structure

    with session() as s:
        s.add(Structure(id=f"{strategy}-{i}", broker=broker, underlying="BTC", strategy=strategy, source=source, status="closed",
                        expiry=0.0, qty=1, pnl=pnl, max_loss=10.0, take_profit_pct=0, stop_loss_pct=0, opened_at=i, closed_at=i + 1.0))
        s.commit()


def test_automatic_sizing_reads_the_strategies_own_record_and_the_brokers_drawdown(env: Any) -> None:
    for i in range(40):
        _closed(i, "ema_9_15", [-10.0, 3.0, -8.0, -5.0][i % 4])  # -0.5R a trade
    x, why = env.trading.risk_scale("paper", "ema_9_15", 10_000.0)
    assert x == 0 and "measured losing" in why
    for i in range(5):
        _closed(100 + i, "supertrend", 5.0, source="manual")  # a click is not the strategy's record
    assert env.trading.risk_scale("paper", "supertrend", 10_000.0) == (1.0, "")
    # the 175 the closed trades lost on net, against 1,000 now: about 15% below the peak
    x, why = env.trading.risk_scale("paper", "supertrend", 1_000.0)
    assert x == 0.5 and "below the equity peak" in why
