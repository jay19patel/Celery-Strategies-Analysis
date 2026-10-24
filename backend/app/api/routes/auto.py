"""The auto-trader: its state, its decisions, and running a cycle now."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import EngineDep
from app.errors import NotFound

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
        "config": {k: getattr(s, k) for k in (
            "auto_interval_minutes", "auto_strategies", "underlyings", "expiry_min_hours", "expiry_max_hours", "short_delta",
            "wing_width_pct", "min_score", "min_pop_pct", "min_credit_width_pct", "max_spread_pct", "risk_per_trade_pct",
            "max_open_structures", "max_per_underlying", "credit_take_profit_pct", "credit_stop_loss_pct",
            "debit_take_profit_pct", "debit_stop_loss_pct", "exit_before_expiry_minutes", "ai_enabled", "ai_review_trades", "ai_required",
        )},
        "ai_ready": engine.analyst.ready(),
        "latest": engine.auto.decisions(limit=10),
    }


@router.get("/decisions")
def decisions(engine: EngineDep, underlying: str = "", action: str = "", limit: int = 50) -> list[dict[str, Any]]:
    return engine.auto.decisions(underlying, action, limit)


@router.post("/run")
async def run_now(engine: EngineDep) -> dict[str, Any]:
    brokers = engine.auto.brokers()
    if not brokers:
        return {"decisions": [], "note": "auto-trading is off on every active broker: switch it on for one first"}
    return {"decisions": [await engine.auto.decide(u, brokers) for u in engine.settings().underlyings]}


@router.post("/toggle")
async def toggle(body: Toggle, engine: EngineDep) -> dict[str, Any]:
    if body.broker not in engine.brokers:
        raise NotFound(f"unknown broker {body.broker!r}")
    await engine.update_settings({f"auto_{body.broker}": body.enabled})
    return {"brokers": engine.broker_status()}
