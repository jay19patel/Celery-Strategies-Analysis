"""FastAPI application: the engine's HTTP API and live WebSocket for the Next.js dashboard.

Private by default: every /api route and /ws needs the session cookie the PIN buys, unless it is in
PUBLIC_PATHS — so a route added later is private until someone lists it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    ai,
    auth,
    auto,
    desk,
    market,
    notifications,
    portfolio,
    settings,
    strategies,
    system,
    trading,
    ws,
)
from app.config import Config, get_config
from app.core import logstore, security
from app.core.db import init_db
from app.engine import Engine
from app.errors import AppError

PUBLIC_PATHS = {"/api/auth/login", "/api/auth/session", "/api/health"}
_RECORD_FIELDS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """A log record as one JSON line: time, level, logger, message, and any `extra=` fields."""

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": round(record.created, 3), "level": record.levelname.lower(), "logger": record.name, "message": record.getMessage(),
        } | {k: v for k, v in vars(record).items() if k not in _RECORD_FIELDS}
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or get_config()
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if cfg.log_json:
        for handler in logging.getLogger().handlers:
            handler.setFormatter(JsonFormatter())
    # httpx logs every request URL at INFO, and the Telegram API carries the bot token in its URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_db(cfg.db_path)
        captured = logstore.install()  # from here on, every warning and error is kept, counted, on the System page
        engine = Engine(cfg)
        app.state.engine = engine
        await engine.start()
        if security.using_default_pin():
            logging.getLogger("app.main").warning("the dashboard PIN is the published default: change it on the Settings page")
        yield
        await engine.stop()
        logstore.uninstall(captured)

    # The API's docs sit under /api, so they are behind the PIN like everything they describe.
    app = FastAPI(title="TradeBuddy API", version="6.0.0", lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
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

    for module in (auth, market, desk, strategies, trading, portfolio, auto, ai, settings, system, notifications):
        app.include_router(module.router, prefix="/api")
    app.include_router(ws.router)

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse({"status": "online", "app": "TradeBuddy Pro Backend", "version": "6.0.0", "docs": "/api/docs"})

    return app


app = create_app()
