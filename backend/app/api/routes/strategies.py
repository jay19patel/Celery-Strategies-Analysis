"""Every strategy: what it is, how it is configured, its settings form, what it is doing now, its
measured record — and manual orders on the perpetual."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlmodel import select

from app.api.deps import EngineDep
from app.core.db import session
from app.core.models import Structure
from app.errors import BrokerError, NotFound
from app.market.options import PERPETUALS
from app.strategies import registry
from app.trading import edge
from app.trading.autotrader import MIN_SCORE

router = APIRouter(tags=["strategies"])


class PerpOrder(BaseModel):
    broker: str
    underlying: str = "BTC"
    side: str
    qty: int = Field(ge=1, le=10_000)
    stop: float = Field(gt=0)  # a manual position is never opened without a stop
    target: float = Field(gt=0)
    request_id: str = Field(min_length=8, max_length=64)


class StrategyChange(BaseModel):
    """Any of a strategy's switches; params are merged into what it has."""

    enabled: bool | None = None
    live: bool | None = None
    underlyings: list[str] | None = None
    params: dict[str, Any] | None = None


def _records() -> dict[str, dict[str, Any]]:
    """Each strategy's automatic closed trades read in R (`trading.edge`)."""
    with session() as s:
        rows = s.exec(select(Structure.strategy, Structure.pnl, Structure.max_loss, Structure.margin).where(
            Structure.source != "manual", Structure.status == "closed")).all()
    by: dict[str, list[float]] = {}
    for name, pnl, loss, margin in rows:
        if (r := edge.r_multiple(pnl, loss or margin)) is not None:
            by.setdefault(name, []).append(r)
    return {name: edge.measure(rs) for name, rs in by.items()}


@router.get("/strategies")
def strategies(engine: EngineDep) -> dict[str, Any]:
    s = engine.settings()
    records = _records()
    out = []
    for row in registry.describe(s):
        name = row["name"]
        out.append(row | {
            "underlyings_on": [u for u in s.underlyings if s.strategy_on(name, u)],
            "brokers": s.strategy_brokers(name),
            "record": records.get(name) or edge.measure([]),
            "latest": engine.runner.latest_for(name) if row["family"] == "perp" else [
                d for u, d in engine.auto.latest.items() if u in s.underlyings
            ],
        })
    jobs = {j["id"]: j for j in engine.scheduler.status()}
    return {
        "strategies": out, "underlyings": s.underlyings, "brokers": engine.broker_status(),
        "jobs": {"perp": jobs.get("strategies.run"), "options": jobs.get("auto.cycle")},
        "min_score": MIN_SCORE,
    }


@router.patch("/strategies/{name}")
async def change_strategy(name: str, body: StrategyChange, engine: EngineDep) -> dict[str, Any]:
    if registry.get(name) is None:
        raise NotFound(f"no strategy {name!r}")
    s = engine.settings()
    cfg = s.config(name).model_dump()
    change = body.model_dump(exclude_none=True)
    if "params" in change:
        change["params"] = cfg["params"] | change["params"]
    strategies = {k: v.model_dump() for k, v in s.strategies.items()} | {name: cfg | change}
    await engine.update_settings({"strategies": strategies})
    return strategies_view(engine, name)


def strategies_view(engine: Any, name: str) -> dict[str, Any]:
    return next(row for row in registry.describe(engine.settings()) if row["name"] == name)


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
            b = engine.brokers[name]
            account = await b.account()
            leverage = await b.leverage(symbol)
            row |= {"equity": account.equity, "available": account.available, "leverage": leverage,
                    "suggested_qty": engine.trading.perp_size(account.equity, account.available, price, stop, cv, rate, leverage)
                    if price and stop else None}
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
