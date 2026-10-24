"""Market API router — candlestick bars, snapshots, and model predictions."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_market_service, require_auth
from app.application.market_service import MarketService

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/bars")
def get_bars(
    market_service: Annotated[MarketService, Depends(get_market_service)],
    _: Annotated[bool, Depends(require_auth)],
    symbol: Annotated[str, Query(description="Ticker symbol, e.g. BTCUSD")] = "BTCUSD",
    interval: Annotated[str, Query(description="Bar interval, e.g. 1m, 5m, 15m, 1h")] = "15m",
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    since: Annotated[int | None, Query(description="Unix timestamp epoch")] = None,
) -> list[dict[str, Any]]:
    """Retrieve OHLCV candlestick data for charts."""
    return market_service.get_bars(symbol=symbol, interval=interval, limit=limit, since=since)


@router.get("/snapshots")
def get_snapshots(
    market_service: Annotated[MarketService, Depends(get_market_service)],
    _: Annotated[bool, Depends(require_auth)],
    symbol: Annotated[str, Query()] = "BTCUSD",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[dict[str, Any]]:
    """Retrieve analyst market metrics and indicators."""
    return market_service.get_snapshots(symbol=symbol, limit=limit)


@router.get("/predictions")
def get_predictions(
    market_service: Annotated[MarketService, Depends(get_market_service)],
    _: Annotated[bool, Depends(require_auth)],
    symbol: Annotated[str, Query()] = "BTCUSD",
    source: Annotated[str | None, Query()] = None,
    scored_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[dict[str, Any]]:
    """Retrieve AI model predictions and accuracy scoring."""
    return market_service.get_predictions(
        symbol=symbol,
        source=source,
        scored_only=scored_only,
        limit=limit,
    )
