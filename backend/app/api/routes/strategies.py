"""Directional strategies on the perpetual: what each is doing now, its signals, and manual orders."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import EngineDep
from app.errors import BrokerError, NotFound
from app.market.options import PERPETUALS

router = APIRouter(tags=["strategies"])


class PerpOrder(BaseModel):
    broker: str
    underlying: str = "BTC"
    side: str
    qty: int = Field(ge=1, le=10_000)
    stop: float = Field(gt=0)  # a manual position is never opened without a stop
    target: float = Field(gt=0)
    request_id: str = Field(min_length=8, max_length=64)


@router.get("/strategies/directional")
def directional(engine: EngineDep) -> dict[str, Any]:
    s = engine.settings()
    job = next((j for j in engine.scheduler.status() if j["id"] == "strategies.run"), None)
    return {"strategies": engine.runner.status(), "job": job, "auto_options": s.auto_options, "brokers": engine.broker_status()}


@router.get("/strategies/signals")
def signals(engine: EngineDep, strategy: str = "", action: str = "", limit: int = 100) -> list[dict[str, Any]]:
    return engine.runner.signals(strategy, action, limit)


@router.get("/trading/perp/ticket")
async def perp_ticket(engine: EngineDep, underlying: str = "BTC", side: str = "buy", stop: float = 0.0) -> dict[str, Any]:
    """The live price and, for a stop, the contracts each active broker's risk budget allows."""
    if underlying not in PERPETUALS:
        raise NotFound(f"unknown underlying {underlying!r}")
    symbol = PERPETUALS[underlying]
    quote = engine.trading.book(symbol)
    price = ((quote[1] if side == "buy" else quote[0]) or quote[2]) if quote else None
    cv = await engine.desk.contract_value(symbol)
    rate = (await engine.desk.fee_rate(symbol)).taker
    sizing = []
    for name in engine.settings().active_brokers:
        row: dict[str, Any] = {"broker": name}
        try:
            account = await engine.brokers[name].account()
            row |= {"equity": account.equity, "available": account.available,
                    "suggested_qty": engine.trading.perp_size(account.equity, account.available, price, stop, cv, rate) if price and stop else None}
        except BrokerError as exc:
            row["error"] = str(exc)
        sizing.append(row)
    return {"symbol": symbol, "price": price, "contract_value": cv, "fee_rate": rate, "sizing": sizing}


@router.post("/trading/perp")
async def open_perp(body: PerpOrder, engine: EngineDep) -> dict[str, Any]:
    return await engine.trading.open_directional(
        body.broker, body.underlying, body.side, body.qty, body.stop, body.target, "manual", "manual", body.request_id,
        "opened from the dashboard",
    )
