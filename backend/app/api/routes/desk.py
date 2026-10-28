"""The Market Desk: every reading of the market the old Market page had, from the engine's caches."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import EngineDep

router = APIRouter(prefix="/desk", tags=["market desk"])


@router.get("/analysis")
def analysis(engine: EngineDep, underlying: str = "BTC") -> dict[str, Any]:
    return engine.market.analysis(underlying)


@router.post("/analysis")
async def analyse_now(engine: EngineDep) -> dict[str, Any]:
    note = await engine.market.analyse()
    return {"note": note}


@router.get("/outlook")
async def outlook(engine: EngineDep, underlying: str = "BTC") -> dict[str, Any]:
    return await engine.market.outlook(underlying)


@router.get("/pulse")
async def pulse(engine: EngineDep, underlying: str = "BTC") -> dict[str, Any]:
    return await engine.market.pulse(underlying)


@router.get("/structure")
async def structure(engine: EngineDep, underlying: str = "BTC") -> dict[str, Any]:
    return await engine.market.structure(underlying)


@router.get("/trendlines")
async def trendlines(engine: EngineDep, underlying: str = "BTC", interval: str = "15m", limit: int = 500) -> dict[str, Any]:
    return await engine.market.trendlines(underlying, interval, limit)


@router.get("/fibonacci")
async def fib(engine: EngineDep, underlying: str = "BTC", interval: str = "15m", limit: int = 500) -> dict[str, Any]:
    return await engine.market.fibonacci(underlying, interval, limit)


@router.get("/verdicts")
async def verdicts(engine: EngineDep, underlying: str = "BTC", days: float = 30) -> dict[str, Any]:
    return await engine.market.verdicts(underlying, days)


@router.get("/bars")
async def bars(engine: EngineDep, underlying: str = "BTC", interval: str = "15m", days: float = 7, limit: int = 1500) -> dict[str, Any]:
    return await engine.market.bars(underlying, interval, days, limit)


@router.get("/options-history")
def options_history(engine: EngineDep, underlying: str = "BTC", hours: float = 24) -> list[dict[str, Any]]:
    return engine.market.options_history(underlying, hours, 60 if hours <= 24 else 900)
