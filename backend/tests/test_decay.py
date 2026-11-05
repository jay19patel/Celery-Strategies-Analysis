"""Expiry-day decay: sell what is still bid far out of the money, as a capped spread, held to settlement."""

from __future__ import annotations

import time
from typing import Any

import pytest
from sqlmodel import select

from app.core.db import session
from app.core.models import Decision, Structure
from app.options import decay
from app.options.strategies import EXPIRY_DECAY, ZERO_HERO
from tests.conftest import load_quotes

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


async def test_the_cycle_sells_decay_once_per_symbol(env: Any) -> None:
    from app.trading.zerohero import ZeroHero

    env.feed.load(hours=3.0)
    env.configure(zh_enabled=True)
    zhx = ZeroHero(env.desk, env.trading, env.settings, env.hub)
    env.desk.realised = lambda u: {"reference": 0.10, "floor": 0.08}  # type: ignore[method-assign]
    expiry = zhx.expiry("BTC")
    dec = Decision(ts=time.time(), underlying="BTC", action="skip", strategy=ZERO_HERO, expiry=expiry,
                   regime={"window_open": True}, reason="no momentum break")
    await zhx._decay(dec, "BTC")
    assert dec.action == "open", dec.reason
    sold = dec.orders["decay"]["paper"]
    assert sold["status"] == "open" and sold["qty"] >= 1 and "decay: sold on paper" in dec.reason
    with session() as s:
        [st] = s.exec(select(Structure).where(Structure.strategy == EXPIRY_DECAY)).all()
    assert st.max_loss is not None  # capped by the wing, so it may trade on real money too
    # One held decay spread on BTC: the next pass sells nothing.
    again = Decision(ts=time.time(), underlying="BTC", action="skip", strategy=ZERO_HERO, expiry=expiry, regime={"window_open": True})
    await zhx._decay(again, "BTC")
    assert again.action == "skip" and not again.orders


def test_the_guard_holds_a_decay_spread_to_settlement(env: Any) -> None:
    from app.trading.guard import Guard

    now = time.time()
    st = Structure(id="d2", broker="paper", underlying="BTC", strategy=EXPIRY_DECAY, source="zerohero", status="open",
                   expiry=now + 30 * 60, qty=1, entry_cash=1.0, max_loss=4.0, take_profit_pct=50, stop_loss_pct=100, opened_at=now)
    reason = Guard(env.trading, env.settings, env.hub).exit_reason(st, [], env.settings(), now)
    assert not (reason or "").startswith("time exit")  # 30 minutes out, an ordinary credit would be closed
