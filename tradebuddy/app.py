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
# Options Desk is the landing page: options chains, Greeks, and non-directional execution.
PAGES = [
    ("options", "/", "sparkles", "Options Desk", "Trading"),
    ("positions", "/positions", "layers", "Positions", "Portfolio"),
    ("orders", "/orders", "list-checks", "Orders", "Portfolio"),
    ("account", "/account", "wallet", "Account", "Portfolio"),
    ("journal", "/journal", "calendar-days", "Trading Journal", "Portfolio"),
    ("system", "/system", "gauge", "System", "Admin"),
    ("settings", "/settings", "settings", "Settings", "Admin"),
]


# The dashboard needs the PIN. Everything — pages, API, the live stream — unless it is listed here.
#
# It used to be the other way round: writes needed the PIN and every GET was open, so that someone
# could watch without unlocking. That is only safe on loopback, and this dashboard is served to the
# internet through a reverse proxy. Open reads meant /api/account returned the balance and
# /api/positions the entry and liquidation prices to anyone who asked, and /ws — which is not under
# /api/ and so was never even looked at — streamed every PositionClosed, PnL included.
#
# Private by default, so a route added later is private unless someone deliberately lists it.
PUBLIC_PATHS = {"/login", "/logout", "/healthz", "/favicon.ico", "/api/auth/login", "/api/auth/logout", "/api/auth/status"}
PUBLIC_PREFIXES = ("/static/",)
LOCKED = "Locked: enter your PIN to make changes"


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def safe_next(url: str) -> str:
    """A post-login destination on this site only: a path, never another host."""
    url = (url or "").strip()
    if not url.startswith("/") or url.startswith("//") or "\\" in url:
        return "/"
    return url


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


