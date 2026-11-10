"""The market and the strategy builder: chain, regime, bars, and a structure previewed before it is traded."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import EngineDep
from app.errors import BrokerError
from app.market.options import PERPETUALS
from app.options.assess import unit_risk
from app.trading.autotrader import lots_for_risk

router = APIRouter(tags=["market"])


@router.get("/market/overview")
def overview(engine: EngineDep, underlying: str = "BTC") -> dict[str, Any]:
    return engine.desk.overview(underlying)


@router.get("/market/chain")
def option_chain(engine: EngineDep, underlying: str = "BTC", expiry: float | None = None) -> dict[str, Any]:
    return engine.desk.chain(underlying, expiry or engine.desk.default_expiry(underlying))


@router.get("/market/ideas")
async def option_ideas(engine: EngineDep, underlying: str = "BTC", expiry: float | None = None) -> dict[str, Any]:
    return await engine.desk.ideas(underlying, expiry or engine.desk.default_expiry(underlying))


@router.get("/market/regime")
def regime(engine: EngineDep, underlying: str = "BTC", expiry: float | None = None) -> dict[str, Any]:
    return engine.desk.regime(underlying, expiry)


@router.get("/market/bars")
def bars(engine: EngineDep, underlying: str = "BTC", limit: int = 672) -> dict[str, Any]:
    symbol = PERPETUALS.get(underlying, "BTCUSD")
    return {"symbol": symbol, "interval": "15m", "bars": engine.feed.bars(symbol, min(max(limit, 10), 3000))}


class Preview(BaseModel):
    underlying: str = "BTC"
    strategy: str
    expiry: float | None = None
    legs: list[dict[str, Any]] | None = None  # for "custom": [{"symbol", "side", "ratio"}]
    qty: int = Field(1, ge=1, le=10_000)
    params: dict[str, Any] | None = None  # the strategy's own params for this ticket, over its configured ones


@router.post("/strategies/preview")
async def preview(body: Preview, engine: EngineDep) -> dict[str, Any]:
    """The structure, priced and checked at `qty`, and what each active broker would size it at."""
    desk, s = engine.desk, engine.settings()
    plan = desk.plan(body.underlying, body.strategy, body.expiry, body.legs, body.params)
    out = await desk.assess(plan, body.qty)
    unit = await desk.assess(plan, 1)
    sizing = []
    for name in s.active_brokers:
        row: dict[str, Any] = {"broker": name, "unit_risk": round(unit_risk(unit), 4)}
        try:
            account = await engine.brokers[name].account()
            worst = out["analysis"]["max_loss"]
            row |= {
                "equity": account.equity, "available": account.available,
                "suggested_qty": lots_for_risk(account.equity, unit, s),
                "worst_case_pct": round(100 * worst / account.equity, 2) if worst is not None and account.equity > 0 else None,
                "margin_ok": out["analysis"]["margin"] + out["analysis"]["entry_fees"] <= account.available,
            }
        except BrokerError as exc:
            row["error"] = str(exc)
        sizing.append(row)
    return out | {"sizing": sizing}
