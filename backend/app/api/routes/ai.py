"""The language model: its status, the market brief, and a connectivity test."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.ai.analyst import BRIEFS
from app.ai.llm import LLMError
from app.api.deps import EngineDep
from app.errors import Refused

router = APIRouter(prefix="/ai", tags=["ai"])


class BriefRequest(BaseModel):
    underlying: str = "BTC"
    kind: str = "options"  # which desk is asking: the options desk or the market (perpetual) desk


@router.get("/status")
def status(engine: EngineDep) -> dict[str, Any]:
    return engine.llm.status() | {"ready": engine.analyst.ready()}


@router.get("/brief")
def latest_brief(engine: EngineDep, underlying: str = "BTC", limit: int = 1, kind: str = "options") -> list[dict[str, Any]]:
    if kind not in BRIEFS:
        raise Refused(f"unknown brief {kind!r}")
    return engine.analyst.latest(underlying, min(max(limit, 1), 50), kind)


@router.post("/brief")
async def new_brief(body: BriefRequest, engine: EngineDep) -> dict[str, Any]:
    if body.kind not in BRIEFS:
        raise Refused(f"unknown brief {body.kind!r}")
    if why := engine.analyst.ready():
        raise Refused(why)
    analysis = engine.market.latest.get(engine.market.perpetual(body.underlying)) or {}
    context = analysis.get("context") or {}
    outlook = await engine.market.outlook(body.underlying)
    structure = await engine.market.structure(body.underlying)
    pulse = await engine.market.pulse(body.underlying)
    market = {
        **context,
        "alignment": outlook.get("alignment"),
        "timeframes": [
            {k: frame.get(k) for k in ("interval", "label", "trend", "direction", "enough", "note")}
            for frame in outlook.get("timeframes", [])
        ],
        "structure": {
            "bias": (structure.get("smc") or {}).get("bias"),
            "swing_high": (structure.get("smc") or {}).get("swing_high"),
            "swing_low": (structure.get("smc") or {}).get("swing_low"),
            "profile": {k: (structure.get("profile") or {}).get(k) for k in ("poc", "vah", "val")},
            "nearby_above": (structure.get("nearby") or {}).get("above", [])[:3],
            "nearby_below": (structure.get("nearby") or {}).get("below", [])[:3],
            "last_break": ((structure.get("smc") or {}).get("breaks") or [None])[-1],
        },
        "insights": [
            {k: item.get(k) for k in ("title", "detail", "bias", "severity")}
            for item in analysis.get("insights", [])[:12]
        ],
        "flow": {
            "bias": (structure.get("flow") or {}).get("bias"),
            "delta_pct": (structure.get("flow") or {}).get("delta_pct"),
            "approx": (structure.get("flow") or {}).get("approx"),
        },
        "pulse": {k: pulse.get(k) for k in ("state", "headline", "way", "note")},
    }
    return await engine.analyst.brief(body.underlying, engine.desk.regime(body.underlying), market, body.kind)


@router.post("/test")
async def test(engine: EngineDep) -> dict[str, Any]:
    """One tiny request through the configured models, in order — the same path a review takes."""
    try:
        answer, meta = await engine.llm.json('Reply with JSON only: {"ok": true}', {"ping": True}, max_tokens=20)
    except LLMError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "answer": answer} | meta
