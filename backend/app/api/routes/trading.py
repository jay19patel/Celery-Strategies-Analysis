"""Manual trading: open a structure, close it, change its exits, and the kill switch."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import EngineDep
from app.options.strategies import CATALOG

router = APIRouter(prefix="/trading", tags=["trading"])


class OpenStructure(BaseModel):
    broker: str
    underlying: str = "BTC"
    strategy: str  # a catalog key, or "custom"
    legs: list[dict[str, Any]] = Field(min_length=1, max_length=6)  # [{"symbol", "side", "ratio"}] from the preview
    qty: int = Field(ge=1, le=10_000)
    request_id: str = Field(min_length=8, max_length=64)


class Exits(BaseModel):
    take_profit_pct: float
    stop_loss_pct: float


class CloseAll(BaseModel):
    broker: str = ""


@router.post("/open")
async def open_structure(body: OpenStructure, engine: EngineDep) -> dict[str, Any]:
    """Opens exactly the contracts that were previewed, repriced at the live book: a strategy is
    never re-picked between the preview and the click."""
    plan = engine.desk.plan(body.underlying, "custom", legs=body.legs)
    if body.strategy in CATALOG:
        plan = replace(plan, strategy=body.strategy)
    return await engine.trading.open(body.broker, plan, body.qty, "manual", body.request_id)


@router.get("/structures")
def structures(engine: EngineDep, status: str = "", broker: str = "", limit: int = 200) -> list[dict[str, Any]]:
    return engine.trading.structures(status, broker, limit)


@router.get("/structures/{sid}")
def structure(sid: str, engine: EngineDep) -> dict[str, Any]:
    return engine.trading.structure(sid)


@router.post("/structures/{sid}/close")
async def close(sid: str, engine: EngineDep) -> dict[str, Any]:
    return await engine.trading.close(sid, "closed from the dashboard")


@router.patch("/structures/{sid}")
def update_exits(sid: str, body: Exits, engine: EngineDep) -> dict[str, Any]:
    return engine.trading.update_exits(sid, body.take_profit_pct, body.stop_loss_pct)


@router.post("/close-all")
async def close_all(body: CloseAll, engine: EngineDep) -> dict[str, Any]:
    result = await engine.trading.close_all("kill switch from the dashboard", body.broker)
    engine.hub.publish("KillSwitch", f"close all{' on ' + body.broker if body.broker else ''}: {len(result['closed'])} closed, {len(result['errors'])} errors",
                       "warning", **result)
    return result


@router.get("/orders")
def orders(engine: EngineDep, broker: str = "", limit: int = 300) -> list[dict[str, Any]]:
    return engine.trading.orders(broker, limit)
