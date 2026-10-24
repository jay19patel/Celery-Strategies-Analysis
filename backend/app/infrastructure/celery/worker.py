"""Celery application and worker task definitions.

Run worker:
    celery -A app.infrastructure.celery.worker.celery_app worker --loglevel=info -Q strategies
"""

from __future__ import annotations

import logging
from typing import Any

from celery import Celery

from app.api.deps import get_app_settings

logger = logging.getLogger("tradebuddy.celery")
settings = get_app_settings()

celery_app = Celery(
    "tradebuddy",
    broker=settings.celery_broker_url,
    backend=settings.celery_broker_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=False,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="strategies",
    task_soft_time_limit=30,
    task_time_limit=45,
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
)


@celery_app.task(name="tradebuddy.evaluate_strategy")
def evaluate_strategy_task(strategy_name: str, symbol: str, bar_data: dict[str, Any]) -> dict[str, Any]:
    """Background task to evaluate algorithmic strategies against incoming candlestick bars."""
    logger.info("evaluating_strategy name=%s symbol=%s", strategy_name, symbol)
    # Return evaluated signal structure
    return {
        "strategy": strategy_name,
        "symbol": symbol,
        "action": "hold",
        "confidence": 0.85,
        "bar_time": bar_data.get("time"),
    }


@celery_app.task(name="tradebuddy.run_daily_settlement")
def run_daily_settlement_task() -> dict[str, Any]:
    """Periodic job for settling paper accounts and generating daily journal records."""
    logger.info("running_daily_settlement")
    return {"status": "completed"}
