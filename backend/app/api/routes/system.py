"""Health, the engine's status, jobs and the event log."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import col, select

from app.api.deps import EngineDep
from app.core import security
from app.core.db import session
from app.core.models import EventLog
from app.errors import NotFound, Refused

router = APIRouter(tags=["system"])


@router.get("/health")
def health(engine: EngineDep) -> dict[str, Any]:
    return {"ok": True, "uptime_seconds": engine.status()["uptime_seconds"]}


@router.get("/system/status")
def status(engine: EngineDep) -> dict[str, Any]:
    return engine.status()


class ResetData(BaseModel):
    pin: str


@router.post("/system/reset-data")
async def reset_data(body: ResetData, engine: EngineDep) -> dict[str, Any]:
    """Delete every trade, order, decision, signal and log, keeping the configuration. The PIN is
    asked again: a session left open is not consent to wipe the history."""
    if not security.verify_pin(body.pin):
        raise Refused("wrong PIN: nothing was deleted")
    return {"deleted": await engine.reset_data()}


@router.get("/system/events")
def events(type: str = "", level: str = "", limit: int = 200) -> list[dict[str, Any]]:
    with session() as s:
        q = select(EventLog)
        if type:
            q = q.where(EventLog.type == type)
        if level == "warning":
            q = q.where(col(EventLog.level).in_(("warning", "error")))
        elif level:
            q = q.where(EventLog.level == level)
        return [e.model_dump() for e in s.exec(q.order_by(col(EventLog.ts).desc()).limit(min(max(limit, 1), 2000))).all()]


@router.post("/system/jobs/{job_id}/run")
async def run_job(job_id: str, engine: EngineDep) -> dict[str, Any]:
    if job_id not in engine.scheduler.jobs:
        raise NotFound(f"no job {job_id!r}")
    await engine.scheduler.run_now(job_id)
    return next(j for j in engine.scheduler.status() if j["id"] == job_id)
