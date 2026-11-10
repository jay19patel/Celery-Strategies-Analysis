"""PIN login, the session cookie, and changing the PIN."""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.config import get_config
from app.core import security
from app.errors import AppError, Refused

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILURES = 5  # per client
MAX_FAILURES_ALL = 30  # across every client: guessing from many addresses is still guessing
LOCKOUT_SECONDS = 300
LOOPBACK = ("127.0.0.1", "::1")
_failures: dict[str, list[float]] = {}
_guard = threading.Lock()  # login runs in the threadpool: the count must not lose an update


class TooManyAttempts(AppError):
    status = 429


class Login(BaseModel):
    pin: str


class ChangePin(BaseModel):
    current: str
    new: str


def _set_session(response: Response) -> None:
    response.set_cookie(
        security.COOKIE, security.issue_session(), max_age=get_config().session_days * 86_400,
        httponly=True, samesite="lax", path="/",
    )


def _client(request: Request) -> str:
    """Who is asking. Behind the dashboard's own proxy on this machine every request comes from
    loopback, so the address the proxy forwarded for is the one counted."""
    host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded if host in LOOPBACK and forwarded else host


@router.post("/login")
def login(body: Login, request: Request, response: Response) -> dict:
    who, now = _client(request), time.time()
    with _guard:
        for ip in [ip for ip, ts in _failures.items() if now - ts[-1] >= LOCKOUT_SECONDS]:
            del _failures[ip]  # scanners hit the login from new addresses forever; forget the stale ones
        recent = [t for t in _failures.get(who, []) if now - t < LOCKOUT_SECONDS]
        everyone = sum(1 for ts in _failures.values() for t in ts if now - t < LOCKOUT_SECONDS)
        if len(recent) >= MAX_FAILURES or everyone >= MAX_FAILURES_ALL:
            first = recent[0] if len(recent) >= MAX_FAILURES else min(t for ts in _failures.values() for t in ts)
            raise TooManyAttempts(f"too many wrong PINs: try again in {int(LOCKOUT_SECONDS - (now - first))}s")
        if not security.verify_pin(body.pin):
            _failures[who] = [*recent, now]
            raise Refused("wrong PIN")
        _failures.pop(who, None)
    _set_session(response)
    return {"authenticated": True, "using_default_pin": security.using_default_pin()}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(security.COOKIE, path="/")
    return {"authenticated": False}


@router.get("/session")
def session_status(request: Request) -> dict:
    ok = security.valid_session(request.cookies.get(security.COOKIE))
    return {"authenticated": ok, "using_default_pin": security.using_default_pin() if ok else None}


@router.get("/ws")
def ws_ticket(request: Request) -> dict:
    """Where the live socket is and a one-time ticket into it. Asked through the same /api proxy as
    every other call, so the Host is the backend's own public address."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return {"url": f"{'wss' if proto == 'https' else 'ws'}://{request.headers.get('host', request.url.netloc)}/ws",
            "ticket": security.issue_ticket()}


@router.post("/pin")
def change_pin(body: ChangePin, response: Response) -> dict:
    """A new PIN ends every other session; this one gets a fresh cookie."""
    security.change_pin(body.current, body.new)
    _set_session(response)
    return {"ok": True}
