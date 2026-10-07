"""Dashboard: server-rendered pages (Jinja2) + JSON API + one WebSocket for live events.

Routes are thin: each one calls a method on `Api` — the engine itself in
single-process mode, or the engine over ZeroMQ RPC in distributed mode.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

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


class AITest(BaseModel):
    model: str = Field(default="", max_length=64)


class PositionControl(BaseModel):
    trailing: bool | None = None
    max_steps: int | None = Field(default=None, ge=0, le=50)


class EmailReportRequest(BaseModel):
    date: str = Field(default="", max_length=32)


def asset_version() -> str:
    digest = hashlib.sha256()
    for path in sorted((HERE / "static").rglob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_app(api: Api | RemoteApi, live: Broadcaster, lifespan: Lifespan | None = None, api_token: str = "") -> FastAPI:
    app = FastAPI(title="TradeBuddy", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    # Static URLs carry a hash of the files, so a browser never runs an old app.js against new pages.
    templates.env.globals["v"] = asset_version()

    def protected(x_api_token: str = Header(default="")) -> None:
        if api_token and not hmac.compare_digest(x_api_token, api_token):
            raise HTTPException(401, "missing or wrong API token")

    async def call(method: str, **params: Any) -> Any:
        try:
            return await getattr(api, method)(**params)
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
                    "pages": [p for p in PAGES if (p[0] != "paper" or ctx["paper_active"]) and (p[0] != "ai" or ctx.get("ai_enabled"))],
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

    return create_app(Api(system), system.live, lifespan, system.cfg.api_token)
