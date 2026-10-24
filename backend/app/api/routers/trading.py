"""Trading API router — order execution, position lifecycle, and controls."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import get_trading_service, require_auth
from app.application.trading_service import TradingService

router = APIRouter(prefix="/trading", tags=["trading"])


class OrderRequest(BaseModel):
    """Payload to place a trade order."""

    symbol: str = Field(min_length=1, max_length=32)
    side: str = Field(pattern="^(buy|sell)$")
    size: int = Field(gt=0)
    price: float = Field(gt=0.0)
    strategy: str = Field(default="manual", max_length=64)
    broker: str = Field(default="paper", max_length=16)
    stop_loss: float | None = Field(default=None, gt=0.0)
    take_profit: float | None = Field(default=None, gt=0.0)


class TrailingStopRequest(BaseModel):
    """Payload to configure trailing stop on a position."""

    trailing: bool = True
    max_steps: int = Field(default=5, ge=1, le=50)
    broker: str = Field(default="paper", max_length=16)


@router.post("/orders")
async def place_order(
    payload: OrderRequest,
    trading_service: Annotated[TradingService, Depends(get_trading_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> dict[str, Any]:
    """Place a new order."""
    return await trading_service.place_order(
        symbol=payload.symbol,
        side=payload.side,
        size=payload.size,
        price=payload.price,
        strategy=payload.strategy,
        broker=payload.broker,
        stop_loss=payload.stop_loss,
        take_profit=payload.take_profit,
    )


@router.get("/orders")
async def list_orders(
    trading_service: Annotated[TradingService, Depends(get_trading_service)],
    _: Annotated[bool, Depends(require_auth)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    broker: Annotated[str | None, Query()] = None,
) -> list[dict[str, Any]]:
    """Fetch past and current orders."""
    return trading_service.get_orders(limit=limit, broker=broker)


@router.get("/orders/open")
async def list_open_orders(
    trading_service: Annotated[TradingService, Depends(get_trading_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> list[dict[str, Any]]:
    """Fetch all open (non-terminal) orders."""
    return trading_service.get_open_orders()


@router.get("/positions")
async def list_positions(
    trading_service: Annotated[TradingService, Depends(get_trading_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> list[dict[str, Any]]:
    """Fetch all currently open positions."""
    return trading_service.get_positions()


@router.post("/positions/{symbol}/close")
async def close_position(
    symbol: str,
    trading_service: Annotated[TradingService, Depends(get_trading_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> dict[str, Any]:
    """Close an open position by symbol."""
    return await trading_service.close_position(symbol=symbol, reason="manual_close")


@router.post("/positions/close-all")
async def close_all_positions(
    trading_service: Annotated[TradingService, Depends(get_trading_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> list[dict[str, Any]]:
    """Emergency button — close all open positions across all symbols."""
    return await trading_service.close_all_positions()


@router.post("/positions/{symbol}/trailing-stop")
async def set_trailing_stop(
    symbol: str,
    payload: TrailingStopRequest,
    trading_service: Annotated[TradingService, Depends(get_trading_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> dict[str, Any]:
    """Configure trailing stop parameter on an active position."""
    return trading_service.set_position_control(
        broker=payload.broker,
        symbol=symbol,
        trailing=payload.trailing,
        max_steps=payload.max_steps,
    )
