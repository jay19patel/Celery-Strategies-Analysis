"""The auto-trader's decisions — the rules alone decide — and the market brief the model writes."""

from __future__ import annotations

import time
from typing import Any

import pytest
from sqlmodel import select

import app.trading.autotrader as autotrader_mod
from app.ai.analyst import ALLOWED, Analyst, digest
from app.ai.llm import LLM
from app.core.db import session
from app.core.models import Structure
from app.settings import Settings, StrategyConfig
from app.strategies import registry
from app.trading.autotrader import AutoTrader, lots_for_risk


class StubLLM(LLM):
    def __init__(self, settings: Any, answer: Any) -> None:
        super().__init__(settings)
        self.answer = answer
        self.calls = 0

    async def json(self, system: str, payload: dict[str, Any], max_tokens: int = 900) -> tuple[dict[str, Any], dict[str, Any]]:
        self.calls += 1
        self.system, self.payload = system, payload
        if isinstance(self.answer, Exception):
            raise self.answer
        answer = self.answer(payload) if callable(self.answer) else self.answer
        return answer, {"model": "stub/model", "ms": 1, "fallback": False}


OPEN_GATE = {"min_credit_width_pct": 0, "min_pop_pct": 0}


@pytest.fixture(autouse=True)
def any_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """The captured chain is one quiet afternoon; the score bar would refuse it all day."""
    monkeypatch.setattr(autotrader_mod, "MIN_SCORE", 0)


def make(env: Any, only: list[str] | None = None, **settings: Any) -> AutoTrader:
    names = only or [n for n in registry.names("options") if registry.option(n).default_on]  # type: ignore[union-attr]
    strategies = {n: StrategyConfig(enabled=n in names, params=OPEN_GATE if n != "expiry_decay" else {}) for n in registry.names("options")}
    env.configure(max_spread_pct=100, strategies=strategies, **settings)
    return AutoTrader(env.desk, env.trading, env.settings, env.hub)


def opened() -> list[Structure]:
    with session() as s:
        return list(s.exec(select(Structure).where(Structure.status == "open")).all())


async def test_rules_alone_open_the_best_candidate_sized_by_risk(env: Any) -> None:
    auto = make(env)
    d = await auto.decide("BTC", ["paper"])
    assert d["action"] == "open", d["reason"]
    assert "rules chose" in d["reason"]
    [st] = opened()
    assert st.source == "auto" and st.strategy == d["strategy"] and st.decision_id == d["id"]
    assert st.strategy_version == registry.get(st.strategy).version  # type: ignore[union-attr]
    assert st.reason.startswith("rules chose") and "score" in st.reason  # why it opened, kept on the position
    risk_budget = 1000 * env.settings().risk_per_trade_pct / 100
    assert st.max_loss <= risk_budget  # the worst case fits the risk budget
    assert d["orders"]["paper"]["qty"] == st.qty





async def test_a_broker_with_no_room_is_skipped_before_anything_is_built(env: Any) -> None:
    auto = make(env)
    await auto.decide("BTC", ["paper"])
    d = await auto.decide("BTC", ["paper"])
    assert d["action"] == "skip" and "no room" in d["reason"] and not d["candidates"]


async def test_an_automatic_perpetual_position_takes_the_underlyings_one_slot(env: Any) -> None:
    """One automatic position per underlying, across every family: a BTC short and a BTC condor
    are one bet on BTC, not two. A dashboard ticket is the user's own and takes no slot."""
    auto = make(env)
    await env.trading.open_directional("paper", "BTC", "buy", 1, 79_500, 81_000, "manual", "manual", "req-perp-manual", "")
    d = await auto.decide("BTC", ["paper"])
    assert d["action"] == "open", d["reason"]

    again = await auto.decide("BTC", ["paper"])
    assert again["action"] == "skip" and "no room" in again["reason"] and "automatic BTC position" in again["reason"]
    assert again["id"] is None and len(AutoTrader.decisions()) == 1  # a skip before any broker is not stored
    assert "no room" in auto.latest["BTC"]["reason"]  # ...but its reason is still shown live


