"""Broker execution, profile configuration, authority verification, and emergency exit router.

Routes incoming HTTP requests to app.services.BrokerService.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.broker_service import get_broker_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/broker", tags=["Broker"])


class ArmRequest(BaseModel):
    """Payload for live trading arming confirmation."""

    confirmation: str = Field(..., description="Confirmation phrase 'ARM LIVE TRADING'")


class ModeRequest(BaseModel):
    """Payload for switching execution mode."""

    mode: str = Field(..., description="Execution mode: 'PAPER' or 'LIVE'")


class CancelOrderRequest(BaseModel):
    """Payload for cancelling a specific live order."""

    order_id: str
    product_id: int | None = None


class SaveProfileRequest(BaseModel):
    """Payload for saving or updating broker API credentials."""

    base_url: str = Field(
        default="https://api.india.delta.exchange",
        description="Delta Exchange API Base URL",
    )
    api_key: str = Field(..., description="Delta Exchange API Key")
    api_secret: str = Field(..., description="Delta Exchange API Secret")
    client_id: int = Field(default=0, description="Delta Exchange Client ID / User ID")


class VerifyAuthRequest(BaseModel):
    """Payload for verifying broker authorization on-demand."""

    base_url: str | None = None
    api_key: str | None = None
    api_secret: str | None = None
    client_id: int | None = None


class ToggleLiveRequest(BaseModel):
    """Payload for enabling or disabling live broker trade execution."""

    enabled: bool = Field(..., description="True to enable live trading, False to disable")
    confirmation: str | None = Field(default=None, description="Confirmation phrase 'ARM LIVE TRADING' if enabling")


@router.get("/status")
def get_broker_status() -> dict[str, Any]:
    """Retrieve execution mode, live trading arming status, and Delta API readiness."""
    service = get_broker_service()
    return service.get_status()


@router.get("/profile")
def get_broker_profile() -> dict[str, Any]:
    """Retrieve broker profile, authorization status, and masked credentials."""
    service = get_broker_service()
    return service.get_broker_profile()


@router.post("/profile")
def save_broker_profile(payload: SaveProfileRequest) -> dict[str, Any]:
    """Save or update broker API credentials."""
    try:
        service = get_broker_service()
        return service.save_broker_profile(
            base_url=payload.base_url,
            api_key=payload.api_key,
            api_secret=payload.api_secret,
            client_id=payload.client_id,
        )
    except Exception as exc:
        logger.exception("Failed to save broker profile")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/verify-auth")
@router.post("/verify-authority")
def verify_broker_authority(payload: VerifyAuthRequest | None = None) -> dict[str, Any]:
    """Test and verify API authorization against Delta Exchange."""
    try:
        service = get_broker_service()
        req = payload or VerifyAuthRequest()
        return service.verify_authority(
            base_url=req.base_url,
            api_key=req.api_key,
            api_secret=req.api_secret,
            client_id=req.client_id,
        )
    except Exception as exc:
        logger.exception("Failed to verify broker authority")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/toggle-live")
def toggle_live_trading(payload: ToggleLiveRequest) -> dict[str, Any]:
    """Enable or disable real live order execution with explicit safety confirmation."""
    try:
        service = get_broker_service()
        return service.toggle_live_trading(
            enabled=payload.enabled,
            confirmation=payload.confirmation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to toggle live trading")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/arm")
def arm_live_broker(payload: ArmRequest) -> dict[str, Any]:
    """Arm live trade execution after verifying the explicit confirmation phrase."""
    try:
        service = get_broker_service()
        return service.arm_live_trading(payload.confirmation)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to arm live trading")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/disarm")
def disarm_live_broker() -> dict[str, Any]:
    """Disarm live trade execution and safely revert the system to PAPER mode."""
    service = get_broker_service()
    return service.disarm_live_trading()


@router.post("/mode")
def set_broker_mode(payload: ModeRequest) -> dict[str, Any]:
    """Switch execution mode between PAPER and LIVE with safety validation."""
    try:
        service = get_broker_service()
        return service.set_execution_mode(payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to switch broker mode")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/balance")
def get_broker_balance() -> dict[str, Any]:
    """Retrieve real-time wallet balance (USD and INR) from Delta Exchange."""
    service = get_broker_service()
    return service.get_balance()


@router.get("/server-ip")
def get_server_public_ip() -> dict[str, Any]:
    """Retrieve outgoing server public IP address for Delta Exchange whitelist configuration."""
    service = get_broker_service()
    return {"ip": service.get_server_ip(), "status": "success"}


@router.get("/paper-positions")
def get_paper_positions() -> list[dict[str, Any]]:
    """Retrieve active simulated paper trading positions from broker_accounts."""
    service = get_broker_service()
    return service.get_paper_positions()


@router.get("/paper-orders")
def get_paper_orders() -> list[dict[str, Any]]:
    """Retrieve simulated paper trading orders and closed trade logs."""
    service = get_broker_service()
    return service.get_paper_orders()


@router.get("/positions")
def get_broker_positions(mode: str | None = None) -> list[dict[str, Any]]:
    """Retrieve active positions from Delta Exchange (LIVE) or virtual sandbox (PAPER)."""
    service = get_broker_service()
    return service.get_positions(mode=mode)


@router.get("/orders")
def get_broker_orders(mode: str | None = None) -> list[dict[str, Any]]:
    """Retrieve open orders from Delta Exchange (LIVE) or virtual sandbox (PAPER)."""
    service = get_broker_service()
    return service.get_orders(mode=mode)


@router.post("/cancel-order")
def cancel_broker_order(payload: CancelOrderRequest) -> dict[str, Any]:
    """Cancel a specific live order via Delta Exchange."""
    service = get_broker_service()
    return service.cancel_order(order_id=payload.order_id, product_id=payload.product_id)


@router.post("/emergency-exit")
def emergency_exit() -> dict[str, Any]:
    """EMERGENCY PANIC SWITCH: Cancels all open orders and market-closes all positions."""
    try:
        service = get_broker_service()
        return service.emergency_exit()
    except Exception as exc:
        logger.exception("Emergency exit trigger failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
