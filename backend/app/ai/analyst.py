"""What the language model is asked, what it is shown, and what of its answer is kept.

Two jobs:
  - the **market brief**: the measured numbers read back as a few plain sentences;
  - the **trade review**: the auto-trader's candidates (already built, priced and checked by the
    rules) put in front of a model that may approve one of them or skip. It cannot invent a strike,
    a size or a price: an answer naming anything but a listed candidate is a skip, and the hard risk
    gates run after it whatever it said.

What leaves the machine is market data only. `digest()` builds the payload from an allowlist
(`ALLOWED`), enforced in code: no balance, position, order, P&L or key is ever in it.
"""

from __future__ import annotations

import time
from typing import Any

from sqlmodel import col, select

from app.ai.llm import LLM, LLMError
from app.core.db import session
from app.core.events import EventHub
from app.core.models import AiBrief
from app.settings import Settings

CONFIDENCE = ("high", "medium", "low")

BRIEF_SYSTEM = """You are the market writer inside TradeBuddy, a crypto options desk.

You receive one JSON digest of numbers ALREADY measured for one underlying: spot, implied volatility
(ATM IV) against realised volatility, the trend read, the option chain's positioning (put/call ratio,
max pain, OI walls, skew) and the move the options price to expiry, plus each strategy's rule score.

Write what kind of options market this is, for someone about to choose a structure.

Rules:
- Use ONLY numbers in the digest; quote the number behind each claim. Never invent a price or level.
- Never predict a direction or give a target, an entry or a stop. Describe the present.
- Say plainly when the volatility premium is small or negative: selling options then has no edge.
- Short, plain sentences. No hype, no disclaimers.

Answer with JSON only:
{"headline": "<one sentence>",
 "volatility": "<1-2 sentences: IV vs realised and what it means for buyers and sellers>",
 "positioning": "<1-2 sentences: put/call, max pain, walls, skew>",
 "summary": "<2-3 sentences pulling it together>",
 "watch": ["<up to 3 things that would change this read>"],
 "confidence": "high|medium|low"}"""

REVIEW_SYSTEM = """You are the risk reviewer on TradeBuddy's automated crypto options desk.

The rules engine has measured the market and built up to three candidate structures. Every candidate
already passed the hard checks (liquidity, probability of profit, edge at realised volatility). You
see the market digest and the candidates with their numbers. Decide whether to trade ONE of them now
or to skip this cycle.

Rules:
- You may only choose a candidate by its exact "id", or skip. You cannot change strikes, size or price.
- Prefer SKIP when the numbers conflict, the volatility premium is thin, or a trend is building
  against a neutral structure. Skipping costs nothing; a bad trade does.
- Prefer defined risk. Weigh: edge at realised vol, probability of profit, worst case vs credit,
  how the regime fits the structure.
- Use only numbers in the input. Do not invent levels or predict a direction.

Answer with JSON only:
{"decision": "trade|skip",
 "candidate": "<id of the chosen candidate, or empty when skipping>",
 "confidence": "high|medium|low",
 "reasons": ["<up to 4 short reasons citing numbers>"],
 "risks": ["<up to 3 risks to watch on this trade>"]}"""

# The only top-level keys a digest may contain — the gate on what leaves this machine.
ALLOWED = frozenset({
    "underlying", "spot", "hours_to_expiry", "regime", "direction", "direction_parts", "atm_iv_pct",
    "realised_vol_24h_pct", "realised_vol_7d_pct", "vol_premium_pts", "change_24h_pct", "rsi_1h",
    "range_position_24h", "implied_move_pct", "max_pain", "pcr_oi", "skew_25d", "call_wall", "put_wall",
    "strategy_scores", "candidates",
})


def _pct(v: float | None) -> float | None:
    return None if v is None else round(100 * v, 2)


