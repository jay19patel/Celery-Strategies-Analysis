"""Dashboard: server-rendered pages (Jinja2) + JSON API + one WebSocket for live events.

Routes stay thin: every one calls System or a broker.
"""

from __future__ import annotations

import hmac
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from tradebuddy.brokers import Broker
from tradebuddy.errors import BrokerError
from tradebuddy.settings import LIVE_CONFIRM_PHRASE, SettingsError
from tradebuddy.system import System

HERE = Path(__file__).parent

# (id, path, lucide icon, title, nav group)
PAGES = [
    ("overview", "/", "layout-dashboard", "Overview", "Trading"),
    ("strategies", "/strategies", "brain-circuit", "Strategies", "Trading"),
    ("signals", "/signals", "activity", "Signals", "Trading"),
    ("positions", "/positions", "layers", "Positions", "Portfolio"),
    ("orders", "/orders", "list-checks", "Orders", "Portfolio"),
    ("account", "/account", "wallet", "Account", "Portfolio"),
    ("paper", "/paper", "flask-conical", "Paper Trading", "Portfolio"),
    ("market", "/market", "candlestick-chart", "Market Data", "Monitoring"),
    ("system", "/system", "gauge", "System", "Monitoring"),
    ("events", "/events", "scroll-text", "Event Log", "Monitoring"),
    ("settings", "/settings", "settings", "Settings", "Admin"),
]

SIGNAL_EVENTS = ["SignalGenerated", "TradeSkipped", "StrategyError"]


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
    take_profit: float


def create_app(system: System) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await system.start()
        yield
        await system.stop()

    app = FastAPI(title="TradeBuddy", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")

    def protected(x_api_token: str = Header(default="")) -> None:
        token = system.cfg.api_token
        if token and not hmac.compare_digest(x_api_token, token):
            raise HTTPException(401, "missing or wrong API token")

    def broker_for(name: str | None) -> Broker:
        if name is None:
            return system.broker
        if name not in system.brokers:
            raise HTTPException(404, f"unknown broker {name!r}")
        return system.brokers[name]

    async def call(coro):
        try:
            return await coro
        except BrokerError as exc:
            raise HTTPException(502, str(exc)) from exc

    # -- pages --------------------------------------------------------------

    def page(page_id: str, path: str, title: str):
        async def render(request: Request) -> HTMLResponse:
            return templates.TemplateResponse(
                request,
                f"{page_id}.html",
                {"page": page_id, "title": title, "pages": PAGES, "live_phrase": LIVE_CONFIRM_PHRASE},
            )

        app.add_api_route(path, render, methods=["GET"], response_class=HTMLResponse, include_in_schema=False, name=f"page_{page_id}")

    for page_id, path, _icon, title, _group in PAGES:
        page(page_id, path, title)

    # -- read ---------------------------------------------------------------

    @app.get("/api/header")
    async def header() -> dict:
        return system.header()

    @app.get("/api/overview")
    async def overview() -> dict:
        day = time.time() - 86_400
        try:
            account = (await system.broker.account()).to_dict()
            account_error = ""
        except BrokerError as exc:
            account, account_error = None, str(exc)
        return {
            "header": system.header(),
            "account": account,
            "account_error": account_error,
            "counts_24h": {t: system.store.count_events_since(t, day) for t in ("SignalGenerated", "TradeSkipped", "OrderPlaced", "OrderFailed", "PositionClosed")},
            "strategies": system.strategies_view(),
            "recent": system.store.recent_events(15, types=[*SIGNAL_EVENTS, "OrderPlaced", "OrderFailed", "OrderUnknown", "PositionClosed"]),
            "paper": system.paper.stats(),
        }

    @app.get("/api/strategies")
    async def strategies() -> list[dict]:
        return system.strategies_view()

    @app.get("/api/signals")
    async def signals(limit: int = 300, strategy: str = "", symbol: str = "") -> list[dict]:
        rows = system.store.recent_events(min(limit, 2000), types=SIGNAL_EVENTS)
        return [r for r in rows if (not strategy or r.get("strategy") == strategy) and (not symbol or r.get("symbol") == symbol)]

    @app.get("/api/positions")
    async def positions(broker: str | None = None) -> list[dict]:
        return [p.to_dict() for p in await call(broker_for(broker).positions())]

    @app.get("/api/orders")
    async def orders(broker: str | None = None, limit: int = 300) -> list[dict]:
        return system.store.recent_orders(min(limit, 2000), broker=broker)

    @app.get("/api/open-orders")
    async def open_orders(broker: str | None = None) -> list[dict]:
        return await call(broker_for(broker).open_orders())

    @app.get("/api/account")
    async def account(broker: str | None = None) -> dict:
        return (await call(broker_for(broker).account())).to_dict()

    @app.get("/api/paper/stats")
    async def paper_stats() -> dict:
        return system.paper.stats()

    @app.get("/api/paper/trades")
    async def paper_trades(limit: int = 300) -> list[dict]:
        return system.paper.trades(min(limit, 5000))

    @app.get("/api/metrics")
    async def metrics() -> dict:
        return system.metrics()

    @app.get("/api/events")
    async def events(limit: int = 300, type: str = "") -> list[dict]:
        return system.store.recent_events(min(limit, 2000), types=[type] if type else None)

    def settings_view() -> dict:
        return system.settings.public() | {"token_required": bool(system.cfg.api_token)}

    @app.get("/api/settings")
    async def get_settings() -> dict:
        return settings_view()

    # -- write --------------------------------------------------------------

    @app.put("/api/settings", dependencies=[Depends(protected)])
    async def put_settings(body: SettingsUpdate) -> dict:
        try:
            await system.update_settings(body.changes, body.confirm)
        except SettingsError as exc:
            raise HTTPException(400, str(exc)) from exc
        return settings_view()

    @app.post("/api/settings/clear-credentials", dependencies=[Depends(protected)])
    async def clear_credentials() -> dict:
        await system.clear_credentials()
        return settings_view()

    @app.post("/api/settings/test-delta", dependencies=[Depends(protected)])
    async def test_delta(body: DeltaTest) -> dict:
        return await system.test_delta(body.env, body.api_key.strip(), body.api_secret.strip())

    @app.post("/api/toggles", dependencies=[Depends(protected)])
    async def toggle(body: Toggle) -> dict:
        try:
            system.set_toggle(body.key, body.enabled)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"key": body.key, "enabled": body.enabled}

    @app.post("/api/close-all", dependencies=[Depends(protected)])
    async def close_all() -> dict:
        return await call(system.close_all())

    @app.post("/api/positions/{broker}/{symbol}/close", dependencies=[Depends(protected)])
    async def close_position(broker: str, symbol: str) -> dict:
        return await call(broker_for(broker).close_position(symbol))

    @app.post("/api/paper/positions/{symbol}/protection", dependencies=[Depends(protected)])
    async def protection(symbol: str, body: Protection) -> dict:
        try:
            system.paper.update_protection(symbol, body.stop_loss, body.take_profit)
        except BrokerError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"symbol": symbol, "stop_loss": body.stop_loss, "take_profit": body.take_profit}

    @app.post("/api/paper/reset", dependencies=[Depends(protected)])
    async def paper_reset() -> dict:
        system.paper.reset()
        return system.paper.stats()

    # -- live ---------------------------------------------------------------

    @app.websocket("/ws")
    async def live(ws: WebSocket) -> None:
        await ws.accept()
        system.live.clients.add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            system.live.clients.discard(ws)

    return app
