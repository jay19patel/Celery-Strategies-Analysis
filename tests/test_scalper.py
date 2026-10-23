"""The scalper, and the one thing about it that is genuinely dangerous: its size.

A tiny stop makes the risk rule ask for a large position. That is correct — the same money is at
risk per trade as anywhere else, just behind a closer stop — and it is also exactly the shape of
the mistake that empties an account, so the tests that matter here are the ones proving the risk
rule still binds.
"""

from __future__ import annotations

import pytest

from tradebuddy.delta import Candle
from tradebuddy.strategies.scalper import (
    MIN_STOP_PCT,
    MIN_VOLUME_RATIO,
    PUSH,
    REWARD,
    Scalper,
    decide,
)

MIDNIGHT = 1_700_006_400


def bars(n: int = 40, *, close_at: float = 1.0, volume: float = 10.0, last_volume: float | None = None) -> list[Candle]:
    """A flat series with a controllable last bar. `close_at` is where the last bar closes inside
    its own range: 1.0 on the high, 0.0 on the low."""
    out = [Candle(MIDNIGHT + i * 300, 100, 101, 99, 100, volume) for i in range(n - 1)]
    out.append(Candle(MIDNIGHT + (n - 1) * 300, 100, 101, 99, 99 + 2 * close_at,
                      volume if last_volume is None else last_volume))
    return out


def view(short: int = 1, medium: int = 1, profile: dict | None = None) -> dict:
    return {
        "summary": [{"interval": "15m", "direction": short}, {"interval": "1h", "direction": medium}],
        "profile": profile or {},
    }


# =====================================================================================
# when it refuses, which is most bars
# =====================================================================================


def test_it_refuses_when_the_two_timeframes_above_disagree():
    """A scalp against the hour is the trade that works four times and gives it back on the fifth."""
    out = decide(bars(close_at=1.0, last_volume=30), view(short=1, medium=-1))
    assert out.side is None
    assert "do not agree" in out.reason


def test_it_refuses_when_neither_timeframe_has_a_direction():
    out = decide(bars(close_at=1.0, last_volume=30), view(short=0, medium=0))
    assert out.side is None and "do not agree" in out.reason


def test_it_refuses_a_bar_that_drifted_rather_than_pushed():
    """Closing mid-range is a bar nobody took."""
    out = decide(bars(close_at=0.5, last_volume=30), view())
    assert out.side is None
    assert f"{PUSH:.0%}" in out.reason


def test_it_refuses_a_push_on_ordinary_volume():
    out = decide(bars(close_at=1.0, last_volume=10), view())
    assert out.side is None
    assert f"{MIN_VOLUME_RATIO:.2f}x" in out.reason


def test_it_refuses_when_the_level_it_would_aim_at_is_already_here():
    """Entering a scalp into the price the traded size itself made is paying for the last point."""
    close = bars(close_at=1.0, last_volume=30)[-1].close
    out = decide(bars(close_at=1.0, last_volume=30),
                 view(profile={"ready": True, "price": close, "poc": close * 1.0002,
                               "value_low": None, "value_high": None}))
    assert out.side is None
    assert "to the next level" in out.reason


def test_it_refuses_without_enough_bars():
    assert decide(bars(5), view()).side is None


def test_it_refuses_with_no_range_to_measure():
    flat = [Candle(MIDNIGHT + i * 300, 100, 100, 100, 100, 10) for i in range(40)]
    out = decide(flat, view())
    assert out.side is None


# =====================================================================================
# when it takes one
# =====================================================================================


def test_a_clean_push_with_the_trend_is_a_trade():
    out = decide(bars(close_at=1.0, last_volume=30), view(1, 1))
    assert out.side == "buy"
    assert out.stop_loss_pct and out.take_profit_pct


def test_it_is_symmetrical():
    """Neither side may be accidentally easier to trigger than the other."""
    long = decide(bars(close_at=1.0, last_volume=30), view(1, 1))
    short = decide(bars(close_at=0.0, last_volume=30), view(-1, -1))
    assert long.side == "buy" and short.side == "sell"
    assert long.stop_loss_pct == short.stop_loss_pct
    assert long.take_profit_pct == short.take_profit_pct


