"""Dashboard: server-rendered pages (Jinja2) + JSON API + one WebSocket for live events.

Routes are thin: each one calls a method on `Api` — the engine itself in
single-process mode, or the engine over ZeroMQ RPC in distributed mode.
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from tradebuddy import auth
from tradebuddy.api import Api, ApiError, RemoteApi
from tradebuddy.settings import LIVE_CONFIRM_PHRASE
from tradebuddy.system import Broadcaster, System

HERE = Path(__file__).parent

# (id, path, lucide icon, title, nav group)
PAGES = [
    ("overview", "/", "layout-dashboard", "Overview", "Trading"),
    ("ai", "/ai", "sparkles", "TradeBuddy AI", "Trading"),
    ("strategies", "/strategies", "brain-circuit", "Strategies", "Trading"),
    ("signals", "/signals", "activity", "Signals", "Trading"),
    ("positions", "/positions", "layers", "Positions", "Portfolio"),
    ("orders", "/orders", "list-checks", "Orders", "Portfolio"),
    ("account", "/account", "wallet", "Account", "Portfolio"),
    ("journal", "/journal", "calendar-days", "Trading Journal", "Portfolio"),
    ("market", "/market", "candlestick-chart", "Market Data", "Monitoring"),
    ("system", "/system", "gauge", "System", "Monitoring"),
    ("events", "/events", "scroll-text", "Event Log", "Monitoring"),
    ("settings", "/settings", "settings", "Settings", "Admin"),
]


class Toggle(BaseModel):
    key: str
    enabled: bool


class SettingsUpdate(BaseModel):
    changes: dict[str, Any]
    confirm: str = ""


class DeltaTest(BaseModel):
    env: str | None = None
    api_key: str = ""
    api_secret: str = ""


class Protection(BaseModel):
    stop_loss: float
    take_profit: float | None = None


class ManualOrder(BaseModel):
    brokers: list[str] = Field(min_length=1, max_length=4)
    symbol: str
    side: Literal["buy", "sell"]
    size: int | None = Field(default=None, ge=1)  # contracts, one broker only
    margin_pct: float | None = Field(default=None, gt=0, le=100)  # or a share of each broker's available margin
    request_id: str = Field(min_length=8, max_length=64)
    stop_loss: float = Field(gt=0)
    take_profit: float | None = Field(default=None, gt=0)


class StructurePick(BaseModel):
    action: Literal["buy", "sell"]
    kind: Literal["call", "put"]
    strike: float = Field(gt=0)


class StructureOrder(BaseModel):
    broker: Literal["paper"] = "paper"
    symbol: str = Field(min_length=1, max_length=32)
    kind: Literal["straddle", "strangle", "iron_condor", "call_spread", "put_spread"]
    qty: int = Field(ge=1, le=1000)
    request_id: str = Field(min_length=8, max_length=64)
    sl_pct: float = Field(ge=5, le=100)  # of the max loss; required, entries are always protected
    tp_pct: float | None = Field(default=None, ge=5, le=500)  # of the premium
    expiry: float | None = None
    legs: list[str] = Field(min_length=2, max_length=4)  # the contracts the person reviewed
    picks: list[StructurePick] | None = Field(default=None, max_length=4)


class AITest(BaseModel):
    model: str = Field(default="", max_length=64)


class PositionControl(BaseModel):
    trailing: bool | None = None
    max_steps: int | None = Field(default=None, ge=0, le=50)


class EmailReportRequest(BaseModel):
    date: str = Field(default="", max_length=32)


class PinLoginRequest(BaseModel):
    pin: str = Field(min_length=6, max_length=6)
    next: str = Field(default="/", max_length=256)


class PinChangeRequest(BaseModel):
    old_pin: str = Field(min_length=6, max_length=6)
    new_pin: str = Field(min_length=6, max_length=6)


def asset_version() -> str:
    digest = hashlib.sha256()
    for path in sorted((HERE / "static").rglob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_app(
    api: Api | RemoteApi,
    live: Broadcaster,
    lifespan: Lifespan | None = None,
    api_token: str = "",
    auth_pin: str = "242425",
    auth_secret: str = "",
) -> FastAPI:
    app = FastAPI(title="TradeBuddy", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    # Static URLs carry a hash of the files, so a browser never runs an old app.js against new pages.
    templates.env.globals["v"] = asset_version()

    if not auth_secret:
        auth_secret = hashlib.sha256(f"tb_auth_default:{api_token}:{auth_pin}".encode()).hexdigest()

    brute_force_guard = auth.BruteForceGuard()

    def has_valid_session(request: Request) -> bool:
        if not auth_pin:
            return False
        cookie = request.cookies.get(auth.SESSION_COOKIE_NAME)
        return bool(cookie and auth.validate_session_token(cookie, auth_secret))

    def is_authenticated(request: Request) -> bool:
        """Check whether request carries valid 7-day session cookie or valid API token."""
        if not auth_pin:
            return True
        if has_valid_session(request):
            return True
        token_header = request.headers.get("X-API-Token", "")
        return bool(api_token and token_header and hmac.compare_digest(token_header, api_token))

    def protected(request: Request, x_api_token: str = Header(default="")) -> None:
        """Protect mutating actions: allowed if session is active or valid API token is supplied."""
        if has_valid_session(request):
            return
        if api_token and hmac.compare_digest(x_api_token, api_token):
            return
        if api_token:
            raise HTTPException(401, "missing or wrong API token")
        if auth_pin and not is_authenticated(request):
            raise HTTPException(401, "Authentication required")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next: Any) -> Response:
        """Block unauthorized access to pages and APIs; redirect to PIN login screen."""
        path = request.url.path
        # Static files and auth endpoints remain publicly reachable
        if not auth_pin or path.startswith("/static/") or path in ("/login", "/logout", "/api/auth/login", "/api/auth/status", "/favicon.ico"):
            if path == "/login" and auth_pin and is_authenticated(request):
                return RedirectResponse(url="/", status_code=303)
            return await call_next(request)

        if not is_authenticated(request):
            # API requests return 401 JSON error
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Authentication required. Please log in with PIN."}, status_code=401)
            # Web page requests redirect to login with destination path
            next_url = request.url.path
            if request.url.query:
                next_url += f"?{request.url.query}"
            return RedirectResponse(url=f"/login?next={urllib.parse.quote(next_url)}", status_code=303)

        return await call_next(request)

    async def call(method: str, **params: Any) -> Any:
        try:
            return await getattr(api, method)(**params)
        except ApiError as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    # -- auth routes --------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse, response_model=None, include_in_schema=False)
    async def login_page(request: Request, next: str = "/") -> Response:
        if auth_pin and is_authenticated(request):
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "next": next or "/",
                "title": "Login · Security PIN",
                "v": asset_version(),
            },
        )

    @app.get("/logout", include_in_schema=False)
    async def logout_page() -> RedirectResponse:
        res = RedirectResponse(url="/login", status_code=303)
        res.delete_cookie(auth.SESSION_COOKIE_NAME, path="/")
        return res

    @app.post("/api/auth/login")
    async def api_auth_login(request: Request, body: PinLoginRequest, response: Response) -> dict[str, Any]:
        """Verify 4-digit PIN, check rate-limiting, and issue 7-day session cookie."""
        client_ip = request.client.host if request.client else "unknown"
        locked, remaining_seconds = brute_force_guard.is_locked(client_ip)
        if locked:
            raise HTTPException(429, f"Too many incorrect attempts. Locked out for {remaining_seconds}s.")

        pin = body.pin.strip()
        if len(pin) != 6 or not pin.isdigit():
            raise HTTPException(400, "PIN must be exactly 6 digits")

        is_valid = await call("verify_pin", pin=pin)
        if not is_valid:
            remaining, lock_time = brute_force_guard.record_failure(client_ip)
            if lock_time > 0:
                raise HTTPException(429, f"Incorrect PIN. Locked out for {lock_time} seconds.")
            raise HTTPException(401, f"Incorrect PIN. {remaining} attempt(s) remaining.")

        brute_force_guard.record_success(client_ip)
        # SECURITY: Create 7-day signed session token and set secure HTTP-only cookie
        token = auth.create_session_token(auth_secret, max_age_seconds=auth.SESSION_MAX_AGE_SECONDS)
        response.set_cookie(
            key=auth.SESSION_COOKIE_NAME,
            value=token,
            max_age=auth.SESSION_MAX_AGE_SECONDS,
            expires=auth.SESSION_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )
        return {"ok": True, "token": token, "redirect": body.next or "/"}

    @app.post("/api/auth/logout")
    async def api_auth_logout(response: Response) -> dict[str, Any]:
        """Clear 7-day session cookie."""
        response.delete_cookie(auth.SESSION_COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/auth/status")
    async def api_auth_status(request: Request) -> dict[str, Any]:
        """Return current authentication state."""
        return {
            "authenticated": is_authenticated(request),
            "pin_required": bool(auth_pin),
        }

    @app.post("/api/auth/change-pin")
    async def api_auth_change_pin(request: Request, body: PinChangeRequest) -> dict[str, Any]:
        """Update 6-digit PIN."""
        if not is_authenticated(request):
            raise HTTPException(401, "Authentication required")
        if not (body.new_pin.isdigit() and len(body.new_pin) == 6):
            raise HTTPException(400, "New PIN must be exactly 6 digits")
        try:
            return await call("change_pin", old_pin=body.old_pin, new_pin=body.new_pin)
        except ApiError as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    # -- pages --------------------------------------------------------------

    def page(page_id: str, path: str, title: str) -> None:
        async def render(request: Request) -> HTMLResponse:
            ctx = await call("page_context")
            return templates.TemplateResponse(
                request,
                f"{page_id}.html",
                {
                    "page": page_id,
                    "title": title,
                    "pages": [
                        p for p in PAGES
                        if (p[0] != "paper" or ctx["paper_active"])
                        and (p[0] != "ai" or ctx.get("ai_enabled") or page_id == "ai")
                    ],
                    "active_brokers": ctx["active_brokers"],
                    "live_phrase": LIVE_CONFIRM_PHRASE,
                },
            )

        app.add_api_route(path, render, methods=["GET"], response_class=HTMLResponse, include_in_schema=False, name=f"page_{page_id}")

    for page_id, path, _icon, title, _group in PAGES:
        page(page_id, path, title)

    @app.get("/paper", include_in_schema=False)
    async def paper_redirect() -> RedirectResponse:
        return RedirectResponse(url="/account?broker=paper")

    # -- read ---------------------------------------------------------------

    @app.get("/api/header")
    async def header() -> dict:
        return await call("header")

    @app.get("/api/overview")
    async def overview() -> dict:
        return await call("overview")

    @app.get("/api/strategies")
    async def strategies() -> list[dict]:
        return await call("strategies")

    @app.get("/api/signals")
    async def signals(limit: int = 300, strategy: str = "", symbol: str = "") -> list[dict]:
        return await call("signals", limit=limit, strategy=strategy, symbol=symbol)

    @app.get("/api/positions")
    async def positions(broker: str | None = None) -> list[dict]:
        return await call("positions", broker=broker)

    @app.get("/api/orders")
    async def orders(broker: str | None = None, limit: int = 300) -> list[dict]:
        return await call("orders", broker=broker, limit=limit)

    @app.post("/api/orders", dependencies=[Depends(protected)])
    async def place_order(body: ManualOrder) -> dict:
        return await call("place_order", **body.model_dump())

    @app.get("/api/order-ticket")
    async def order_ticket(symbol: str, side: Literal["buy", "sell"] = "buy", margin_pct: float | None = None) -> dict:
        return await call("order_ticket", symbol=symbol, side=side, margin_pct=margin_pct)

    @app.get("/api/option-ticket")
    async def option_ticket(symbol: str = "", kind: str = "", qty: int = 1, expiry: float = 0, suggested: bool = False) -> dict:
        return await call("option_ticket", symbol=symbol, kind=kind, qty=qty, expiry=expiry, suggested=suggested)

    @app.get("/api/structures")
    async def structures(broker: str = "paper") -> list:
        return await call("structures", broker=broker)

    @app.post("/api/structures", dependencies=[Depends(protected)])
    async def place_structure(body: StructureOrder) -> dict:
        data = body.model_dump()
        data["picks"] = data["picks"] or None
        return await call("place_structure", **data)

    @app.post("/api/structures/{broker}/{structure_id}/close", dependencies=[Depends(protected)])
    async def close_structure(broker: str, structure_id: int) -> dict:
        return await call("close_structure", broker=broker, structure_id=structure_id)

    @app.get("/api/risk")
    async def risk() -> dict:
        return await call("risk")

    @app.post("/api/risk/{broker}/resume", dependencies=[Depends(protected)])
    async def risk_resume(broker: str) -> dict:
        return await call("risk_resume", broker=broker)

    @app.post("/api/positions/{broker}/{symbol}/control", dependencies=[Depends(protected)])
    async def position_control(broker: str, symbol: str, body: PositionControl) -> dict:
        return await call("set_position_control", broker=broker, symbol=symbol, trailing=body.trailing, max_steps=body.max_steps)

    @app.get("/api/open-orders")
    async def open_orders(broker: str | None = None) -> list[dict]:
        return await call("open_orders", broker=broker)

    @app.get("/api/account")
    async def account(broker: str | None = None) -> dict:
        return await call("account", broker=broker)

    @app.get("/api/paper/stats")
    async def paper_stats() -> dict:
        return await call("paper_stats")

    @app.get("/api/paper/history")
    async def paper_history(page: int = 1, per_page: int = 25, strategy: str = "", q: str = "", outcome: str = "") -> dict:
        return await call("paper_history", page=page, per_page=per_page, strategy=strategy, q=q, outcome=outcome)

    @app.get("/api/paper/trades")
    async def paper_trades(limit: int = 300) -> list[dict]:
        return await call("paper_trades", limit=limit)

    @app.get("/api/metrics")
    async def metrics() -> dict:
        return await call("metrics") | {"dashboard_clients": len(live.clients)}

    @app.get("/api/analysis")
    async def analysis() -> dict:
        return await call("analysis")

    @app.get("/api/options/history")
    async def options_history(symbol: str = "BTCUSD", hours: float = 24) -> list[dict]:
        return await call("options_history", symbol=symbol, hours=hours)

    @app.get("/api/database")
    async def database() -> dict:
        return await call("database")

    @app.get("/api/ai")
    async def ai_report() -> dict:
        return await call("ai_report")

    @app.get("/api/ai/history")
    async def ai_history(start: float = 0.0, end: float = 0.0, ok_only: bool = False, limit: int = 500) -> list[dict]:
        return await call("ai_history", start=start, end=end, ok_only=ok_only, limit=limit)

    @app.get("/api/ai/reports/{report_id}")
    async def ai_report_at(report_id: int) -> dict:
        return await call("ai_report_at", report_id=report_id)

    @app.post("/api/ai/test", dependencies=[Depends(protected)])
    async def test_ai(body: AITest) -> dict:
        return await call("test_ai", model=body.model)

    @app.get("/api/events")
    async def events(limit: int = 300, type: str = "", level: Literal["", "warning", "error"] = "") -> list[dict]:
        return await call("events", limit=limit, type=type, level=level)

    @app.get("/api/settings")
    async def get_settings() -> dict:
        return await call("settings")

    @app.get("/api/journal/summary")
    async def journal_summary(date: str = "") -> dict:
        return await call("journal_summary", date=date)

    @app.get("/api/journal/month")
    async def journal_month(year: int = 0, month: int = 0) -> dict:
        return await call("journal_month", year=year, month=month)

    # -- write --------------------------------------------------------------

    @app.put("/api/settings", dependencies=[Depends(protected)])
    async def put_settings(body: SettingsUpdate) -> dict:
        return await call("update_settings", changes=body.changes, confirm=body.confirm)

    @app.post("/api/settings/clear-credentials", dependencies=[Depends(protected)])
    async def clear_credentials() -> dict:
        return await call("clear_credentials")

    @app.post("/api/send-email-report", dependencies=[Depends(protected)])
    async def send_email_report(body: EmailReportRequest = EmailReportRequest()) -> dict:
        return await call("send_email_report", date=body.date)

    @app.post("/api/settings/test-delta", dependencies=[Depends(protected)])
    async def test_delta(body: DeltaTest) -> dict:
        return await call("test_delta", env=body.env, api_key=body.api_key, api_secret=body.api_secret)

    @app.post("/api/toggles", dependencies=[Depends(protected)])
    async def toggle(body: Toggle) -> dict:
        return await call("toggle", key=body.key, enabled=body.enabled)

    @app.post("/api/close-all", dependencies=[Depends(protected)])
    async def close_all() -> dict:
        return await call("close_all")

    @app.post("/api/positions/{broker}/{symbol}/close", dependencies=[Depends(protected)])
    async def close_position(broker: str, symbol: str) -> dict:
        return await call("close_position", broker=broker, symbol=symbol)

    @app.post("/api/positions/{broker}/{symbol}/protection", dependencies=[Depends(protected)])
    async def protection(broker: str, symbol: str, body: Protection) -> dict:
        return await call("protection", broker=broker, symbol=symbol, stop_loss=body.stop_loss, take_profit=body.take_profit)

    @app.post("/api/paper/reset", dependencies=[Depends(protected)])
    async def paper_reset() -> dict:
        return await call("paper_reset")

    # -- live ---------------------------------------------------------------

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        if auth_pin:
            cookie = socket.cookies.get(auth.SESSION_COOKIE_NAME, "")
            param = socket.query_params.get("token", "")
            hdr = socket.headers.get("x-api-token", "")
            is_authed = (
                (cookie and auth.validate_session_token(cookie, auth_secret))
                or (param and auth.validate_session_token(param, auth_secret))
                or (api_token and hdr and hmac.compare_digest(hdr, api_token))
            )
            if not is_authed:
                # SECURITY: Reject unauthenticated WebSocket connection attempts
                await socket.close(code=1008, reason="Unauthorized")
                return

        await socket.accept()
        live.clients.add(socket)
        try:
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            live.clients.discard(socket)

    return app


def local_app(system: System) -> FastAPI:
    """Single process: the dashboard talks to the engine directly."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await system.start()
        yield
        await system.stop()

    return create_app(
        Api(system),
        system.live,
        lifespan,
        system.cfg.api_token,
        auth_pin=system.cfg.auth_pin,
        auth_secret=system.cfg.auth_secret,
    )
