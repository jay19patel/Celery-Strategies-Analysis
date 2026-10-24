"""WebSocket manager and endpoint — real-time event streaming to web clients.

Subscribes to Redis PubSub events and broadcasts them to all active client
connections with connection recovery, heartbeat, and graceful teardown.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.infrastructure.redis.client import get_redis
from app.infrastructure.redis.pubsub import EventSubscriber

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Tracks active browser WebSocket connections and broadcasts messages."""

    def __init__(self) -> None:
        self._active_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new client connection."""
        await websocket.accept()
        async with self._lock:
            self._active_connections.add(websocket)
        logger.info("websocket_connected clients=%d", len(self._active_connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a client connection."""
        async with self._lock:
            self._active_connections.discard(websocket)
        logger.info("websocket_disconnected clients=%d", len(self._active_connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast a structured payload to all connected clients."""
        payload = json.dumps(message, default=str)
        async with self._lock:
            connections = list(self._active_connections)

        for connection in connections:
            try:
                await connection.send_text(payload)
            except Exception:
                logger.debug("websocket_send_failed", exc_info=False)
                async with self._lock:
                    self._active_connections.discard(connection)

    @property
    def count(self) -> int:
        """Number of currently connected WebSocket clients."""
        return len(self._active_connections)


manager = ConnectionManager()
_broadcaster_task: asyncio.Task[None] | None = None


async def start_broadcaster() -> None:
    """Start background task that listens to Redis PubSub and relays to WebSocket clients."""
    global _broadcaster_task  # noqa: PLW0603

    async def _relay_loop() -> None:
        while True:
            try:
                redis = get_redis()
                subscriber = EventSubscriber(redis)
                await subscriber.subscribe()
                logger.info("pubsub_broadcaster_started")

                async for message in subscriber.listen():
                    await manager.broadcast(message)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("pubsub_broadcaster_error: %s. Reconnecting in 2s...", exc)
                await asyncio.sleep(2.0)

    _broadcaster_task = asyncio.create_task(_relay_loop())


async def stop_broadcaster() -> None:
    """Stop the Redis PubSub background relay task."""
    global _broadcaster_task  # noqa: PLW0603
    if _broadcaster_task:
        _broadcaster_task.cancel()
        try:
            await _broadcaster_task
        except asyncio.CancelledError:
            pass
        _broadcaster_task = None
        logger.info("pubsub_broadcaster_stopped")


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket stream for real-time market ticks, order updates, and positions."""
    await manager.connect(websocket)

    # Send initial welcome / handshake
    await websocket.send_json({
        "type": "connection_established",
        "data": {"status": "connected", "active_clients": manager.count},
    })

    try:
        while True:
            # Client heartbeat / ping-pong
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "data": {"ts": msg.get("ts")}})
            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception as exc:
        logger.warning("websocket_error: %s", exc)
        await manager.disconnect(websocket)
