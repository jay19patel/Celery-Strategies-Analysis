"""TradeBuddy v4 — FastAPI Application Entry Point.

Clean architecture: API -> Application (Services) -> Domain -> Infrastructure.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import get_app_settings
from app.api.routers import (
    auth_router,
    journal_router,
    market_router,
    paper_router,
    settings_router,
    system_router,
    trading_router,
)
from app.api.websocket import router as ws_router
from app.api.websocket import start_broadcaster, stop_broadcaster
from app.domain.exceptions import (
    AppError,
    AuthenticationError,
    AuthorisationError,
    NotFoundError,
    SettingsError,
    TradeRefusedError,
    ValidationError,
)
from app.infrastructure.database.session import init_db
from app.infrastructure.redis.client import close_redis, init_redis

# Structured logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tradebuddy.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifecycle management — database, Redis connection, and WebSocket broadcaster."""
    settings = get_app_settings()

    # 1. Initialize SQLite database & SQLModel tables
    db_url = f"sqlite:///{settings.db_path}" if not settings.db_path.startswith("sqlite") else settings.db_path
    init_db(db_url)

    # 2. Initialize Redis connection pool
    try:
        await init_redis(settings.redis_url)
        # 3. Start WebSocket broadcaster relay
        await start_broadcaster()
    except Exception as exc:
        logger.warning("redis_connection_warning: %s. Real-time PubSub will be disabled.", exc)

    logger.info("tradebuddy_backend_started app=%s port=%d", settings.app_name, settings.port)

    yield

    # Shutdown sequence
    await stop_broadcaster()
    await close_redis()
    logger.info("tradebuddy_backend_shutdown_complete")


app = FastAPI(
    title="TradeBuddy v4 API",
    description="Algorithmic Trading & Analytics Engine API with WebSocket Streaming",
    version="4.0.0",
    lifespan=lifespan,
)

# CORS Middleware for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Custom Domain Exception Handlers
# ---------------------------------------------------------------------------

@app.exception_handler(NotFoundError)
async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"error": str(exc), "code": exc.code})


@app.exception_handler(ValidationError)
async def validation_error_handler(_: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"error": str(exc), "code": exc.code})


@app.exception_handler(AuthenticationError)
async def auth_error_handler(_: Request, exc: AuthenticationError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"error": str(exc), "code": exc.code})


@app.exception_handler(AuthorisationError)
async def forbidden_handler(_: Request, exc: AuthorisationError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"error": str(exc), "code": exc.code})


@app.exception_handler(TradeRefusedError)
async def trade_refused_handler(_: Request, exc: TradeRefusedError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": str(exc), "code": exc.code})


@app.exception_handler(SettingsError)
async def settings_error_handler(_: Request, exc: SettingsError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": str(exc), "code": exc.code})


@app.exception_handler(AppError)
async def generic_app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": str(exc), "code": exc.code})


# ---------------------------------------------------------------------------
# Router Registration
# ---------------------------------------------------------------------------

app.include_router(auth_router, prefix="/api")
app.include_router(trading_router, prefix="/api")
app.include_router(market_router, prefix="/api")
app.include_router(paper_router, prefix="/api")
app.include_router(journal_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(system_router, prefix="/api")
app.include_router(ws_router)
