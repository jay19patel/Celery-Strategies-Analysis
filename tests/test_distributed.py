"""Distributed mode: codec, ZeroMQ transport, Celery evaluation, engine over RPC."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from tradebuddy import codec
from tradebuddy.api import Api, ApiError, RemoteApi
from tradebuddy.delta import Candle
from tradebuddy.events import CandleClosed, FeedHeartbeat, SettingsChanged, StrategyEvaluated, Tick
from tradebuddy.system import RemoteFeed, System
from tradebuddy.transport import Publisher, ResultPuller, ResultPusher, RpcClient, RpcError, RpcServer, subscribe
from tradebuddy.worker import TASK, CeleryEvaluator, run_job

from .conftest import IdleStream, bars
from .test_pipeline import AlwaysBuy, NeedsHistory

BAR = (int(time.time()) // 60 - 1) * 60
REGISTRY = {s.name: s for s in (AlwaysBuy(), NeedsHistory())}
ANY_PORT = "tcp://127.0.0.1:*"


def test_codec_round_trips_every_event_shape():
    for event in [
        Tick(symbol="BTCUSD", price=1.5),
        CandleClosed(symbol="BTCUSD", resolution="1m", bar_time=BAR, source="websocket", candle=Candle(BAR, 1, 2, 0.5, 1.5, 3)),
        SettingsChanged(changed=["delta_env"], trading_stopped=True),
        StrategyEvaluated(strategy="s", version=2, symbol="X", resolution="5m", bar_time=BAR, side="buy", reason="r", worker="w:1"),
        FeedHeartbeat(status={"connected": True}, process={"role": "feed"}),
    ]:
        assert codec.decode(codec.encode(event)) == event


def test_codec_refuses_unknown_types():
    with pytest.raises(ValueError):
        codec.decode(b'{"type": "Nope"}')


# -- ZeroMQ -----------------------------------------------------------------------


async def test_pub_sub_carries_events_filtered_by_topic():
    pub = Publisher(ANY_PORT)
    received: list[Any] = []

    async def listen():
        async for e in subscribe(pub.endpoint, ("SettingsChanged",)):
            received.append(e)
            return

    task = asyncio.create_task(listen())
    for _ in range(50):  # PUB drops messages until the subscriber has joined
        await pub.on_event(Tick(symbol="BTCUSD", price=1))
        await pub.on_event(SettingsChanged(changed=["x"], trading_stopped=False))
        if task.done():
            break
        await asyncio.sleep(0.02)
    await asyncio.wait_for(task, 2)
    assert [type(e).__name__ for e in received] == ["SettingsChanged"]
    pub.close()


async def test_worker_results_reach_the_engine():
    puller = ResultPuller(ANY_PORT)
    ResultPusher(puller.endpoint).send(StrategyEvaluated(strategy="s", version=1, symbol="X", resolution="1m", bar_time=BAR))
    got = await asyncio.wait_for(anext(puller.__aiter__()), 2)
    assert isinstance(got, StrategyEvaluated) and got.strategy == "s"
    puller.close()


async def test_rpc_round_trip_and_errors():
    async def handler(method: str, params: dict) -> Any:
        if method == "boom":
            raise ApiError(400, "bad input")
        await asyncio.sleep(0.01 if params.get("slow") else 0)
        return {"method": method, **params}

    server = RpcServer(ANY_PORT, handler)
    task = asyncio.create_task(server.run())
    client = RpcClient(server.endpoint, timeout=2)
    slow, fast = await asyncio.gather(client.call("a", slow=True), client.call("b"))  # concurrent on one socket
    assert slow == {"method": "a", "slow": True} and fast == {"method": "b"}
    with pytest.raises(RpcError) as info:
        await client.call("boom")
    assert info.value.status == 400 and info.value.detail == "bad input"
    await client.close()
    task.cancel()


async def test_rpc_client_times_out_when_engine_is_down():
    client = RpcClient("tcp://127.0.0.1:59999", timeout=0.2)
    with pytest.raises(Exception, match="did not answer"):
        await client.call("header")
    await client.close()


# -- engine with Celery evaluation ---------------------------------------------------


class FakeCelery:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.conf = type("Conf", (), {"broker_url": "redis://fake"})()

    def send_task(self, name, args, queue, expires):
        self.sent.append({"name": name, "job": args[0], "queue": queue, "expires": expires})


@pytest.fixture
async def engine(cfg, exchange):
    celery = FakeCelery()
    system = System(
        cfg, strategies=[AlwaysBuy(), NeedsHistory()], stream_factory=IdleStream, client_factory=exchange.client,
        evaluator=lambda _s: CeleryEvaluator(celery), local_feed=False, role="engine",
    )
    system.bus.start()
    system.bus.publish(Tick(symbol="BTCUSD", price=100.0))
    await system.bus.drain()
    yield system, celery
    await system.bus.stop()


async def test_candle_close_dispatches_one_task_per_due_strategy(engine):
    system, celery = engine
    system.bus.publish(CandleClosed(symbol="BTCUSD", resolution="1m", bar_time=BAR, source="clock"))
    await system.drain()
    jobs = {t["job"]["strategy"]: t for t in celery.sent}
    assert set(jobs) == {"always_buy", "needs_history"}
    task = jobs["always_buy"]
    assert (task["name"], task["queue"], task["expires"]) == (TASK, "strategies", 60)
    assert task["job"]["prices"] == {"BTCUSD": 100.0} and task["job"]["data_env"] == "demo"
    assert system.runner.stats["always_buy"].in_flight == 1


async def test_worker_result_becomes_a_trade(engine, exchange):
    system, celery = engine
    system.bus.publish(CandleClosed(symbol="BTCUSD", resolution="1m", bar_time=BAR, source="clock"))
    await system.drain()
    job = next(t["job"] for t in celery.sent if t["job"]["strategy"] == "always_buy")
    system.bus.publish(await run_job(job, exchange.client, REGISTRY))  # what the worker would push back
    await system.drain()
    [pos] = await system.paper.positions()
    assert pos.strategy == "always_buy"
    assert system.runner.stats["always_buy"].last_worker.count(":") == 1  # host:pid


async def test_duplicate_worker_result_trades_once(engine, exchange):
    system, celery = engine
    system.bus.publish(CandleClosed(symbol="BTCUSD", resolution="1m", bar_time=BAR, source="clock"))
    await system.drain()
    job = next(t["job"] for t in celery.sent if t["job"]["strategy"] == "always_buy")
    result = await run_job(job, exchange.client, REGISTRY)
    system.bus.publish(result)
    system.bus.publish(result)  # Celery redelivered it
    await system.drain()
    assert len(system.store.recent_orders(broker="paper")) == 1


async def test_stale_result_is_dropped(engine):
    system, _ = engine
    old = BAR - 5 * 60
    system.bus.publish(StrategyEvaluated(strategy="always_buy", version=1, symbol="BTCUSD", resolution="1m", bar_time=old, side="buy", reason="late"))
    await system.drain()
    assert await system.paper.positions() == []
    assert "stale result dropped" in system.store.recent_events(5, types=["StrategyError"])[0]["error"]


async def test_broker_outage_is_a_strategy_error(engine):
    system, celery = engine

    def down(*_a, **_k):
        raise ConnectionError("redis down")

    celery.send_task = down
    system.bus.publish(CandleClosed(symbol="BTCUSD", resolution="1m", bar_time=BAR, source="clock"))
    await system.drain()
    errors = system.store.recent_events(10, types=["StrategyError"])
    assert errors and all("not dispatched" in e["error"] for e in errors)
    assert system.runner.stats["always_buy"].in_flight == 0


async def test_worker_job_fetches_its_own_history(exchange):
    job = {"strategy": "needs_history", "version": 1, "symbol": "BTCUSD", "resolution": "1m", "bar_time": BAR, "candle": None, "data_env": "demo", "prices": {}}
    exchange.history = bars([1, 2, 3, 4], start=BAR - 3 * 60)
    NeedsHistory.seen.clear()
    assert (await run_job(job, exchange.client, REGISTRY)).error == ""
    assert NeedsHistory.seen == [[BAR - 120, BAR - 60, BAR]]
    assert exchange.clients[-1].closed  # the per-job client is closed

    exchange.history = bars([1, 2, 3], start=BAR - 4 * 60)  # ends a bar early
    assert (await run_job(job, exchange.client, REGISTRY)).error == "the bar that just closed is not in the history yet"


async def test_worker_refuses_a_version_it_does_not_have(exchange):
    job = {"strategy": "random_1m", "version": 99, "symbol": "BTCUSD", "resolution": "1m", "bar_time": BAR, "data_env": "demo"}
    assert "version mismatch" in (await run_job(job, client_factory=exchange.client)).error


async def test_engine_answers_the_dashboard_over_rpc(engine):
    system, _ = engine
    server = RpcServer(ANY_PORT, Api(system).dispatch)
    task = asyncio.create_task(server.run())
    api = RemoteApi(RpcClient(server.endpoint, timeout=2), local_metrics=lambda: {"role": "web"})
    assert [b["name"] for b in (await api.header())["brokers"]] == ["paper"]
    assert (await api.toggle(key="trading:paper", enabled=False))["enabled"] is False
    with pytest.raises(ApiError) as info:
        await api.toggle(key="nope", enabled=True)
    assert info.value.status == 400
    with pytest.raises(RpcError) as info:
        await api.client.call("__init__")  # only whitelisted methods
    assert info.value.status == 404
    roles = [p["role"] for p in (await api.metrics())["processes"]]
    assert roles == ["engine", "web"]
    await api.client.close()
    task.cancel()


async def test_remote_feed_goes_stale_without_heartbeats():
    feed = RemoteFeed()
    await feed.on_heartbeat(FeedHeartbeat(status={"connected": True, "url": "wss://x"}, process={"role": "feed"}))
    assert feed.status()["connected"] is True
    feed.updated_at -= 60
    assert feed.status()["connected"] is False and "no heartbeat" in feed.status()["last_error"]
