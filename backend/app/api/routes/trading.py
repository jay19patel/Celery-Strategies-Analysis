"""Manual trading: open a structure, close it, change its exits, and the kill switch (Stop all)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import EngineDep
from app.strategies import registry

router = APIRouter(prefix="/trading", tags=["trading"])


class OpenStructure(BaseModel):
    broker: str
    underlying: str = "BTC"
    strategy: str  # an option strategy's name, or "custom"
    legs: list[dict[str, Any]] = Field(min_length=1, max_length=6)  # [{"symbol", "side", "ratio"}] from the preview
    qty: int = Field(ge=1, le=10_000)
    request_id: str = Field(min_length=8, max_length=64)


class Exits(BaseModel):
    take_profit_pct: float
    stop_loss_pct: float


@router.post("/open")
async def open_structure(body: OpenStructure, engine: EngineDep) -> dict[str, Any]:
    """Opens exactly the contracts that were previewed, repriced at the live book: a strategy is
    never re-picked between the preview and the click."""
    plan = engine.desk.plan(body.underlying, "custom", legs=body.legs)
    if registry.option(body.strategy):
        plan = replace(plan, strategy=body.strategy)
    return await engine.trading.open(body.broker, plan, body.qty, "manual", body.request_id)


@router.get("/structures")
def structures(engine: EngineDep, status: str = "", broker: str = "", limit: int = 200) -> list[dict[str, Any]]:
    return engine.trading.structures(status, broker, limit)


@router.get("/structures/{sid}")
def structure(sid: str, engine: EngineDep) -> dict[str, Any]:
    """One structure with its legs and every order sent for it."""
    return engine.trading.structure(sid)


@router.post("/structures/{sid}/close")
async def close(sid: str, engine: EngineDep) -> dict[str, Any]:
    return await engine.trading.close(sid, "closed from the dashboard")


@router.patch("/structures/{sid}")
async def update_exits(sid: str, body: Exits, engine: EngineDep) -> dict[str, Any]:
    return await engine.trading.update_exits(sid, body.take_profit_pct, body.stop_loss_pct)


@router.post("/stop-all")
async def stop_all(engine: EngineDep) -> dict[str, Any]:
    """Every entry on every broker refused until resumed, then every position closed."""
    return await engine.stop_all()


@router.post("/resume")
def resume_all(engine: EngineDep) -> dict[str, Any]:
    return engine.resume_all()


@router.get("/orders")
def orders(engine: EngineDep, broker: str = "", limit: int = 300, since: float = 0.0, until: float = 0.0) -> list[dict[str, Any]]:
    return engine.trading.orders(broker, limit, since, until)


@router.get("/orders/page")
def order_page(engine: EngineDep, broker: str = "", status: str = "", page: int = 1, size: int = 20) -> dict[str, Any]:
    return engine.trading.order_page(broker, status, page, size)


@router.post("/test-all")
async def test_all(engine: EngineDep) -> list[dict[str, Any]]:
    """Paper only: opens one of everything that may be held at once, to see them side by side."""
    return await engine.trading.open_test_set()
