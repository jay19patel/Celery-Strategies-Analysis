"""The auto-trader: its state, its decisions, and running a cycle now."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import EngineDep
from app.errors import NotFound
from app.strategies import registry
from app.trading.autotrader import INTERVAL_MINUTES, MIN_SCORE

router = APIRouter(prefix="/auto", tags=["auto"])


class Toggle(BaseModel):
    broker: str
    enabled: bool


@router.get("/status")
def status(engine: EngineDep) -> dict[str, Any]:
    s = engine.settings()
    job = next((j for j in engine.scheduler.status() if j["id"] == "auto.cycle"), None)
    return {
        "brokers": engine.broker_status(),
        "job": job,
        "config": {
            "interval_minutes": INTERVAL_MINUTES, "min_score": MIN_SCORE, "underlyings": s.underlyings,
            "strategies": list(registry.enabled(s, "options")),
            "risk_per_trade_pct": s.risk_per_trade_pct, "max_spread_pct": s.max_spread_pct,
            "exit_before_expiry_minutes": s.exit_before_expiry_minutes,
        },
        "now": [engine.auto.latest[u] for u in s.underlyings if u in engine.auto.latest],
    }


@router.get("/decisions")
def decisions(engine: EngineDep, underlying: str = "", action: str = "", limit: int = 50) -> list[dict[str, Any]]:
    return engine.auto.decisions(underlying, action, limit)


@router.post("/run")
async def run_now(engine: EngineDep) -> dict[str, Any]:
    """One cycle now — through the scheduler, so it never runs beside a scheduled one."""
    if not engine.auto.brokers():
        return {"decisions": [], "note": "auto-trading is off on every active broker: switch it on for one first"}
    await engine.scheduler.run_now("auto.cycle")
    return {"decisions": [engine.auto.latest[u] for u in engine.settings().underlyings if u in engine.auto.latest]}


@router.post("/toggle")
async def toggle(body: Toggle, engine: EngineDep) -> dict[str, Any]:
    if body.broker not in engine.brokers:
        raise NotFound(f"unknown broker {body.broker!r}")
    await engine.update_settings({f"auto_{body.broker}": body.enabled})
    return {"brokers": engine.broker_status()}
