"""System API router — health monitoring, runtime metrics, and audit events."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_system_service, require_auth
from app.application.system_service import SystemService

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health_check(
    system_service: Annotated[SystemService, Depends(get_system_service)],
) -> dict[str, Any]:
    """Public health-check endpoint for container orchestration and uptime monitors."""
    return system_service.get_system_health()


@router.get("/events")
def list_events(
    system_service: Annotated[SystemService, Depends(get_system_service)],
    _: Annotated[bool, Depends(require_auth)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    event_type: Annotated[str | None, Query()] = None,
    since_id: Annotated[int | None, Query()] = None,
) -> list[dict[str, Any]]:
    """Retrieve historical system audit events and execution logs."""
    return system_service.get_events(limit=limit, event_type=event_type, since_id=since_id)