def test_the_target_is_a_fixed_multiple_of_the_stop():
    out = decide(bars(close_at=1.0, last_volume=30), view())
    assert out.take_profit_pct == pytest.approx(out.stop_loss_pct * REWARD, rel=1e-6)


def test_the_stop_never_goes_under_the_floor():
    """Under it the spread and the fees eat the target before price can reach it."""
    out = decide(bars(close_at=1.0, last_volume=30), view())
    assert out.stop_loss_pct >= MIN_STOP_PCT


def test_every_refusal_says_why():
    """A skip with no reason is a strategy nobody can debug from the event log."""
    for v in (view(1, -1), view(0, 0)):
        assert decide(bars(close_at=1.0, last_volume=30), v).reason
    assert decide(bars(close_at=0.5, last_volume=30), view()).reason
    assert decide(bars(5), view()).reason


def test_it_is_pure():
    """Same bars and same view, same answer — the backtest, a worker and the page must agree."""
    candles, v = bars(close_at=1.0, last_volume=30), view()
    assert decide(candles, v) == decide(candles, v)


def test_a_missing_profile_does_not_disable_the_rest_of_the_rule():
    """The location check reads the profile the outlook carries. When there is none it is skipped,
    not treated as a refusal — but every other gate still applies."""
    out = decide(bars(close_at=1.0, last_volume=30), {"summary": view()["summary"]})
    assert out.side == "buy"


# =====================================================================================
# the size, which is the part that can hurt
# =====================================================================================


def test_the_strategy_asks_only_for_a_looser_cap():
    assert Scalper.margin_pct is not None
    assert Scalper.is_default_sl_tp is False  # Settings' percentages would be a swing trade here
    assert Scalper.interval == "5m"


async def test_a_looser_cap_can_never_risk_more_than_the_risk_rule_allows(cfg, exchange):
    """THE test. `margin_pct` raises the CAP; sizing stays min(by_risk, by_margin), so a scalper
    with a huge cap still risks exactly `risk_per_trade_pct` behind its stop. If this ever
    inverts, a 0.15% stop with a 60% cap is an account-ending position."""
    from tests.test_pipeline import make

    system = await make(cfg, exchange)
    await system.update_settings({"risk_per_trade_pct": 1.0, "trade_margin_pct": 10.0})
    broker = system.paper
    account = await broker.account()
    price = 100_000.0

    tight = await system.trader.size_for_risk(broker, "BTCUSD", price, 0.15, account, system.settings)
    loose = await system.trader.size_for_risk(broker, "BTCUSD", price, 0.15, account, system.settings, margin_pct=60.0)
    by_risk = await broker.size_for_notional(
        "BTCUSD", price, account.equity * system.settings.risk_per_trade_pct / 100.0 / (0.15 / 100.0))

    assert loose >= tight          # the cap was what was binding
    assert loose <= by_risk        # and the risk rule still is
    # A cap of a thousand percent cannot buy more than the risk rule allows.
    absurd = await system.trader.size_for_risk(broker, "BTCUSD", price, 0.15, account, system.settings, margin_pct=1000.0)
    assert absurd == by_risk


async def test_only_a_strategy_that_asked_for_a_cap_gets_one(cfg, exchange):
    """The override must not leak onto everything else. Built from the real registry, so a strategy
    that quietly grows a `margin_pct` shows up here."""
    from tradebuddy.strategies import discover
    from tradebuddy.system import System

    from .conftest import IdleStream

    system = System(cfg, strategies=discover(), stream_factory=IdleStream, client_factory=exchange.client)
    margins = system.trader.margins
    assert margins == {"scalper": Scalper.margin_pct}, f"unexpected margin overrides: {margins}"
    assert system.trader.margin_for("tb_master_15m") is None
    assert system.trader.margin_for("scalper") == Scalper.margin_pct
