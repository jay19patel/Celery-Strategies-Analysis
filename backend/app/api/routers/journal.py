"""Journal API router — daily trade reviews and monthly calendar heatmaps."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_journal_service, require_auth
from app.application.journal_service import JournalService

router = APIRouter(prefix="/journal", tags=["journal"])


@router.get("/day")
def get_daily_journal(
    journal_service: Annotated[JournalService, Depends(get_journal_service)],
    _: Annotated[bool, Depends(require_auth)],
    day: Annotated[str, Query(description="Date in YYYY-MM-DD format")],
) -> dict[str, Any]:
    """Retrieve detailed trade review and stats for a given day."""
    return journal_service.get_day_summary(day=day)


@router.get("/month")
def get_monthly_journal(
    journal_service: Annotated[JournalService, Depends(get_journal_service)],
    _: Annotated[bool, Depends(require_auth)],
    month: Annotated[str, Query(description="Month in YYYY-MM format")],
) -> dict[str, Any]:
    """Retrieve monthly trade summary aggregated day-by-day for calendar heatmaps."""
    return journal_service.get_month_summary(month=month)
