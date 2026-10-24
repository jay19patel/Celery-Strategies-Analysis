"""Paper Trading API router — account state, trade history, stats, and resets."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import get_paper_service, require_auth
from app.application.paper_service import PaperService

router = APIRouter(prefix="/paper", tags=["paper"])


class ResetAccountRequest(BaseModel):
    """Payload to reset paper trading account."""

    starting_balance: float = Field(default=10000.0, gt=0.0, le=10_000_000.0)


@router.get("/account")
def get_account(
    paper_service: Annotated[PaperService, Depends(get_paper_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> dict[str, Any]:
    """Retrieve current paper trading balance, realized PnL, and fees."""
    return paper_service.get_account()


@router.get("/trades")
def get_trades(
    paper_service: Annotated[PaperService, Depends(get_paper_service)],
    _: Annotated[bool, Depends(require_auth)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    symbol: Annotated[str | None, Query()] = None,
) -> list[dict[str, Any]]:
    """Retrieve closed trade execution history."""
    return paper_service.get_trades(limit=limit, symbol=symbol)


@router.get("/stats")
def get_stats(
    paper_service: Annotated[PaperService, Depends(get_paper_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> dict[str, Any]:
    """Retrieve cumulative performance metrics (win rate, profit factor, max drawdown)."""
    return paper_service.get_stats()


@router.post("/reset")
def reset_account(
    payload: ResetAccountRequest,
    paper_service: Annotated[PaperService, Depends(get_paper_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> dict[str, Any]:
    """Reset paper account balance to initial amount."""
    return paper_service.reset(starting_balance=payload.starting_balance)
