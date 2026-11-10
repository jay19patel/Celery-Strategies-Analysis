"""Expiry-day decay: sell what is still bid far out of the money, as a capped spread, held to settlement."""

from __future__ import annotations

import time
from typing import Any

import pytest
from sqlmodel import select

from app.core.db import session
from app.core.models import Structure
from app.options import decay
from app.settings import StrategyConfig
from app.trading.autotrader import AutoTrader
from tests.conftest import load_quotes

EXPIRY_DECAY = "expiry_decay"

SPOT = 80_331.1


def found(vol: float) -> list[dict[str, Any]]:
    now = time.time()
    return decay.candidates(load_quotes(now, hours=3.0), SPOT, now, vol, lambda price: 0.0, 100)


def test_a_calm_market_leaves_premium_worth_selling_and_a_wild_one_does_not() -> None:
    calm = found(0.10)
    assert calm, "three hours out at 10% realised vol, the far strikes are overpaid"
    for c in calm:
        assert c["sigmas"] >= decay.MIN_SIGMAS and c["ev"] > 0 and c["credit"] > 0
        assert c["max_loss"] == pytest.approx(c["width"] - c["credit"], rel=1e-6)  # capped by the wing
        assert (c["wing"] > c["strike"]) if c["kind"] == "call" else (c["wing"] < c["strike"])
        assert c["pop"] > 0.5
    ranked = [c["ev_on_risk_pct"] for c in calm]
    assert ranked == sorted(ranked, reverse=True)
    assert found(3.0) == []  # moving at 300%, nothing that far is safe to sell for what it bids


def test_without_a_realised_vol_nothing_is_offered() -> None:
    assert found(0.0) == []


async def test_the_auto_trader_sells_decay_inside_its_window_once_per_symbol(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Its own strategy now, on the auto-trader's cycle: no zero-hero switch, no separate job."""
    env.feed.load(hours=3.0)
    env.configure(max_spread_pct=100, strategies={EXPIRY_DECAY: StrategyConfig(enabled=True)} | {
        n: StrategyConfig(enabled=False) for n in ("iron_condor", "iron_butterfly", "bull_put_spread", "bear_call_spread")
    })
    env.desk.realised = lambda u: {"reference": 0.10, "floor": 0.08, "forecast_24h": None}  # type: ignore[method-assign]
    auto = AutoTrader(env.desk, env.trading, env.settings, env.hub)
    d = await auto.decide("BTC", ["paper"])
    assert d["action"] == "open", d["reason"]
    assert d["strategy"] == EXPIRY_DECAY and d["orders"]["paper"]["qty"] >= 1
    with session() as s:
        [st] = s.exec(select(Structure).where(Structure.strategy == EXPIRY_DECAY)).all()
    assert st.max_loss is not None  # capped by the wing, so it may trade on real money too
    again = await auto.decide("BTC", ["paper"])  # one held on BTC: the next pass has no room
    assert again["action"] == "skip" and "no room" in again["reason"]


async def test_outside_its_window_the_decay_spread_says_so(env: Any) -> None:
    env.configure(max_spread_pct=100, strategies={EXPIRY_DECAY: StrategyConfig(enabled=True)} | {
        n: StrategyConfig(enabled=False) for n in ("iron_condor", "iron_butterfly", "bull_put_spread", "bear_call_spread")
    })
    d = await AutoTrader(env.desk, env.trading, env.settings, env.hub).decide("BTC", ["paper"])  # the fixture is ~17h out
    assert d["action"] == "skip" and "no expiry 2h to 4h away" in d["reason"]


def test_the_guard_holds_a_decay_spread_to_settlement(env: Any) -> None:
    from app.trading.guard import Guard

    now = time.time()
    st = Structure(id="d2", broker="paper", underlying="BTC", strategy=EXPIRY_DECAY, source="auto", status="open",
                   expiry=now + 30 * 60, qty=1, entry_cash=1.0, max_loss=4.0, take_profit_pct=50, stop_loss_pct=100, opened_at=now)
    reason = Guard(env.trading, env.settings, env.hub).exit_reason(st, [], env.settings(), now)
    assert not (reason or "").startswith("time exit")  # 30 minutes out, an ordinary credit would be closed
