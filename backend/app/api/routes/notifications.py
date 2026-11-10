"""The dashboard's notifications: the stored events worth telling someone about, read or unread.

They are the event log itself (kept 30 days), filtered to what happened to a position or to risk —
so nothing is recorded twice, and a notification is there even for an event that fired while no
dashboard was open."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import col, func, select, update

from app.core.db import session
from app.core.models import EventLog

router = APIRouter(prefix="/notifications", tags=["notifications"])

TYPES = (
    "StructureOpened", "StructureClosed", "StructureFailed", "StructureError", "RiskHalt", "RiskResumed",
    "KillSwitch", "KillSwitchCleared", "ReconcileMismatch", "ReconcileMatched",
)
NOTIFIABLE = col(EventLog.type).in_(TYPES)
UNREAD = col(EventLog.read_at).is_(None)


def unread_count() -> int:
    with session() as s:
        return s.exec(select(func.count()).select_from(EventLog).where(NOTIFIABLE, UNREAD)).one()


@router.get("")
def notifications(page: int = 1, size: int = 20, unread: bool = False) -> dict[str, Any]:
    """One page, newest first, with how many match and how many are unread."""
    size = min(max(size, 1), 100)
    where = [NOTIFIABLE, UNREAD] if unread else [NOTIFIABLE]
    with session() as s:
        total = s.exec(select(func.count()).select_from(EventLog).where(*where)).one()
        pages = max(1, -(-total // size))
        page = min(max(page, 1), pages)
        rows = s.exec(select(EventLog).where(*where).order_by(col(EventLog.ts).desc()).offset((page - 1) * size).limit(size)).all()
        return {"rows": [r.model_dump() for r in rows], "total": total, "unread": unread_count(),
                "page": page, "pages": pages, "size": size}


class Read(BaseModel):
    ids: list[int] = []  # empty: every unread notification


@router.post("/read")
def mark_read(body: Read) -> dict[str, Any]:
    q = update(EventLog).where(NOTIFIABLE, UNREAD).values(read_at=time.time())
    if body.ids:
        q = q.where(col(EventLog.id).in_(body.ids))
    with session() as s:
        marked = s.exec(q).rowcount  # type: ignore[call-overload]
        s.commit()
    return {"marked": marked, "unread": unread_count()}
