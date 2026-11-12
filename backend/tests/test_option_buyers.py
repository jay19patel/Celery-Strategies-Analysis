"""The option buyers: each buys only when the option is cheap at realised volatility, with the trend."""

from __future__ import annotations

import time
from typing import Any

import pytest
from sqlmodel import select

from app.core.db import session
from app.core.models import Structure
from app.errors import Refused
from app.settings import StrategyConfig
from app.strategies.base import OptionContext
from app.strategies.expiry_hero import ExpiryHero
from app.strategies.long_option import LongOption
from app.trading.autotrader import AutoTrader
from app.trading.guard import Guard
from tests.conftest import load_quotes

METRICS = {"atm_iv": 0.40, "vol_premium": -0.08, "vol_premium_floor": -0.05, "rv_ratio": 1.0}


def ctx(direction: float, floor: float, hours: float) -> OptionContext:
    now = time.time()
    quotes = load_quotes(now, hours)
    regime = {"direction": direction, "metrics": METRICS, "label": "trending up", "gamma": None}
    return OptionContext("BTC", quotes[0].spot or 80_000.0, now, quotes, lambda e: regime,
                         {"floor": floor, "reference": floor}, lambda price: 0.0, 100.0)


def test_long_option_buys_with_a_strong_trend_and_cheap_iv_and_waits_without_one() -> None:
    lo = LongOption()
    assert lo.score({"direction": 0.6, "metrics": METRICS})[0] >= 60
    assert lo.score({"direction": 0.1, "metrics": METRICS})[0] < 60
    rich = METRICS | {"vol_premium_floor": 0.10}  # IV well above what the market moves: a buyer overpays
    assert lo.score({"direction": 0.6, "metrics": rich})[0] < 60
    c = ctx(0.6, 0.5, 20)
    plan = lo.build(c, c.quotes[0].expiry)
    assert plan is not None and [(leg.kind, leg.side) for leg in plan.legs] == [("call", "buy")]
    down = ctx(-0.6, 0.5, 20)
    assert lo.build(down, down.quotes[0].expiry).legs[0].kind == "put"  # type: ignore[union-attr]
    flat = ctx(0.1, 0.5, 20)
    with pytest.raises(Refused, match="weaker"):
        lo.build(flat, flat.quotes[0].expiry)


def test_expiry_hero_buys_an_otm_option_only_under_its_fair_value() -> None:
    hero = ExpiryHero()
    c = ctx(0.5, 2.0, 3)  # the market moving far more than its IV: OTM calls are cheap
    plan = hero.build(c, c.quotes[0].expiry)
    assert plan is not None and plan.legs[0].side == "buy" and plan.legs[0].kind == "call"
    assert plan.legs[0].strike > c.spot and 0.08 <= abs(plan.legs[0].delta or 0) <= 0.30
    calm = ctx(0.5, 0.01, 3)  # barely moving: every OTM option is dear
    with pytest.raises(Refused, match="fair value"):
        hero.build(calm, calm.quotes[0].expiry)
    assert hero.score({"direction": 0.05})[0] == 0  # no trend, no direction to bet on


async def test_the_auto_trader_buys_a_cheap_option_and_holds_it_without_a_premium_stop(env: Any) -> None:
    env.configure(max_spread_pct=100, strategies={"long_option": StrategyConfig(enabled=True)} | {
        n: StrategyConfig(enabled=False) for n in ("iron_condor", "iron_butterfly", "bull_put_spread", "bear_call_spread", "expiry_decay")
    })
    env.desk.read_regime = lambda u, e: {"direction": 0.7, "metrics": METRICS, "label": "trending up", "gamma": None}  # type: ignore[method-assign]
    env.desk.realised = lambda u: {"reference": 3.0, "floor": 3.0, "forecast_24h": None}  # type: ignore[method-assign]
    d = await AutoTrader(env.desk, env.trading, env.settings, env.hub).decide("BTC", ["paper"])
    assert d["action"] == "open", d["reason"]
    with session() as s:
        [st] = s.exec(select(Structure).where(Structure.strategy == "long_option")).all()
    assert st.entry_cash < 0 and st.max_loss is not None and st.take_profit_pct == 100 and st.stop_loss_pct == 100
    soon = time.time()
    st.expiry = soon + 30 * 60  # inside the time exit a seller would get
    assert not (Guard(env.trading, env.settings, env.hub).exit_reason(st, [], env.settings(), soon) or "").startswith("time exit")


async def test_the_auto_trader_refuses_a_buy_when_options_are_dear(env: Any) -> None:
    env.configure(max_spread_pct=100, strategies={"long_option": StrategyConfig(enabled=True)} | {
        n: StrategyConfig(enabled=False) for n in ("iron_condor", "iron_butterfly", "bull_put_spread", "bear_call_spread", "expiry_decay")
    })
    env.desk.read_regime = lambda u, e: {"direction": 0.7, "metrics": METRICS, "label": "trending up", "gamma": None}  # type: ignore[method-assign]
    env.desk.realised = lambda u: {"reference": 0.01, "floor": 0.01, "forecast_24h": None}  # type: ignore[method-assign]
    d = await AutoTrader(env.desk, env.trading, env.settings, env.hub).decide("BTC", ["paper"])
    assert d["action"] == "skip" and "edge at realised vol" in d["reason"]
