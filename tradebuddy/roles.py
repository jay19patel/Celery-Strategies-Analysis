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
from tradebuddy.delta import DeltaClient
from tradebuddy.events import Event, EventBus, FeedHeartbeat, Tick
from tradebuddy.jobs import Jobs
from tradebuddy.options import UNDERLYINGS, OptionsBook, OptionsFeed
from tradebuddy.settings import ENVIRONMENTS, STREAM_FIELDS, from_stored
from tradebuddy.store import Store
from tradebuddy.strategies import discover
from tradebuddy.stream import BarCloser, DeltaStream, PriceBook
from tradebuddy.system import Broadcaster, Monitor, System
from tradebuddy.transport import Publisher, ResultPuller, ResultPusher, RpcClient, RpcServer, subscribe

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
    # Options: the WebSocket fills the book; OptionsFeed publishes a summary per underlying and
    # falls back to REST when the socket goes quiet. The REST client follows the price source.
    prices = PriceBook()
    bus.subscribe(prices.on_tick, Tick)
    book = OptionsBook(set(UNDERLYINGS.values()))
    rest = DeltaClient(ENVIRONMENTS[from_stored(store.load_settings()).data_env][0])
    options = OptionsFeed(book, bus.publish, lambda u: rest.option_tickers(u), prices.price)
    jobs = Jobs("feed")
    closer.job = jobs.add("bar clock", "Closes bars the stream has not, when their time is up", 1)
    options.job = jobs.add("options snapshot", "Summarises the options book per underlying (REST if the socket is quiet)", options.every)
    beat = jobs.add("heartbeat", "Tells the engine the feed is alive, with its status and these jobs", heartbeat_seconds)

    def make_stream() -> DeltaStream:
        s = from_stored(store.load_settings())
        private = s.delta_active and s.has_credentials
        return DeltaStream(
            ENVIRONMENTS[s.data_env][1], bus, closer, s.delta_api_key if private else "", s.delta_api_secret if private else "", options=book,
        )

    stream = make_stream()
    stream_task = asyncio.create_task(stream.run(), name="stream")

    async def heartbeat() -> None:
        while True:
            with beat.tick():
                status = stream.status() | {"options": options.stats(), "jobs": jobs.snapshot()}
                bus.publish(FeedHeartbeat(status=status, process=monitor.snapshot()))
            await asyncio.sleep(heartbeat_seconds)

    async def follow_settings() -> None:
        nonlocal stream, stream_task
        async for event in subscribe(cfg.zmq_events_url, ("SettingsChanged",)):
            if any(f in STREAM_FIELDS for f in getattr(event, "changed", [])):
                log.info("feed_restarting fields=%s", event.changed)
                stream_task.cancel()
                await asyncio.gather(stream_task, return_exceptions=True)
                nonlocal rest
                book.quotes.clear()  # a different exchange (demo/live) lists different contracts
                book.updated.clear()
                await rest.aclose()
                rest = DeltaClient(ENVIRONMENTS[from_stored(store.load_settings()).data_env][0])
                stream = make_stream()
                stream_task = asyncio.create_task(stream.run(), name="stream")

    bus.start()
    log.info("feed_started publish=%s pairs=%d", cfg.zmq_feed_bind, len(closer.pairs))
    try:
        await asyncio.gather(closer.run(), monitor.run(), heartbeat(), follow_settings(), options.run())
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


# -- analyst --------------------------------------------------------------------


async def analyst(cfg: Config) -> None:
    """Market analysis in its own process: heavy imports (scikit-learn) and slow calls (Mistral)
    stay out of the engine. Reads settings and the engine's events; writes only events, PUSHed to
    the engine like strategy results."""
    from tradebuddy.analyst import Analyst, model_dir_for
    from tradebuddy.events import MarketStats, OptionsSnapshot, ProcessHeartbeat

    store = Store(cfg.db_path)
    options: dict[str, dict] = {}
    stats: dict[str, dict] = {}
    clients: dict[str, DeltaClient] = {}

    def settings():
        return from_stored(store.load_settings())

    def client() -> DeltaClient:
        env = settings().data_env
        if env not in clients:
            clients[env] = DeltaClient(ENVIRONMENTS[env][0])
        return clients[env]

    pusher = ResultPusher(cfg.zmq_results_url)
    engine = RemoteApi(RpcClient(cfg.zmq_rpc_url, timeout=20))  # read-only: the digest TB-AI writes about

    async def digest(include_account: bool) -> dict:
        return await engine.ai_digest(include_account=include_account)

    worker = Analyst(client, settings, pusher.send, options.get, stats.get, model_dir=model_dir_for(cfg.db_path), digest=digest)
    jobs, monitor = Jobs("analyst"), Monitor("analyst")
    worker.job = jobs.add("market analysis", "Insights, forecast, options playbook, AI review", worker.every)
    beat = jobs.add("heartbeat", "Tells the engine the analyst is alive, with these jobs", 10)

    async def heartbeat() -> None:
        while True:
            with beat.tick():
                pusher.send(ProcessHeartbeat(role="analyst", process=monitor.snapshot(), jobs=jobs.snapshot(), status=worker.status()))
            await asyncio.sleep(10)

    async def listen() -> None:
        async for event in subscribe(cfg.zmq_events_url, ("OptionsSnapshot", "MarketStats")):
            if isinstance(event, OptionsSnapshot):
                options[event.symbol] = event.summary
            elif isinstance(event, MarketStats):
                stats[event.symbol] = {k: v for k, v in event.to_dict().items() if k not in ("type", "level", "symbol")}

    log.info("analyst_started results=%s models=%s", cfg.zmq_results_url, model_dir_for(cfg.db_path))
    await asyncio.gather(listen(), worker.run(), heartbeat(), monitor.run())


# -- worker ---------------------------------------------------------------------


def run_worker(concurrency: int) -> None:
    # Python 3.13 on macOS starts pool children with spawn, not fork; without this
    # billiard never initialises them and every task fails to unpack its request.
    os.environ.setdefault("FORKED_BY_MULTIPROCESSING", "1")
    from tradebuddy.worker import QUEUE, app

    app.worker_main(["worker", "-Q", QUEUE, "--loglevel", "INFO", "--concurrency", str(concurrency), "--hostname", f"strategies@{socket.gethostname()}"])