async def test_a_strategy_not_switched_to_live_never_reaches_delta(env: Any) -> None:
    env.brokers["delta"] = env.paper  # a second broker that fills like paper
    auto = make(env, delta_active=True, auto_delta=True)
    d = await auto.decide("BTC", ["paper", "delta"])
    assert d["orders"]["paper"]["status"] == "open"
    assert "not switched to trade on delta" in d["orders"]["delta"]["refused"]


def test_what_goes_to_the_model_is_market_data_only(env: Any) -> None:
    regime = env.desk.regime("BTC")
    payload = digest("BTC", regime)
    assert set(payload) <= ALLOWED
    text = str(payload).lower()
    for word in ("balance", "equity", "cash", "api_key", "secret", "pnl"):
        assert word not in text


def test_the_market_block_reaches_the_model_and_is_still_market_data_only(env: Any) -> None:
    """It is allowlisted twice: `MARKET_ALLOWED` inside the block, `ALLOWED` on the block itself."""
    payload = digest("BTC", env.desk.regime("BTC"), market={"rsi_1h": 51.0, "funding_pct": 0.004, "equity": 10_000})
    assert payload["market"] == {"rsi_1h": 51.0, "funding_pct": 0.004}
    assert set(payload) <= ALLOWED


async def test_each_desk_gets_its_own_brief(env: Any) -> None:
    llm = StubLLM(env.settings, lambda _p: {"headline": "flat", "confidence": "medium", "watch": ["a break"]})
    analyst = Analyst(llm, env.settings, env.hub)
    regime = env.desk.regime("BTC")

    await analyst.brief("BTC", regime, {"rsi_1h": 51.0}, "market")
    assert "perpetual futures desk" in llm.system

    [market] = analyst.latest("BTC", kind="market")
    assert market["content"]["headline"] == "flat" and "levels" in market["content"]
    assert analyst.latest("BTC", kind="options") == []  # one desk's brief never shows on the other

    await analyst.brief("BTC", regime, None, "options")
    assert "crypto options desk" in llm.system
    [options] = analyst.latest("BTC", kind="options")
    assert "volatility" in options["content"] and "levels" not in options["content"]



def test_lots_are_sized_on_the_worst_case() -> None:

    s = Settings(risk_per_trade_pct=2, max_lots=50)  # 2% of 1,000 is 20 to risk
    assessment = {"analysis": {"max_loss": 1.5, "round_trip_fees": 0.1, "net_premium": 0.2}}
    assert lots_for_risk(1000, assessment, s) == 12  # 20 / 1.6
    assert lots_for_risk(50, assessment, s) == 0
    unbounded = {"analysis": {"max_loss": None, "round_trip_fees": 0.0, "net_premium": 0.5}}
    assert lots_for_risk(1000, unbounded, s) == pytest.approx(40)  # sized on the stop: 100% of the credit


async def test_a_scheduled_event_before_expiry_keeps_every_seller_out(env: Any) -> None:
    from datetime import UTC, datetime

    soon = datetime.fromtimestamp(time.time() + 3600, UTC).strftime("%Y-%m-%d %H:%M")
    auto = make(env, event_times=[soon])
    d = await auto.decide("BTC", ["paper"])
    assert d["action"] == "skip"
    statuses = {c["strategy"]: c.get("status", "") for c in d["candidates"]}
    sellers = [k for k in statuses if registry.option(k).premium == "credit"]  # type: ignore[union-attr]
    assert sellers and all("scheduled event" in statuses[k] for k in sellers)
    assert not opened()

    auto = make(env, event_times=[])
    assert (await auto.decide("BTC", ["paper"]))["action"] == "open"  # no event, the same desk trades


async def test_a_strategy_stopped_out_today_is_not_bought_again_on_that_underlying(env: Any) -> None:
    """7 Nov: the ETH long straddle was stopped and re-bought nine times in one morning."""
    auto = make(env)
    first = await auto.decide("BTC", ["paper"])
    assert first["action"] == "open", first["reason"]
    await env.trading.close(first["orders"]["paper"]["structure_id"], "stop loss: -9.9 reached 50% stop (held 3 checks)")
    again = await auto.decide("BTC", ["paper"])
    refused = next(c for c in again["candidates"] if c["strategy"] == first["strategy"])
    assert "stopped out" in refused["status"]
    assert again["strategy"] != first["strategy"]  # another idea may still trade
