"""Strategy accounts, global portfolio metrics, and trading calendar router.

Routes incoming HTTP requests to app.services.StrategyService.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.services.strategy_service import get_strategy_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Strategies"])


@router.get("/api/stats")
def get_global_stats() -> dict[str, Any]:
    """Retrieve aggregate performance metrics across all strategy accounts."""
    try:
        service = get_strategy_service()
        return service.get_global_stats()
    except Exception as exc:
        logger.exception("Failed to compile global stats")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/strategies")
def get_strategies_stats() -> list[dict[str, Any]]:
    """Retrieve performance stats and active open positions for each strategy."""
    try:
        service = get_strategy_service()
        return service.get_strategies_stats()
    except Exception as exc:
        logger.exception("Failed to fetch strategy stats")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/strategy/signals")
def get_strategy_signals(
    limit: int = 50,
    offset: int = 0,
    page: int | None = None,
    page_size: int | None = None,
    paginated: bool = False,
    response: Response = None,
) -> Any:
    """Retrieve algorithmic signals with pagination and execution routing status."""
    try:
        service = get_strategy_service()
        total = service.get_signals_count()

        if page is not None and page > 0:
            effective_page_size = page_size if (page_size and page_size > 0) else limit
            effective_offset = (page - 1) * effective_page_size
            effective_limit = effective_page_size
            current_page = page
        else:
            effective_limit = limit
            effective_offset = offset
            effective_page_size = limit
            current_page = (offset // effective_limit) + 1 if effective_limit > 0 else 1

        signals = service.get_signals_log(limit=effective_limit, offset=effective_offset)
        total_pages = max(1, (total + effective_page_size - 1) // effective_page_size) if effective_page_size > 0 else 1

        if response is not None:
            response.headers["X-Total-Count"] = str(total)
            response.headers["X-Page"] = str(current_page)
            response.headers["X-Page-Size"] = str(effective_limit)
            response.headers["X-Total-Pages"] = str(total_pages)

        if paginated or page is not None:
            return {
                "status": "success",
                "signals": signals,
                "total": total,
                "page": current_page,
                "page_size": effective_limit,
                "total_pages": total_pages,
            }

        return signals
    except Exception as exc:
        logger.exception("Failed to fetch strategy signals")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

class TriggerSignalPayload(BaseModel):
    strategy_name: str | None = None
    strategy: str | None = None
    symbol: str = Field(default="BTC-USD")
    signal_type: str | None = None
    action: str | None = None
    price: float = Field(default=65000.0)
    confidence: float = Field(default=1.0)


@router.post("/api/strategy/trigger")
@router.post("/api/strategy/manual-signal")
def trigger_manual_signal(payload: TriggerSignalPayload) -> dict[str, Any]:
    """Manually generate a test strategy signal to test Paper or Live execution."""
    try:
        service = get_strategy_service()
        chosen_strategy = payload.strategy_name or payload.strategy or "ManualOverrideStrategy"
        chosen_signal = (payload.signal_type or payload.action or "BUY").upper()
        return service.trigger_signal(
            strategy_name=chosen_strategy,
            symbol=payload.symbol,
            signal_type=chosen_signal,
            price=payload.price,
            confidence=payload.confidence,
        )
    except Exception as exc:
        logger.exception("Failed to trigger signal")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class ToggleStrategyPayload(BaseModel):
    strategy_id: str
    is_paper_enabled: bool | None = None
    is_real_enabled: bool | None = None


@router.get("/api/strategy/list")
def get_strategy_list() -> list[dict[str, Any]]:
    """Retrieve detailed list of strategies, timeframes, symbols, and execution toggles."""
    try:
        service = get_strategy_service()
        return service.get_strategies_detailed()
    except Exception as exc:
        logger.exception("Failed to fetch detailed strategy list")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/strategy/detailed")
def get_strategy_detailed() -> dict[str, Any]:
    """Return per-symbol strategy cards consumed by the Strategies Matrix UI.

    Expands each strategy config (which may cover multiple symbols) into
    individual cards — one per (strategy × symbol) combination — so the
    frontend grid can show a dedicated card for every trading pair.
    """
    try:
        service = get_strategy_service()
        configs = service.get_strategies_detailed()

        cards: list[dict[str, Any]] = []
        for cfg in configs:
            symbols = cfg.get("symbols") or ["BTC-USD"]
            # symbols may already be a list (service returns list[str])
            if isinstance(symbols, str):
                symbols = [s.strip() for s in symbols.split(",") if s.strip()]

            for sym in symbols:
                cards.append({
                    "id": f"{cfg['strategy_id']}_{sym.replace('-', '_')}",
                    "strategy_id": cfg["strategy_id"],
                    "name": cfg["name"],
                    "symbol": sym,
                    "interval": cfg.get("timeframe", "1m"),
                    "category": "MOMENTUM",
                    "paper_enabled": cfg.get("is_paper_enabled", True),
                    "real_enabled": cfg.get("is_real_enabled", False),
                    "total_trades": cfg.get("paper_orders_count", 0),
                    "win_rate": cfg.get("win_rate", 0.0),
                    "pnl": round(cfg.get("capital", 100.0) - 100.0, 2),
                    "total_signals": cfg.get("total_signals", 0),
                    "return_pct": cfg.get("return_pct", 0.0),
                    "open_position": cfg.get("open_position"),
                    "updated_at": cfg.get("updated_at", ""),
                })

        # Unique symbol set across all strategies
        all_symbols = list({c["symbol"] for c in cards})
        unique_strategies = list({c["strategy_id"] for c in cards})

        return {
            "status": "success",
            "strategies": cards,
            "total_cards": len(cards),
            "total_symbols": len(all_symbols),
            "total_strategies": len(unique_strategies),
        }
    except Exception as exc:
        logger.exception("Failed to build detailed strategy cards")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/strategy/toggle")
def toggle_strategy(payload: ToggleStrategyPayload) -> dict[str, Any]:
    """Toggle paper or real execution states for a specific strategy."""
    try:
        service = get_strategy_service()
        return service.toggle_strategy_execution(
            strategy_id=payload.strategy_id,
            is_paper_enabled=payload.is_paper_enabled,
            is_real_enabled=payload.is_real_enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to toggle strategy execution")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/calendar")
def get_trading_calendar() -> dict[str, Any]:
    """Retrieve exchange calendar and current market status."""
    service = get_strategy_service()
    return service.get_calendar()
