"""Analytics, equity curve, and closed trade audit router.

Routes incoming HTTP requests to app.services.AnalyticsService.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services.analytics_service import AnalyticsService, get_analytics_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Analytics"])


@router.get("/api/portfolio/equity-curve")
def get_portfolio_equity_curve() -> list[dict[str, Any]]:
    """Retrieve cumulative portfolio equity progression over time."""
    try:
        service: AnalyticsService = get_analytics_service()
        return service.get_portfolio_equity_curve()
    except Exception as exc:
        logger.exception("Failed to compute equity curve")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/trades")
def get_recent_trades(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
    """Retrieve completed trade history ordered by newest first."""
    try:
        service: AnalyticsService = get_analytics_service()
        return service.get_recent_trades(limit=limit)
    except Exception as exc:
        logger.exception("Failed to fetch trade history")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/analytics")
def get_strategy_analytics() -> list[dict[str, Any]]:
    """Retrieve comprehensive risk/reward analytics grouped by strategy and symbol."""
    try:
        service: AnalyticsService = get_analytics_service()
        return service.get_strategy_analytics()
    except Exception as exc:
        logger.exception("Failed to compile strategy analytics")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/analytics/paper-dashboard")
def get_paper_dashboard() -> dict[str, Any]:
    """Retrieve mark-to-market paper account statistics, chart data, and trade history."""
    try:
        return get_analytics_service().get_paper_dashboard()
    except Exception as exc:
        logger.exception("Failed to compile paper dashboard")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
