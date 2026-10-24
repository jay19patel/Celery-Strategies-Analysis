"""The live stream: every event the engine publishes, pushed to the dashboard.

Checked here, before accept(): HTTP middleware never sees a WebSocket handshake.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import security

router = APIRouter()


@router.websocket("/ws")
async def stream(ws: WebSocket) -> None:
    if not security.valid_session(ws.cookies.get(security.COOKIE)):
        await ws.close(code=4401)
        return
    await ws.accept()
    hub = ws.app.state.engine.hub
    queue = hub.connect()

    async def drain_incoming() -> None:  # only to notice the browser leaving
        with contextlib.suppress(WebSocketDisconnect):
            while True:
                await ws.receive_text()

    reader = asyncio.create_task(drain_incoming())
    try:
        await ws.send_json({"type": "Hello", "message": "connected", "level": "info", "data": {}, "ts": 0})
        while not reader.done():
            getter = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                await ws.send_json(getter.result())
            else:
                getter.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader.cancel()
        hub.disconnect(queue)
