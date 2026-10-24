"""Authentication API router — PIN verification, logout, session check, and PIN change."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.api.deps import get_auth_service, get_settings_repo, require_auth
from app.application.auth_service import AuthService
from app.infrastructure.database.repositories.settings_repo import SettingsRepository

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """User login request with PIN."""

    pin: str = Field(min_length=4, max_length=16, description="PIN code")


class ChangePinRequest(BaseModel):
    """Request to update user PIN."""

    current_pin: str = Field(min_length=4, max_length=16)
    new_pin: str = Field(min_length=4, max_length=16)


@router.post("/login")
async def login(
    payload: LoginRequest,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings_repo: Annotated[SettingsRepository, Depends(get_settings_repo)],
) -> dict[str, str]:
    """Verify PIN and issue a session token with HTTP-only cookie."""
    stored_hash = settings_repo.get_value("auth_pin_hash")
    token = await auth_service.verify_pin(payload.pin, stored_hash=stored_hash)

    # Set secure HTTP-only cookie
    response.set_cookie(
        key="tb_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7,  # 7 days
    )
    return {"token": token, "status": "authenticated"}


@router.post("/logout")
async def logout(
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    _: Annotated[bool, Depends(require_auth)],
    token: str = "",
) -> dict[str, str]:
    """Invalidate session and clear cookie."""
    if token:
        await auth_service.logout(token)
    response.delete_cookie("tb_session")
    return {"status": "logged_out"}


@router.get("/verify")
async def verify_session(
    _: Annotated[bool, Depends(require_auth)],
) -> dict[str, bool]:
    """Check if the caller is currently authenticated."""
    return {"authenticated": True}


@router.post("/change-pin")
async def change_pin(
    payload: ChangePinRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings_repo: Annotated[SettingsRepository, Depends(get_settings_repo)],
    _: Annotated[bool, Depends(require_auth)],
) -> dict[str, str]:
    """Update system access PIN."""
    stored_hash = settings_repo.get_value("auth_pin_hash")
    new_hash = await auth_service.change_pin(
        current_pin=payload.current_pin,
        new_pin=payload.new_pin,
        stored_hash=stored_hash,
    )
    settings_repo.set_value("auth_pin_hash", new_hash)
    return {"status": "pin_updated"}
