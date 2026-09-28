"""ZeroMQ between processes.

    PUB/SUB     events, fire-and-forget (ticks, candles, everything the dashboard shows)
    PUSH/PULL   strategy results from Celery workers to the engine
    ROUTER/DEALER  request/reply commands from the web process to the engine

None of this is durable: a process that is down misses messages. That is
fine for market data and notifications. Orders never depend on it — they
are decided, sent and recorded inside the engine, in SQLite.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import zmq
import zmq.asyncio

from tradebuddy import codec
from tradebuddy.events import Event

log = logging.getLogger(__name__)

_ctx: zmq.asyncio.Context | None = None


def context() -> zmq.asyncio.Context:
    global _ctx
    if _ctx is None:
        _ctx = zmq.asyncio.Context.instance()
    return _ctx


# -- pub / sub ----------------------------------------------------------------


class Publisher:
    """PUB socket. The topic is the event type, so subscribers can filter at the socket."""

    def __init__(self, bind: str) -> None:
        self.sock = context().socket(zmq.PUB)
        self.sock.setsockopt(zmq.SNDHWM, 10_000)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.bind(bind)
        self.endpoint = self.sock.getsockopt_string(zmq.LAST_ENDPOINT)
        self.sent = 0

    async def on_event(self, event: Event) -> None:
        await self.sock.send_multipart([type(event).__name__.encode(), codec.encode(event)])
        self.sent += 1

    def close(self) -> None:
        self.sock.close()


async def subscribe(url: str, topics: tuple[str, ...] = ("",)) -> AsyncIterator[Event]:
    sock = context().socket(zmq.SUB)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVHWM, 10_000)
    for topic in topics:
        sock.setsockopt(zmq.SUBSCRIBE, topic.encode())
    sock.connect(url)
    try:
        while True:
            _topic, raw = await sock.recv_multipart()
            try:
                yield codec.decode(raw)
            except (ValueError, TypeError) as exc:
                log.warning("zmq_bad_event url=%s error=%s", url, exc)
    finally:
        sock.close()


# -- push / pull (Celery workers -> engine) ------------------------------------


class ResultPuller:
    def __init__(self, bind: str) -> None:
        self.sock = context().socket(zmq.PULL)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.bind(bind)
        self.endpoint = self.sock.getsockopt_string(zmq.LAST_ENDPOINT)

    async def __aiter__(self) -> AsyncIterator[Event]:
        while True:
            raw = await self.sock.recv()
            try:
                yield codec.decode(raw)
            except (ValueError, TypeError) as exc:
                log.warning("zmq_bad_result error=%s", exc)

    def close(self) -> None:
        self.sock.close()


class ResultPusher:
    """Synchronous PUSH for Celery's prefork workers: one socket per process, made after the fork."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._pid = 0
        self._sock: zmq.Socket | None = None

    def send(self, event: Event) -> None:
        if self._sock is None or self._pid != os.getpid():
            self._sock = zmq.Context.instance().socket(zmq.PUSH)
            self._sock.setsockopt(zmq.LINGER, 2000)
            self._sock.setsockopt(zmq.SNDHWM, 10_000)
            self._sock.connect(self.url)
            self._pid = os.getpid()
        self._sock.send(codec.encode(event))


# -- request / reply (web -> engine) -------------------------------------------

Handler = Callable[[str, dict[str, Any]], Awaitable[Any]]


class RpcError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class RpcServer:
    """ROUTER: requests are handled concurrently and answered to whoever asked."""

    def __init__(self, bind: str, handler: Handler) -> None:
        self.sock = context().socket(zmq.ROUTER)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.bind(bind)
        self.endpoint = self.sock.getsockopt_string(zmq.LAST_ENDPOINT)
        self.handler = handler
        self.handled = 0
        self._tasks: set[asyncio.Task] = set()

    async def run(self) -> None:
        try:
            while True:
                peer, raw = await self.sock.recv_multipart()
                task = asyncio.create_task(self._answer(peer, raw))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        finally:
            self.sock.close()

    async def _answer(self, peer: bytes, raw: bytes) -> None:
        request: dict[str, Any] = {}
        try:
            request = json.loads(raw)
            reply = {"id": request["id"], "ok": True, "result": await self.handler(request["method"], request.get("params") or {})}
        except RpcError as exc:
            reply = {"id": request.get("id"), "ok": False, "status": exc.status, "detail": exc.detail}
        except Exception as exc:
            log.exception("rpc_failed method=%s", request.get("method"))
            reply = {"id": request.get("id"), "ok": False, "status": 500, "detail": f"engine error: {exc!r}"}
        self.handled += 1
        await self.sock.send_multipart([peer, json.dumps(reply, default=str).encode()])


class RpcClient:
    """DEALER: many requests in flight on one socket, matched to replies by id."""

    def __init__(self, url: str, timeout: float = 15.0) -> None:
        self.url = url
        self.timeout = timeout
        self.sock = context().socket(zmq.DEALER)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.connect(url)
        self._waiting: dict[str, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None

    async def call(self, method: str, **params: Any) -> Any:
        if self._reader is None or self._reader.done():
            self._reader = asyncio.create_task(self._read())
        rid = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._waiting[rid] = future
        await self.sock.send(json.dumps({"id": rid, "method": method, "params": params}, default=str).encode())
        try:
            reply = await asyncio.wait_for(future, self.timeout)
        except TimeoutError as exc:
            raise RpcError(503, f"engine did not answer {method} within {self.timeout:g}s — is it running?") from exc
        finally:
            self._waiting.pop(rid, None)
        if not reply["ok"]:
            raise RpcError(reply.get("status", 500), reply.get("detail", "engine error"))
        return reply["result"]

    async def _read(self) -> None:
        while True:
            reply = json.loads(await self.sock.recv())
            future = self._waiting.get(reply.get("id"))
            if future and not future.done():
                future.set_result(reply)

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        self.sock.close()
