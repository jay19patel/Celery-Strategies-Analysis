"""FastAPI Frontend Web Application and Dashboard Entrypoint.

Decoupled presentation layer providing OpenAlgo-style system observability,
real-time Delta Exchange broker management, strategy performance analytics,
and high-frequency WebSocket streaming (/ws/live) for orders and positions.
All business logic is strictly encapsulated within app.services.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.health_monitor import start_health_collector
from app.services.broker_service import get_broker_service
from app.services.strategy_service import get_strategy_service
from app.services.system_service import get_system_service
from frontend.routers import (
    analytics_router,
    broker_router,
    log_router,
    strategy_router,
    system_router,
)

logger = logging.getLogger(__name__)

# Base directory paths
BASE_DIR: Path = Path(__file__).parent.resolve()
STATIC_DIR: Path = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)


class LiveStreamManager:
    """Manages active browser WebSocket subscriptions for real-time order/position streaming."""

    def __init__(self) -> None:
        self.active_sockets: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.active_sockets.append(ws)
        logger.info("🔌 Browser connected to /ws/live (Active clients: %s)", len(self.active_sockets))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self.active_sockets:
                self.active_sockets.remove(ws)
        logger.info("🔌 Browser disconnected from /ws/live (Active clients: %s)", len(self.active_sockets))

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            sockets = list(self.active_sockets)

        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                await self.disconnect(ws)


live_stream_manager = LiveStreamManager()


async def _background_stream_loop() -> None:
    """Periodically broadcast live positions, orders, and telemetry to connected browsers."""
    while True:
        try:
            await asyncio.sleep(1.0)
            if not live_stream_manager.active_sockets:
                continue

            broker_service = get_broker_service()
            system_service = get_system_service()
            strategy_service = get_strategy_service()

            positions, orders, balance, status, metrics, signals, strategies, detailed_strategies = await asyncio.gather(
                asyncio.to_thread(broker_service.get_positions, "LIVE"),
                asyncio.to_thread(broker_service.get_orders, "LIVE"),
                asyncio.to_thread(broker_service.get_balance),
                asyncio.to_thread(broker_service.get_status),
                asyncio.to_thread(system_service.get_metrics),
                asyncio.to_thread(strategy_service.get_signals_log, 25),
                asyncio.to_thread(strategy_service.get_strategies_stats),
                asyncio.to_thread(strategy_service.get_strategies_detailed),
            )
            paper_positions = await asyncio.to_thread(broker_service.get_paper_positions)

            payload = {
                "type": "live_stream",
                "timestamp": time.time(),
                "positions": positions,
                "orders": orders,
                "balance": balance,
                "status": status,
                "metrics": metrics,
                "paper_positions": paper_positions,
                "signals": signals,
                "strategies": strategies,
                "detailed_strategies": detailed_strategies,
            }
            await live_stream_manager.broadcast(payload)
        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001
            logger.debug("Error in WebSocket live stream broadcaster: %s", exc)
            await asyncio.sleep(2.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager initializing background health monitoring and WebSocket streaming."""
    try:
        start_health_collector()
        logger.info("✅ Health monitoring collector initialized on dashboard startup.")
    except Exception as exc:  # noqa: BLE001 - Non-blocking startup for background health monitor
        logger.warning("Could not auto-start health collector: %s", exc)

    stream_task = asyncio.create_task(_background_stream_loop())
    try:
        yield
    finally:
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="TradeBuddy - Trading & System Dashboard API",
    description="Decoupled frontend dashboard API for real-time strategy monitoring, Delta Exchange execution, and observability.",
    version="2.2.0",
    lifespan=lifespan,
)

# Mount static assets
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Include decoupled service-backed routers
app.include_router(system_router)
app.include_router(broker_router)
app.include_router(strategy_router)
app.include_router(analytics_router)
app.include_router(log_router)


@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket) -> None:
    """Real-time bidirectional WebSocket stream for live orders, positions, and telemetry."""
    await live_stream_manager.connect(websocket)
    try:
        broker_service = get_broker_service()
        system_service = get_system_service()
        strategy_service = get_strategy_service()

        snapshot = {
            "type": "snapshot",
            "positions": broker_service.get_positions(mode="LIVE"),
            "orders": broker_service.get_orders(mode="LIVE"),
            "balance": broker_service.get_balance(),
            "status": broker_service.get_status(),
            "metrics": system_service.get_metrics(),
            "paper_positions": broker_service.get_paper_positions(),
            "paper_orders": broker_service.get_paper_orders(),
            "signals": strategy_service.get_signals_log(limit=25),
            "strategies": strategy_service.get_strategies_stats(),
            "detailed_strategies": strategy_service.get_strategies_detailed(),
        }
        await websocket.send_json(snapshot)

        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await live_stream_manager.disconnect(websocket)
    except Exception:  # noqa: BLE001
        await live_stream_manager.disconnect(websocket)


@app.get("/")
def get_dashboard() -> FileResponse:
    """Serve the main OpenAlgo-style dashboard HTML interface."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        index_file = BASE_DIR / "index.html"

    if index_file.exists():
        return FileResponse(str(index_file))

    raise HTTPException(status_code=404, detail="Dashboard index.html not found.")