def digest(underlying: str, regime: dict[str, Any], candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    m = regime.get("metrics") or {}
    out: dict[str, Any] = {
        "underlying": underlying,
        "spot": m.get("spot"),
        "hours_to_expiry": regime.get("hours_to_expiry"),
        "regime": regime.get("label"),
        "direction": regime.get("direction"),
        "direction_parts": regime.get("direction_parts"),
        "atm_iv_pct": _pct(m.get("atm_iv")),
        "realised_vol_24h_pct": _pct(m.get("rv_24h")),
        "realised_vol_7d_pct": _pct(m.get("rv_7d")),
        "vol_premium_pts": _pct(m.get("vol_premium")),
        "change_24h_pct": m.get("change_24h_pct"),
        "rsi_1h": m.get("rsi_1h"),
        "range_position_24h": m.get("range_position_24h"),
        "implied_move_pct": m.get("implied_move_pct"),
        "max_pain": m.get("max_pain"),
        "pcr_oi": m.get("pcr_oi"),
        "skew_25d": m.get("skew_25d"),
        "call_wall": m.get("call_wall"),
        "put_wall": m.get("put_wall"),
        "strategy_scores": {k: v.get("score") for k, v in (regime.get("scores") or {}).items()},
    }
    if candidates is not None:
        out["candidates"] = [candidate_digest(c) for c in candidates]
    return {k: v for k, v in out.items() if k in ALLOWED}


def candidate_digest(c: dict[str, Any]) -> dict[str, Any]:
    """Per lot: a candidate's shape and numbers, nothing about the account it would be sized against."""
    a = c["analysis"]
    return {
        "id": c["strategy"],
        "structure": c["label"],
        "legs": [f"{leg['side']} {leg['kind']} {leg['strike']:g}" for leg in c["plan"]["legs"]],
        "premium": a["net_premium"], "kind": a["kind"],
        "max_profit": a["max_profit"], "max_loss": a["max_loss"], "breakevens": a["breakevens"],
        "pop_at_iv_pct": a["pop"], "pop_at_realised_pct": a.get("pop_realised"),
        "edge_at_realised_vol": a.get("expected_value_realised"), "fees_round_trip": a.get("round_trip_fees"),
        "rule_score": c.get("score"), "score_reasons": c.get("reasons"),
    }


def _lines(value: Any, n: int, width: int = 280) -> list[str]:
    return [str(x)[:width] for x in (value if isinstance(value, list) else [])][:n]


def clean_brief(answer: dict[str, Any]) -> dict[str, Any]:
    return {
        "headline": str(answer.get("headline", ""))[:300],
        "volatility": str(answer.get("volatility", ""))[:500],
        "positioning": str(answer.get("positioning", ""))[:500],
        "summary": str(answer.get("summary", ""))[:900],
        "watch": _lines(answer.get("watch"), 3),
        "confidence": answer.get("confidence") if answer.get("confidence") in CONFIDENCE else "low",
    }


def clean_review(answer: dict[str, Any], ids: list[str]) -> dict[str, Any]:
    """An answer naming anything but a listed candidate is a skip: the model chooses, it never invents."""
    choice = str(answer.get("candidate") or "")
    trade = answer.get("decision") == "trade" and choice in ids
    return {
        "decision": "trade" if trade else "skip",
        "candidate": choice if trade else "",
        "confidence": answer.get("confidence") if answer.get("confidence") in CONFIDENCE else "low",
        "reasons": _lines(answer.get("reasons"), 4),
        "risks": _lines(answer.get("risks"), 3),
        "invalid_choice": answer.get("decision") == "trade" and choice not in ids,
    }


class Analyst:
    def __init__(self, llm: LLM, settings: Any, hub: EventHub) -> None:
        self.llm = llm
        self.settings: Any = settings
        self.hub = hub

    async def brief(self, underlying: str, regime: dict[str, Any]) -> dict[str, Any]:
        row = AiBrief(ts=time.time(), underlying=underlying)
        try:
            answer, meta = await self.llm.json(BRIEF_SYSTEM, digest(underlying, regime))
            row.content, row.model, row.latency_ms = clean_brief(answer), meta["model"], meta["ms"]
        except LLMError as exc:
            row.error = str(exc)[:1000]
        with session() as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        self.hub.publish("AiBrief", row.content["headline"] if row.content else f"brief failed: {row.error}",
                         "info" if row.content else "warning", persist=not row.content, underlying=underlying, id=row.id)
        return row.model_dump()

    async def review(self, underlying: str, regime: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Raises LLMError when no model answered."""
        answer, meta = await self.llm.json(REVIEW_SYSTEM, digest(underlying, regime, candidates), max_tokens=700)
        return clean_review(answer, [c["strategy"] for c in candidates]) | {"model": meta["model"], "ms": meta["ms"], "fallback": meta["fallback"]}

    @staticmethod
    def latest(underlying: str, limit: int = 1) -> list[dict[str, Any]]:
        with session() as s:
            rows = s.exec(select(AiBrief).where(AiBrief.underlying == underlying).order_by(col(AiBrief.ts).desc()).limit(limit)).all()
        return [r.model_dump() for r in rows]

    def ready(self) -> str:
        """Why the model cannot be asked, or ""."""
        s: Settings = self.settings()
        if not s.ai_enabled:
            return "AI is switched off in Settings"
        if not any(m["key_present"] for m in self.llm.status()["models"]):
            return "no configured model has its provider key in the environment"
        return ""
