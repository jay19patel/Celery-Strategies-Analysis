"""Zero-hero: the expiry-day scan, its decisions, and the record shadow mode is there to build."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import EngineDep

router = APIRouter(prefix="/zerohero", tags=["zero hero"])


@router.get("/status")
def status(engine: EngineDep, underlying: str = "BTC") -> dict[str, Any]:
    job = next((j for j in engine.scheduler.status() if j["id"] == "zerohero.scan"), None)
    return engine.zerohero.status(underlying) | {"job": job, "record": engine.zerohero.record()}


@router.get("/scan")
def scan(engine: EngineDep, underlying: str = "BTC") -> dict[str, Any]:
    """The live scan. Read-only: it opens nothing and writes no row, so the page may poll it."""
    return engine.zerohero.scan(underlying).to_dict()


@router.get("/decisions")
def decisions(engine: EngineDep, underlying: str = "", action: str = "", limit: int = 50) -> list[dict[str, Any]]:
    return engine.zerohero.decisions(underlying, action, limit)


@router.get("/record")
def record(engine: EngineDep, days: float = 30) -> dict[str, Any]:
    return engine.zerohero.record(days)


@router.post("/run")
async def run_now(engine: EngineDep) -> dict[str, Any]:
    s = engine.settings()
    if not s.zh_enabled:
        return {"decisions": [], "note": "zero-hero is off: switch it on in Settings first"}
    return {"decisions": [await engine.zerohero.decide(u) for u in s.underlyings]}
