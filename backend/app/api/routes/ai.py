"""The language model: its status, the market brief, and a connectivity test."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.ai.llm import LLMError
from app.api.deps import EngineDep
from app.errors import Refused

router = APIRouter(prefix="/ai", tags=["ai"])


class BriefRequest(BaseModel):
    underlying: str = "BTC"


@router.get("/status")
def status(engine: EngineDep) -> dict[str, Any]:
    return engine.llm.status() | {"ready": engine.analyst.ready()}


@router.get("/brief")
def latest_brief(engine: EngineDep, underlying: str = "BTC", limit: int = 1) -> list[dict[str, Any]]:
    return engine.analyst.latest(underlying, min(max(limit, 1), 50))


@router.post("/brief")
async def new_brief(body: BriefRequest, engine: EngineDep) -> dict[str, Any]:
    if why := engine.analyst.ready():
        raise Refused(why)
    return await engine.analyst.brief(body.underlying, engine.desk.regime(body.underlying))


@router.post("/test")
async def test(engine: EngineDep) -> dict[str, Any]:
    """One tiny request through the configured models, in order — the same path a review takes."""
    try:
        answer, meta = await engine.llm.json('Reply with JSON only: {"ok": true}', {"ping": True}, max_tokens=20)
    except LLMError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "answer": answer} | meta
