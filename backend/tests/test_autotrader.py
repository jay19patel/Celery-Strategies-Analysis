"""The auto-trader's decisions: rules alone, the model's review, and what happens when it fails."""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import select

from app.ai.analyst import ALLOWED, Analyst, clean_review, digest
from app.ai.llm import LLM, LLMError
from app.core.db import session
from app.core.models import Structure
from app.errors import SettingsError
from app.settings import Settings
from app.trading.autotrader import AutoTrader, lots_for_risk
from app.trading.risk import day_start, trading_day


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


async def test_a_position_on_the_perpetual_does_not_take_the_options_slot(env: Any) -> None:
    """Each instrument holds its own slot: BTC futures never block a BTC option structure."""
    auto, _ = make(env, ai_enabled=False, max_per_underlying=1)
    await env.trading.open_directional("paper", "BTC", "buy", 1, 79_500, 81_000, "supertrend", "auto", "req-perp-room", "")
    d = await auto.decide("BTC", ["paper"])
    assert d["action"] == "open", d["reason"]

    # The option structure it just opened does, though — that is the per-underlying cap doing its job.
    again = await auto.decide("BTC", ["paper"])
    assert again["action"] == "skip" and "no room" in again["reason"] and "options" in again["reason"]


def test_what_goes_to_the_model_is_market_data_only(env: Any) -> None:
    regime = env.desk.regime("BTC")
    payload = digest("BTC", regime, [])
    assert set(payload) <= ALLOWED
    text = str(payload).lower()
    for word in ("balance", "equity", "cash", "api_key", "secret", "pnl"):
        assert word not in text


async def test_the_bar_eases_through_a_day_with_no_entry_and_resets_on_one(env: Any) -> None:
    """Pacing towards about a trade a day: the score bar falls as an empty day runs out, the first
    entry puts it back, and a floor at or above min_score is simply off."""
    auto, _ = make(env, ai_enabled=False, day_timezone="UTC")  # the engine's own bar stays at 0
    paced = env.settings().model_copy(update={"min_score": 60, "min_score_floor": 40})
    day = day_start("UTC", trading_day("UTC"))

    assert auto._bar("BTC", paced, day)[0] == 60  # the day opens at the full bar
    midday, note = auto._bar("BTC", paced, day + 43_200)
    assert midday == 50 and "eased from 60 to 50" in note
    assert auto._bar("BTC", paced, day + 86_399)[0] == 40  # and reaches the floor by its end
    assert auto._bar("ETH", paced, day + 43_200)[0] == 50  # each underlying is paced on its own

    off = paced.model_copy(update={"min_score_floor": 60})
    assert auto._bar("BTC", off, day + 43_200) == (60, "")  # a floor at the bar is no pacing at all

    # One option structure today and the bar is back up: the floor paces entries, it never adds one.
    assert (await auto.decide("BTC", ["paper"]))["action"] == "open"
    assert auto._bar("BTC", paced, day + 43_200) == (60, "")


async def test_a_position_on_the_perpetual_is_not_the_options_desks_entry_for_the_day(env: Any) -> None:
    auto, _ = make(env, ai_enabled=False, day_timezone="UTC")
    paced = env.settings().model_copy(update={"min_score": 60, "min_score_floor": 40})
    day = day_start("UTC", trading_day("UTC"))
    await env.trading.open_directional("paper", "BTC", "buy", 1, 79_500, 81_000, "supertrend", "auto", "req-pace", "")
    assert auto._bar("BTC", paced, day + 43_200)[0] == 50


def test_a_floor_above_the_bar_is_refused() -> None:
    from app.settings import apply_changes

    with pytest.raises(SettingsError, match="eases down"):
        apply_changes(Settings(min_score=60), {"min_score_floor": 70})


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
