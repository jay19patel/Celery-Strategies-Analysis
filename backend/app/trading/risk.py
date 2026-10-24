"""The trading day and each broker's daily loss halt."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.db import session
from app.core.models import DayRisk


def trading_day(tz: str, now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, ZoneInfo(tz)).strftime("%Y-%m-%d")


def day_start(tz: str, day: str) -> float:
    return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=ZoneInfo(tz)).timestamp()


def day_record(broker: str, tz: str) -> DayRisk | None:
    with session() as s:
        return s.get(DayRisk, (broker, trading_day(tz)))


def halt_reason(broker: str, tz: str) -> str:
    row = day_record(broker, tz)
    return row.halted if row else ""


def start_day(broker: str, tz: str, equity: float) -> DayRisk:
    with session() as s:
        row = s.get(DayRisk, (broker, trading_day(tz)))
        if row is None:
            row = DayRisk(broker=broker, day=trading_day(tz), start_equity=equity)
            s.add(row)
            s.commit()
        return row


def set_halt(broker: str, tz: str, reason: str, equity: float | None = None) -> None:
    """Halt (reason) or resume (""): a resume restarts the day from `equity`."""
    with session() as s:
        row = s.get(DayRisk, (broker, trading_day(tz)))
        if row is None:
            return
        row.halted = reason
        if equity is not None:
            row.start_equity = equity
        s.add(row)
        s.commit()
