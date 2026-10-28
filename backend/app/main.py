"""FastAPI application: the engine's HTTP API and live WebSocket for the Next.js dashboard.

Private by default: every /api route and /ws needs the session cookie the PIN buys, unless it is in
PUBLIC_PATHS — so a route added later is private until someone lists it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import ai, auth, auto, desk, market, portfolio, settings, strategies, system, trading, ws, zerohero
from app.config import Config, get_config
from app.core import security
from app.core.db import init_db
from app.engine import Engine
from app.errors import AppError

PUBLIC_PATHS = {"/api/auth/login", "/api/auth/session", "/api/health"}


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or get_config()
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_db(cfg.db_path)
        engine = Engine(cfg)
        app.state.engine = engine
        await engine.start()
        if security.using_default_pin():
            logging.getLogger("tradebuddy").warning("the dashboard PIN is the published default: change it on the Settings page")
        yield
        await engine.stop()

    app = FastAPI(title="TradeBuddy API", version="5.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=cfg.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )

    @app.middleware("http")
    async def private_by_default(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        guarded = path.startswith("/api") and path not in PUBLIC_PATHS and request.method != "OPTIONS"
        if guarded and not security.valid_session(request.cookies.get(security.COOKIE)):
            return JSONResponse({"error": "locked: unlock the dashboard with your PIN"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    for module in (auth, market, desk, strategies, trading, portfolio, auto, zerohero, ai, settings, system):
        app.include_router(module.router, prefix="/api")
    app.include_router(ws.router)

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse({"status": "online", "app": "TradeBuddy Pro Backend", "version": "5.0.0", "docs": "/docs"})

    return app


app = create_app()
