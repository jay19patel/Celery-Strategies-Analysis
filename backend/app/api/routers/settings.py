"""Settings API router — system configuration and feature toggles."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_settings_service, require_auth
from app.application.settings_service import SettingsService

router = APIRouter(prefix="/settings", tags=["settings"])


class UpdateSettingsRequest(BaseModel):
    """Payload to update key-value configuration."""

    settings: dict[str, Any] = Field(description="Dictionary of key-value setting changes")
    confirm: str = Field(default="", description="Required confirmation phrase when enabling live trading")


class ToggleRequest(BaseModel):
    """Payload to toggle a feature flag."""

    key: str = Field(min_length=1, max_length=128)
    enabled: bool


@router.get("")
def get_settings(
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> dict[str, Any]:
    """Retrieve all configuration settings with sensitive secrets masked."""
    return settings_service.get_all()


@router.put("")
def update_settings(
    payload: UpdateSettingsRequest,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> dict[str, Any]:
    """Validate and update configuration settings."""
    warnings = settings_service.update_settings(
        changes=payload.settings,
        confirm=payload.confirm,
    )
    return {
        "status": "updated",
        "warnings": warnings,
        "settings": settings_service.get_all(),
    }


@router.get("/toggles")
def get_toggles(
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> list[dict[str, Any]]:
    """Retrieve all operational feature flags and strategies toggles."""
    return settings_service.get_toggles()


@router.post("/toggle")
def set_toggle(
    payload: ToggleRequest,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
    _: Annotated[bool, Depends(require_auth)],
) -> dict[str, Any]:
    """Enable or disable a feature flag or strategy toggle."""
    settings_service.update_toggle(key=payload.key, enabled=payload.enabled)
    return {"key": payload.key, "enabled": payload.enabled, "status": "updated"}
