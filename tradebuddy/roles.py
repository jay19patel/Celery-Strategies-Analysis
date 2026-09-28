"""Process roles. docker-compose runs one container per role.

    feed    Delta WebSocket and candle closes -> ZeroMQ
    engine  decisions, brokers, SQLite; strategies dispatched to Celery
    web     dashboard; talks to the engine over ZeroMQ
    worker  Celery worker evaluating strategies
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from tradebuddy.api import Api, RemoteApi
from tradebuddy.app import create_app
from tradebuddy.config import Config, require_safe_bind
from tradebuddy.events import Event, EventBus, FeedHeartbeat
from tradebuddy.settings import ENVIRONMENTS, STREAM_FIELDS, from_stored
from tradebuddy.store import Store
from tradebuddy.strategies import discover
from tradebuddy.stream import BarCloser, DeltaStream
from tradebuddy.system import Broadcaster, Monitor, System
from tradebuddy.transport import Publisher, ResultPuller, RpcClient, RpcServer, subscribe

log = logging.getLogger(__name__)


def serve(app: FastAPI, cfg: Config) -> None:
    require_safe_bind(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


# -- feed -----------------------------------------------------------------------


async def pump(source: AsyncIterator[Event], bus: EventBus) -> None:
    async for event in source:
        bus.publish(event)


async def feed(cfg: Config, heartbeat_seconds: float = 3.0) -> None:
    store = Store(cfg.db_path)
    strategies = discover()
    bus = EventBus()
    closer = BarCloser(bus, {(sym, s.interval) for s in strategies for sym in s.symbols})
    publisher = Publisher(cfg.zmq_feed_bind)
    bus.subscribe(publisher.on_event)
    monitor = Monitor("feed")

    def make_stream() -> DeltaStream:
        s = from_stored(store.load_settings())
        private = s.delta_active and s.has_credentials
        return DeltaStream(ENVIRONMENTS[s.data_env][1], bus, closer, s.delta_api_key if private else "", s.delta_api_secret if private else "")

    stream = make_stream()
    stream_task = asyncio.create_task(stream.run(), name="stream")

    async def heartbeat() -> None:
        while True:
            bus.publish(FeedHeartbeat(status=stream.status(), process=monitor.snapshot()))
            await asyncio.sleep(heartbeat_seconds)

    async def follow_settings() -> None:
        nonlocal stream, stream_task
        async for event in subscribe(cfg.zmq_events_url, ("SettingsChanged",)):
            if any(f in STREAM_FIELDS for f in getattr(event, "changed", [])):
                log.info("feed_restarting fields=%s", event.changed)
                stream_task.cancel()
                await asyncio.gather(stream_task, return_exceptions=True)
                stream = make_stream()
                stream_task = asyncio.create_task(stream.run(), name="stream")

    bus.start()
    log.info("feed_started publish=%s pairs=%d", cfg.zmq_feed_bind, len(closer.pairs))
    try:
        await asyncio.gather(closer.run(), monitor.run(), heartbeat(), follow_settings())
    finally:
        stream_task.cancel()
        await bus.stop()
        publisher.close()


# -- engine ---------------------------------------------------------------------


async def engine(cfg: Config) -> None:
    from tradebuddy.worker import CeleryEvaluator  # imports Celery only where it is used

    system = System(cfg, local_feed=False, evaluator=lambda _s: CeleryEvaluator(), role="engine")
    publisher = Publisher(cfg.zmq_events_bind)
    system.bus.subscribe(publisher.on_event)
    results = ResultPuller(cfg.zmq_results_bind)
    rpc = RpcServer(cfg.zmq_rpc_bind, Api(system).dispatch)

    await system.start()
    log.info("engine_started feed=%s events=%s rpc=%s results=%s", cfg.zmq_feed_url, cfg.zmq_events_bind, cfg.zmq_rpc_bind, cfg.zmq_results_bind)
    try:
        await asyncio.gather(rpc.run(), pump(subscribe(cfg.zmq_feed_url), system.bus), pump(results.__aiter__(), system.bus))
    finally:
        await system.stop()
        results.close()
        publisher.close()


# -- web ------------------------------------------------------------------------


def web_app(cfg: Config) -> FastAPI:
    live = Broadcaster()
    monitor = Monitor("web")
    client = RpcClient(cfg.zmq_rpc_url)
    api = RemoteApi(client, local_metrics=monitor.snapshot)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async def relay() -> None:
            async for event in subscribe(cfg.zmq_events_url):
                await live.on_event(event)

        tasks = [asyncio.create_task(relay()), asyncio.create_task(monitor.run())]
        yield
        for t in tasks:
            t.cancel()
        await client.close()

    return create_app(api, live, lifespan, cfg.api_token)


# -- worker ---------------------------------------------------------------------


def run_worker(concurrency: int) -> None:
    # Python 3.13 on macOS starts pool children with spawn, not fork; without this
    # billiard never initialises them and every task fails to unpack its request.
    os.environ.setdefault("FORKED_BY_MULTIPROCESSING", "1")
    from tradebuddy.worker import QUEUE, app

    app.worker_main(["worker", "-Q", QUEUE, "--loglevel", "INFO", "--concurrency", str(concurrency), "--hostname", f"strategies@{socket.gethostname()}"])
