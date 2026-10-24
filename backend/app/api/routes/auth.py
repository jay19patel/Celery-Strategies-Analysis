"""PIN login, the session cookie, and changing the PIN."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.config import get_config
from app.core import security
from app.errors import AppError, Refused

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILURES = 5
LOCKOUT_SECONDS = 300
_failures: dict[str, list[float]] = {}


class TooManyAttempts(AppError):
    status = 429


class Login(BaseModel):
    pin: str


class ChangePin(BaseModel):
    current: str
    new: str


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login")
def login(body: Login, request: Request, response: Response) -> dict:
    who, now = _client(request), time.time()
    recent = [t for t in _failures.get(who, []) if now - t < LOCKOUT_SECONDS]
    if len(recent) >= MAX_FAILURES:
        raise TooManyAttempts(f"too many wrong PINs: try again in {int(LOCKOUT_SECONDS - (now - recent[0]))}s")
    if not security.verify_pin(body.pin):
        _failures[who] = [*recent, now]
        raise Refused("wrong PIN")
    _failures.pop(who, None)
    response.set_cookie(
        security.COOKIE, security.issue_session(), max_age=get_config().session_days * 86_400,
        httponly=True, samesite="lax", path="/",
    )
    return {"authenticated": True, "using_default_pin": security.using_default_pin()}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(security.COOKIE, path="/")
    return {"authenticated": False}


@router.get("/session")
def session_status(request: Request) -> dict:
    ok = security.valid_session(request.cookies.get(security.COOKIE))
    return {"authenticated": ok, "using_default_pin": security.using_default_pin() if ok else None}


@router.post("/pin")
def change_pin(body: ChangePin) -> dict:
    security.change_pin(body.current, body.new)
    return {"ok": True}
