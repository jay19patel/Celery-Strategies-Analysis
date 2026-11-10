"""Accounts, results and the journal."""

from __future__ import annotations

import calendar
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import col, func, or_, select

from app.api.deps import EngineDep
from app.core.db import session
from app.core.models import DayRisk, Decision, EventLog, SignalLog, Structure
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


@router.get("/equity")
def equity(engine: EngineDep, broker: str = "", days: int = 365) -> list[dict[str, Any]]:
    """Each broker's equity at the start of each trading day, oldest first: the series the daily
    loss limit already records, so no new snapshot is taken for it."""
    tz = ZoneInfo(engine.settings().day_timezone)  # the days are the trading days, not the server's
    first = (datetime.now(tz) - timedelta(days=min(max(days, 1), 3650))).strftime("%Y-%m-%d")
    q = select(DayRisk).where(DayRisk.day >= first)
    if broker:
        q = q.where(DayRisk.broker == broker)
    with session() as s:
        rows = s.exec(q.order_by(DayRisk.day, DayRisk.broker)).all()
    return [{"broker": r.broker, "day": r.day, "equity": r.start_equity} for r in rows]


@router.get("/funnel")
def funnel(since: float, until: float = 0, broker: str = "") -> dict[str, Any]:
    """From what the rules said to what paid, in [since, until): signals (directional signals and
    option cycles), those that passed every gate, structures sent to a broker, those filled
    (an unresolved one in error is not),
    and those closed in profit. The first two are before any broker, so `broker` filters the rest."""
    until = until or time.time()
    with session() as s:
        def count(q: Any) -> int:
            return s.exec(q).one()

        n = select(func.count())
        sig = n.select_from(SignalLog).where(SignalLog.ts >= since, SignalLog.ts < until, SignalLog.action != "error")
        dec = n.select_from(Decision).where(Decision.ts >= since, Decision.ts < until, Decision.action != "error")
        st = n.select_from(Structure).where(Structure.opened_at >= since, Structure.opened_at < until)
        if broker:
            st = st.where(Structure.broker == broker)
        return {
            "since": since, "until": until,
            "signals": count(sig) + count(dec),
            "passed": count(sig.where(SignalLog.action == "open")) + count(dec.where(Decision.action == "open")),
            "orders": count(st),
            "filled": count(st.where(col(Structure.status).in_(("open", "closing", "closed")))),
            "profitable": count(st.where(Structure.status == "closed", col(Structure.pnl) > 0)),
        }


@router.get("/journal")
def journal_month(engine: EngineDep, year: int = 0, month: int = 0, broker: str = "") -> dict[str, Any]:
    tz = engine.settings().day_timezone
    now = datetime.now(ZoneInfo(tz))
    year, month = year or now.year, month or now.month
    if not 1 <= month <= 12:
        raise Refused("month must be 1-12")
    days = calendar.monthrange(year, month)[1]
    stats = engine.trading.stats(broker)
    prefix = f"{year:04d}-{month:02d}-"
    start = day_start(tz, f"{prefix}01")
    end = day_start(tz, f"{prefix}{days:02d}") + 86_400
    with session() as s:
        decisions = s.exec(select(Decision).where(Decision.ts >= start, Decision.ts < end)).all()
        q = select(Structure).where(Structure.status == "closed", col(Structure.closed_at) >= start, col(Structure.closed_at) < end)
        closed = s.exec(q.where(Structure.broker == broker) if broker else q).all()
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
    structures = engine.trading.structures_by_id([t.id for t in touched])
    closed = [v for v in structures if v["closed_at"] and start <= v["closed_at"] < end and v["status"] == "closed"]
    return {
        "date": date, "timezone": tz,
        "pnl": round(sum(v["pnl"] or 0 for v in closed), 4),
        "structures": structures,
        "decisions": [d.model_dump() for d in decisions],
        "events": [e.model_dump() for e in events],
    }
