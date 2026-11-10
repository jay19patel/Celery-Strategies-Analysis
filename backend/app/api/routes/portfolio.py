"""Accounts, results and a trading day."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import col, or_, select

from app.api.deps import EngineDep
from app.core.db import session
from app.core.models import Decision, EventLog, Structure
from app.errors import NotFound, Refused
from app.trading.risk import day_record, day_start, kill_state, trading_day

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class Resume(BaseModel):
    broker: str


@router.get("/accounts")
async def accounts(engine: EngineDep) -> dict[str, Any]:
    """Each active broker's account, its status, and its trading day: the equity it started at
    (what the daily loss limit measures from) and why it halted, if it did."""
    tz = engine.settings().day_timezone
    today = {}
    for name in engine.settings().active_brokers:
        row = day_record(name, tz)
        today[name] = {"day": trading_day(tz), "start_equity": row.start_equity if row else None, "halted": row.halted if row else ""}
    return {"accounts": await engine.accounts(), "brokers": engine.broker_status(), "today": today, "stopped": kill_state()}


@router.get("/stats")
def stats(engine: EngineDep, broker: str = "") -> dict[str, Any]:
    return engine.trading.stats(broker)


@router.post("/paper/reset")
async def paper_reset(engine: EngineDep) -> dict[str, Any]:
    engine.paper.reset()
    engine.hub.publish("PaperReset", "paper account reset")
    return (await engine.paper.account()).to_dict()


@router.post("/resume")
async def resume(body: Resume, engine: EngineDep) -> dict[str, Any]:
    """Lift today's loss halt on one broker; its day restarts from the current equity."""
    if body.broker not in engine.brokers:
        raise NotFound(f"unknown broker {body.broker!r}")
    account = await engine.brokers[body.broker].account()
    engine.resume_broker(body.broker, account.equity)
    return {"brokers": engine.broker_status()}


@router.get("/journal/day")
def journal_day(engine: EngineDep, date: str, broker: str = "") -> dict[str, Any]:
    """One trading day: its structures (on `broker`, or every broker), decisions and events."""
    tz = engine.settings().day_timezone
    try:
        start = day_start(tz, date)
    except ValueError as exc:
        raise Refused("date must be YYYY-MM-DD") from exc
    end = start + 86_400
    with session() as s:
        q = select(Structure).where(or_(
            (col(Structure.opened_at) >= start) & (col(Structure.opened_at) < end),
            (col(Structure.closed_at) >= start) & (col(Structure.closed_at) < end),
        ))
        touched = s.exec(q.where(Structure.broker == broker) if broker else q).all()
        decisions = s.exec(select(Decision).where(Decision.ts >= start, Decision.ts < end).order_by(col(Decision.ts).desc())).all()
        events = s.exec(select(EventLog).where(EventLog.ts >= start, EventLog.ts < end).order_by(col(EventLog.ts).desc()).limit(300)).all()
    structures = engine.trading.structures_by_id([t.id for t in touched])
    closed = [v for v in structures if v["closed_at"] and start <= v["closed_at"] < end and v["status"] == "closed"]
    return {
        "date": date, "timezone": tz,
        "pnl": round(sum(v["pnl"] or 0 for v in closed), 4),
        "structures": structures,
        "decisions": [d.model_dump() for d in decisions],
        "events": [e.model_dump() for e in events],
    }
