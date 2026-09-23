"""System metrics, health, configuration, and maintenance router.

Routes incoming HTTP requests to app.services.SystemService.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from app.core.prometheus_metrics import PROMETHEUS_REGISTRY
from app.services.system_service import get_system_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["System"])


@router.get("/metrics", include_in_schema=False)
def get_prometheus_metrics() -> Response:
    """Expose the cached health snapshot in Prometheus text format."""
    return Response(generate_latest(PROMETHEUS_REGISTRY), media_type=CONTENT_TYPE_LATEST)


class ResetRequest(BaseModel):
    """Payload for system reset confirmation."""

    confirmation: str = Field(..., description="Confirmation phrase 'RESET SYSTEM'")


@router.get("/api/system/metrics")
def get_system_metrics() -> dict[str, Any]:
    """Retrieve real-time host resource metrics: CPU %, Memory %, Disk %, DB size, uptime."""
    try:
        service = get_system_service()
        return service.get_metrics()
    except Exception as exc:
        logger.exception("Failed to fetch system metrics")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/health")
def get_health() -> dict[str, Any]:
    """Retrieve aggregated health snapshot across all system components."""
    service = get_system_service()
    return service.get_health()


@router.get("/api/config")
@router.get("/api/system/config")
def get_system_config() -> dict[str, Any]:
    """Retrieve non-sensitive operational parameters."""
    service = get_system_service()
    return service.get_config()


@router.get("/api/schedule")
def get_batch_schedule() -> dict[str, Any]:
    """Retrieve strategy batch execution schedule and last trigger timestamp."""
    service = get_system_service()
    return service.get_batch_schedule()


@router.post("/api/system/reset")
def reset_system(payload: ResetRequest) -> dict[str, Any]:
    """Wipe all trading tables in SQLite and clear log files upon verification."""
    try:
        service = get_system_service()
        return service.reset_system(payload.confirmation)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to execute system reset")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class UpdateConfigRequest(BaseModel):
    """Payload for updating risk and capital configuration."""

    trade_capital_pct: float | None = Field(default=None, description="Capital allocation percentage per trade (1-100)")
    risk_ratio: float | None = Field(default=None, description="Stop-loss risk ratio (0.001 - 0.20)")
    reward_ratio: float | None = Field(default=None, description="Take-profit reward ratio (0.001 - 0.50)")


class ResetPaperRequest(BaseModel):
    """Payload for resetting paper trading account balances."""

    starting_capital: float = Field(default=100.0, description="Starting capital per strategy account")


@router.post("/api/system/config")
def update_system_config(payload: UpdateConfigRequest) -> dict[str, Any]:
    """Update risk management parameters and trade capital allocation."""
    try:
        service = get_system_service()
        return service.update_trading_config(
            trade_capital_pct=payload.trade_capital_pct,
            risk_ratio=payload.risk_ratio,
            reward_ratio=payload.reward_ratio,
        )
    except Exception as exc:
        logger.exception("Failed to update system config")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/system/reset-paper")
def reset_paper_balances(payload: ResetPaperRequest | None = None) -> dict[str, Any]:
    """Reset virtual paper trading accounts back to starting capital and clear paper positions."""
    try:
        service = get_system_service()
        req = payload or ResetPaperRequest()
        return service.reset_paper_balances(starting_capital=req.starting_capital)
    except Exception as exc:
        logger.exception("Failed to reset paper balances")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class UpdatePipelineSettingsRequest(BaseModel):
    """Payload for updating pipeline symbols, strategies, and schedule frequency."""

    symbols: str | None = Field(default=None, description="Comma-separated symbols, e.g. BTC-USD,ETH-USD,SOL-USD")
    strategies: str | None = Field(default=None, description="Comma-separated strategy class names or '*' for all")
    schedule_seconds: int | None = Field(default=None, description="Pipeline schedule interval in seconds (min 10s)")


@router.post("/api/system/pipeline-settings")
def update_pipeline_settings(payload: UpdatePipelineSettingsRequest) -> dict[str, Any]:
    """Update pipeline symbols, strategies, and batch schedule frequency."""
    try:
        service = get_system_service()
        return service.update_pipeline_settings(
            symbols=payload.symbols,
            strategies=payload.strategies,
            schedule_seconds=payload.schedule_seconds,
        )
    except Exception as exc:
        logger.exception("Failed to update pipeline settings")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/batch/run")
@router.post("/api/system/trigger-batch")
def trigger_batch_now() -> dict[str, Any]:
    """Manually dispatch the batch strategy pipeline immediately."""
    try:
        from app.core.tasks import trigger_batch_execution

        task = trigger_batch_execution.delay(force=True)
        return {
            "status": "success",
            "message": "Batch strategy execution pipeline dispatched.",
            "task_id": str(task.id),
        }
    except Exception as exc:
        logger.exception("Failed to trigger batch pipeline")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/system/celery")
def get_celery_cluster_status() -> dict[str, Any]:
    """Retrieve detailed Celery worker nodes, concurrency, child processes, queues, and tasks."""
    try:
        from app.core.celery_app import celery_app
        from app.core.health_monitor import _check_celery_workers

        cluster = _check_celery_workers()
        beat_tasks = []
        for name, entry in celery_app.conf.beat_schedule.items():
            beat_tasks.append({
                "name": name,
                "task": entry.get("task"),
                "schedule": str(entry.get("schedule")),
            })
        cluster["scheduled_beat_tasks"] = beat_tasks
        return cluster
    except Exception as exc:
        logger.exception("Failed to fetch Celery cluster metrics")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/system/telemetry")
def get_system_telemetry() -> dict[str, Any]:
    try:
        from app.core.health_monitor import _check_celery_workers, _check_websocket, _check_zeromq

        return {
            "celery": _check_celery_workers(),
            "websocket": _check_websocket(),
            "zeromq": _check_zeromq(),
        }
    except Exception as exc:
        logger.exception("Failed to fetch system telemetry")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/system/public-ip")
def get_system_public_ip() -> dict[str, Any]:
    """Retrieve outgoing server public IP for Delta Exchange whitelist configuration."""
    from app.services.broker_service import get_broker_service

    service = get_broker_service()
    ip_addr = service.get_server_ip()
    return {
        "ip": ip_addr,
        "public_ip": ip_addr,
        "status": "success",
    }

