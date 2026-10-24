"""The auto-trader's decisions: rules alone, the model's review, and what happens when it fails."""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import select

from app.ai.analyst import ALLOWED, Analyst, clean_review, digest
from app.ai.llm import LLM, LLMError
from app.core.db import session
from app.core.models import Structure
from app.trading.autotrader import AutoTrader, lots_for_risk


class StubLLM(LLM):
    def __init__(self, settings: Any, answer: Any) -> None:
        super().__init__(settings)
        self.answer = answer
        self.calls = 0

    async def json(self, system: str, payload: dict[str, Any], max_tokens: int = 900) -> tuple[dict[str, Any], dict[str, Any]]:
        self.calls += 1
        self.payload = payload
        if isinstance(self.answer, Exception):
            raise self.answer
        answer = self.answer(payload) if callable(self.answer) else self.answer
        return answer, {"model": "stub/model", "ms": 1, "fallback": False}


def make(env: Any, answer: Any = None, **settings: Any) -> tuple[AutoTrader, StubLLM]:
    env.configure(max_spread_pct=100, min_credit_width_pct=0, min_score=0, min_pop_pct=0, **settings)
    llm = StubLLM(env.settings, answer)
    analyst = Analyst(llm, env.settings, env.hub)
    analyst.ready = lambda: ""  # type: ignore[method-assign]
    return AutoTrader(env.desk, env.trading, analyst, env.settings, env.hub), llm


def opened() -> list[Structure]:
    with session() as s:
        return list(s.exec(select(Structure).where(Structure.status == "open")).all())


async def test_rules_alone_open_the_best_candidate_sized_by_risk(env: Any) -> None:
    auto, llm = make(env, ai_enabled=False)
    d = await auto.decide("BTC", ["paper"])
    assert d["action"] == "open", d["reason"]
    assert llm.calls == 0
    [st] = opened()
    assert st.source == "auto" and st.strategy == d["strategy"] and st.decision_id == d["id"]
    risk_budget = 1000 * env.settings().risk_per_trade_pct / 100
    assert st.max_loss <= risk_budget  # the worst case fits the risk budget
    assert d["orders"]["paper"]["qty"] == st.qty


async def test_the_model_can_only_choose_a_listed_candidate(env: Any) -> None:
    auto, _ = make(env, {"decision": "trade", "candidate": "naked_call", "confidence": "high"}, ai_enabled=True)
    d = await auto.decide("BTC", ["paper"])
    assert d["action"] == "skip" and d["ai"]["invalid_choice"]
    assert not opened()


async def test_the_model_picks_which_candidate_trades(env: Any) -> None:
    def pick_last(payload: dict[str, Any]) -> dict[str, Any]:
        return {"decision": "trade", "candidate": payload["candidates"][-1]["id"], "confidence": "medium", "reasons": ["r"]}

    auto, llm = make(env, pick_last, ai_enabled=True)
    d = await auto.decide("BTC", ["paper"])
    assert d["action"] == "open"
    assert d["strategy"] == llm.payload["candidates"][-1]["id"]


async def test_no_model_answering_falls_back_to_the_rules_unless_required(env: Any) -> None:
    auto, _ = make(env, LLMError("every model failed"), ai_enabled=True, ai_required=False)
    d = await auto.decide("BTC", ["paper"])
    assert d["action"] == "open" and "rules decided" in d["reason"]

    auto, _ = make(env, LLMError("every model failed"), ai_enabled=True, ai_required=True, max_per_underlying=2)
    d = await auto.decide("BTC", ["paper"])
    assert d["action"] == "skip" and "required" in d["reason"]


async def test_a_broker_with_no_room_is_skipped_before_anything_is_asked(env: Any) -> None:
    auto, llm = make(env, {"decision": "skip"}, ai_enabled=True)
    await make(env, ai_enabled=False)[0].decide("BTC", ["paper"])
    d = await auto.decide("BTC", ["paper"])
    assert d["action"] == "skip" and "no room" in d["reason"]
    assert llm.calls == 0


def test_what_goes_to_the_model_is_market_data_only(env: Any) -> None:
    regime = env.desk.regime("BTC")
    payload = digest("BTC", regime, [])
    assert set(payload) <= ALLOWED
    text = str(payload).lower()
    for word in ("balance", "equity", "cash", "api_key", "secret", "pnl"):
        assert word not in text


def test_review_answers_are_shaped_and_capped() -> None:
    out = clean_review({"decision": "trade", "candidate": "iron_condor", "confidence": "certain", "reasons": ["x" * 999] * 9}, ["iron_condor"])
    assert out["decision"] == "trade" and out["confidence"] == "low"
    assert len(out["reasons"]) == 4 and all(len(r) <= 280 for r in out["reasons"])


def test_lots_are_sized_on_the_worst_case() -> None:
    from app.settings import Settings

    s = Settings(risk_per_trade_pct=2, max_lots=50)
    assessment = {"analysis": {"max_loss": 1.5, "round_trip_fees": 0.1, "net_premium": 0.2}}
    assert lots_for_risk(1000, assessment, s) == 12  # 20 / 1.6
    assert lots_for_risk(50, assessment, s) == 0
    unbounded = {"analysis": {"max_loss": None, "round_trip_fees": 0.0, "net_premium": 0.5}}
    assert lots_for_risk(1000, unbounded, s) == pytest.approx(40)  # sized on the stop: 100% of the credit
