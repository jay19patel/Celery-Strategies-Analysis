"""Accounts, results and the journal."""

from __future__ import annotations

import calendar
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import col, or_, select

from app.api.deps import EngineDep
from app.core.db import session
from app.core.models import Decision, EventLog, Structure
from app.errors import NotFound, Refused
from app.trading.risk import day_record, day_start, trading_day

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
    return {"accounts": await engine.accounts(), "brokers": engine.broker_status(), "today": today}


@router.post("/report")
async def send_report(engine: EngineDep) -> dict[str, Any]:
    """Today's report, now, to every enabled notification channel."""
    if not engine.notifier.enabled(engine.settings()):
        raise Refused("no notification channel is enabled and complete (Settings → Notifications)")
    return {"results": await engine.daily_report()}


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


@router.get("/journal")
def journal_month(engine: EngineDep, year: int = 0, month: int = 0) -> dict[str, Any]:
    tz = engine.settings().day_timezone
    now = datetime.now()  # noqa: DTZ005 - only the default year and month
    year, month = year or now.year, month or now.month
    if not 1 <= month <= 12:
        raise Refused("month must be 1-12")
    days = calendar.monthrange(year, month)[1]
    stats = engine.trading.stats()
    prefix = f"{year:04d}-{month:02d}-"
    start = day_start(tz, f"{prefix}01")
    end = day_start(tz, f"{prefix}{days:02d}") + 86_400
    with session() as s:
        decisions = s.exec(select(Decision).where(Decision.ts >= start, Decision.ts < end)).all()
        closed = s.exec(select(Structure).where(Structure.status == "closed", col(Structure.closed_at) >= start, col(Structure.closed_at) < end)).all()
    out: dict[str, dict[str, Any]] = {}
    for d in range(1, days + 1):
        out[f"{prefix}{d:02d}"] = {"pnl": stats["daily"].get(f"{prefix}{d:02d}", 0.0), "trades": 0, "wins": 0, "decisions": 0, "opened": 0}
    for t in closed:
        row = out.get(trading_day(tz, t.closed_at))
        if row:
            row["trades"] += 1
            row["wins"] += (t.pnl or 0) > 0
    for dec in decisions:
        row = out.get(trading_day(tz, dec.ts))
        if row:
            row["decisions"] += 1
            row["opened"] += dec.action == "open"
    month_pnl = sum(r["pnl"] for r in out.values())
    return {"year": year, "month": month, "timezone": tz, "days": out, "pnl": round(month_pnl, 4),
            "trades": sum(r["trades"] for r in out.values()), "wins": sum(r["wins"] for r in out.values())}


@router.get("/journal/day")
def journal_day(engine: EngineDep, date: str) -> dict[str, Any]:
    tz = engine.settings().day_timezone
    try:
        start = day_start(tz, date)
    except ValueError as exc:
        raise Refused("date must be YYYY-MM-DD") from exc
    end = start + 86_400
    with session() as s:
        touched = s.exec(select(Structure).where(or_(
            (col(Structure.opened_at) >= start) & (col(Structure.opened_at) < end),
            (col(Structure.closed_at) >= start) & (col(Structure.closed_at) < end),
        ))).all()
        decisions = s.exec(select(Decision).where(Decision.ts >= start, Decision.ts < end).order_by(col(Decision.ts).desc())).all()
        events = s.exec(select(EventLog).where(EventLog.ts >= start, EventLog.ts < end).order_by(col(EventLog.ts).desc()).limit(300)).all()
    ids = {t.id for t in touched}
    structures = [v for v in engine.trading.structures(limit=1000) if v["id"] in ids]
    closed = [v for v in structures if v["closed_at"] and start <= v["closed_at"] < end and v["status"] == "closed"]
    return {
        "date": date, "timezone": tz,
        "pnl": round(sum(v["pnl"] or 0 for v in closed), 4),
        "structures": structures,
        "decisions": [d.model_dump() for d in decisions],
        "events": [e.model_dump() for e in events],
    }