class OptionsOrderLeg(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    size: int = Field(default=1, ge=1)


class OptionsOrderRequest(BaseModel):
    broker: str = "paper"
    strategy: str = "single"  # "iron_condor" | "straddle" | "strangle" | "single"
    legs: list[OptionsOrderLeg]
    request_id: str = Field(default="", max_length=64)




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

    # Whether anything gates this dashboard at all. Keying the gate on the PIN alone left a hole:
    # AUTH_PIN="" with an API_TOKEN set passed require_safe_bind and then served every read to
    # anyone, because a token-only deployment skipped the check entirely. Either credential arms it.
    gated = bool(auth_pin or api_token)

    def has_valid_session(request: Request | WebSocket) -> bool:
        if not auth_pin:
            return False
        cookie = request.cookies.get(auth.SESSION_COOKIE_NAME)
        return bool(cookie and auth.validate_session_token(cookie, auth_secret))

    def is_authenticated(request: Request | WebSocket) -> bool:
        """A valid 7-day session cookie or the API token. Takes a WebSocket too: both carry
        cookies and headers, and the live stream needs the same gate as the routes."""
        if not gated:
            return True
        if has_valid_session(request):
            return True
        token_header = request.headers.get("X-API-Token", "")
        return bool(api_token and token_header and hmac.compare_digest(token_header, api_token))

    def protected(request: Request, x_api_token: str = Header(default="")) -> None:
        """Every state-changing route: an unlocked PIN session, or the API token."""
        if has_valid_session(request):
            return
        if api_token and hmac.compare_digest(x_api_token, api_token):
            return
        if auth_pin:
            raise HTTPException(401, LOCKED)
        if api_token:
            raise HTTPException(401, "missing or wrong API token")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next: Any) -> Response:
        """Everything needs the PIN except the login flow and static assets."""
        if not gated or is_authenticated(request):
            return await call_next(request)
        path = request.url.path
        if is_public(path):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"detail": LOCKED}, status_code=401)
        return RedirectResponse(url=f"/login?next={urllib.parse.quote(path)}", status_code=303)

    async def call(method: str, **params: Any) -> Any:
        try:
            return await getattr(api, method)(**params)
        except ApiError as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    # -- auth routes --------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse, response_model=None, include_in_schema=False)
    async def login_page(request: Request, next: str = "/") -> Response:
        if is_authenticated(request):
            return RedirectResponse(url=safe_next(next), status_code=303)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "next": safe_next(next),
                "title": "Login · Security PIN",
                "v": asset_version(),
            },
        )

    @app.get("/logout", include_in_schema=False)
    async def logout_page() -> RedirectResponse:
        # Locking drops back to the read-only dashboard, not to a login wall.
        res = RedirectResponse(url="/", status_code=303)
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
        # The token travels only in the HttpOnly cookie, never in a body page script can read.
        return {"ok": True, "redirect": safe_next(body.next)}

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

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """Public on purpose, and says nothing: a proxy needs to know the process answers, and
        a liveness probe that needs a credential is a liveness probe that fails closed."""
        return {"status": "ok"}

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
                        if p[0] != "paper" or ctx["paper_active"]
                    ],
                    "active_brokers": ctx["active_brokers"],
                    "live_phrase": LIVE_CONFIRM_PHRASE,
                    "can_edit": is_authenticated(request),
                    "pin_required": bool(auth_pin),
                    "demo_data": ctx.get("demo_data", False),
                },
            )

        app.add_api_route(path, render, methods=["GET"], response_class=HTMLResponse, include_in_schema=False, name=f"page_{page_id}")

    for page_id, path, _icon, title, _group in PAGES:
        page(page_id, path, title)

    @app.get("/market", include_in_schema=False)
    async def market_redirect() -> RedirectResponse:
        return RedirectResponse(url="/")

    @app.get("/overview", include_in_schema=False)
    async def overview_redirect() -> RedirectResponse:
        return RedirectResponse(url="/")

    @app.get("/signals", include_in_schema=False)
    async def signals_redirect() -> RedirectResponse:
        return RedirectResponse(url="/")

    @app.get("/events", include_in_schema=False)
    async def events_redirect() -> RedirectResponse:
        return RedirectResponse(url="/system")

    @app.get("/strategies", include_in_schema=False)
    async def strategies_redirect() -> RedirectResponse:
        return RedirectResponse(url="/")

    @app.get("/paper", include_in_schema=False)
    async def paper_redirect() -> RedirectResponse:
        return RedirectResponse(url="/account?broker=paper")

    # -- options desk -------------------------------------------------------

    @app.get("/api/options/desk")
    async def options_desk(symbol: str = "BTCUSD") -> dict:
        return await call("options_desk", symbol=symbol)

    @app.post("/api/options/order", dependencies=[Depends(protected)])
    async def place_options_order(body: OptionsOrderRequest) -> dict:
        import uuid
        req_id = body.request_id or f"opt-{uuid.uuid4().hex[:12]}"
        return await call("options_order", broker=body.broker, strategy=body.strategy, legs=[l.model_dump() for l in body.legs], request_id=req_id)

    # -- read ---------------------------------------------------------------

    @app.get("/api/header")
    async def header() -> dict:
        return await call("header")

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

    @app.get("/api/bars")
    async def bars(symbol: str = "BTCUSD", interval: str = "15m", days: float = 7, limit: int = 5000) -> dict:
        return await call("bars", symbol=symbol, interval=interval, days=days, limit=limit)

    @app.get("/api/snapshots")
    async def snapshots(symbol: str = "BTCUSD", days: float = 7) -> dict:
        return await call("snapshots", symbol=symbol, days=days)

    @app.get("/api/outlook")
    async def outlook_route(symbol: str = "BTCUSD") -> dict:
        return await call("outlook", symbol=symbol)

    @app.get("/api/market-structure")
    async def market_structure(symbol: str = "BTCUSD") -> dict:
        return await call("market_structure", symbol=symbol)

    @app.get("/api/pulse")
    async def pulse_route(symbol: str = "BTCUSD") -> dict:
        return await call("pulse", symbol=symbol)

    @app.get("/api/ai-report")
    async def ai_report(symbol: str = "BTCUSD") -> dict:
        return await call("ai_report", symbol=symbol)

    @app.get("/api/verdicts")
    async def verdicts(symbol: str = "BTCUSD", days: float = 30) -> dict:
        return await call("verdicts", symbol=symbol, days=days)

    @app.get("/api/database")
    async def database() -> dict:
        return await call("database")

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
        # The same gate as every route. The middleware never sees this handshake — it is not an
        # HTTP route — so it is checked here, before accept(): the stream carries PositionClosed
        # with entry, exit and PnL, and an unauthenticated viewer must not see a single frame.
        if not is_authenticated(socket):
            await socket.close(code=1008)  # policy violation
            return
        # Push-only: the server never reads what a client sends, and events carry no secret
        # (SettingsChanged names fields only).
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
