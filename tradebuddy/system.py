"""Wires every component to the bus, owns runtime settings, and exposes views for the dashboard."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import Callable
from typing import Any, Protocol

import psutil
from fastapi import WebSocket

from tradebuddy.brokers import Broker, DeltaBroker, PaperBroker
from tradebuddy.config import Config
from tradebuddy.delta import DeltaClient
from tradebuddy.events import (
    CandleClosed,
    Event,
    EventBus,
    OrderRequested,
    OrderUpdate,
    SettingsChanged,
    SignalGenerated,
    Tick,
    ToggleChanged,
)
from tradebuddy.runner import MarketData, StrategyRunner, pair_key, strategy_key
from tradebuddy.settings import (
    ENVIRONMENTS,
    ROUTING_FIELDS,
    Settings,
    apply_changes,
    changed_fields,
    clear_credentials,
    from_stored,
)
from tradebuddy.store import Store
from tradebuddy.strategies import Strategy, discover
from tradebuddy.stream import BarCloser, DeltaStream, PriceBook
from tradebuddy.trading import TRADING_KEY, Executor, Trader

log = logging.getLogger(__name__)


class Stream(Protocol):
    async def run(self) -> None: ...
    def status(self) -> dict[str, Any]: ...


StreamFactory = Callable[..., Stream]
ClientFactory = Callable[..., DeltaClient]


class Broadcaster:
    """Pushes every event to open dashboard WebSockets. Ticks are sent at most once a second per symbol."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self._last_tick: dict[str, float] = {}

    async def on_event(self, event: Event) -> None:
        if not self.clients:
            return
        if isinstance(event, Tick):
            if event.ts - self._last_tick.get(event.symbol, 0) < 1:
                return
            self._last_tick[event.symbol] = event.ts
        message = event.to_dict()
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                self.clients.discard(ws)


class Monitor:
    """Process and event-loop load."""

    def __init__(self) -> None:
        self.started_at = time.time()
        self.loop_lag_ms = 0.0
        self.loop_lag_max_ms = 0.0
        self._proc = psutil.Process(os.getpid())
        self._proc.cpu_percent(None)

    async def run(self, interval: float = 0.5) -> None:
        while True:
            before = time.perf_counter()
            await asyncio.sleep(interval)
            lag = max(0.0, (time.perf_counter() - before - interval) * 1000)
            self.loop_lag_ms = round(lag, 2)
            self.loop_lag_max_ms = round(max(self.loop_lag_max_ms, lag), 2)

    def snapshot(self) -> dict[str, Any]:
        with self._proc.oneshot():
            memory = self._proc.memory_info().rss
            threads = self._proc.num_threads()
            cpu = self._proc.cpu_percent(None)
        return {
            "uptime_seconds": round(time.time() - self.started_at),
            "process_cpu_pct": cpu,
            "process_memory_mb": round(memory / 1_048_576, 1),
            "threads": threads,
            "asyncio_tasks": len(asyncio.all_tasks()),
            "loop_lag_ms": self.loop_lag_ms,
            "loop_lag_max_ms": self.loop_lag_max_ms,
            "system_cpu_pct": psutil.cpu_percent(None),
            "system_memory_pct": psutil.virtual_memory().percent,
            "load_avg": [round(x, 2) for x in os.getloadavg()],
        }


class System:
    def __init__(
        self,
        cfg: Config,
        strategies: list[Strategy] | None = None,
        stream_factory: StreamFactory | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.cfg = cfg
        self.bus = EventBus()
        self.store = Store(cfg.db_path)
        self.settings: Settings = from_stored(self.store.load_settings())
        self.strategies = discover() if strategies is None else strategies
        self.prices = PriceBook()
        self.monitor = Monitor()
        self.live = Broadcaster()

        self._client_factory = client_factory or DeltaClient
        self._stream_factory = stream_factory or DeltaStream
        self.market_client, self.delta_client = self._make_clients()

        pairs = {(sym, s.interval) for s in self.strategies for sym in s.symbols}
        self.closer = BarCloser(self.bus, pairs)
        self.stream: Stream = self._make_stream()

        self.paper = PaperBroker(self.store, self.bus, self.prices, specs=lambda sym: self.market_client.product(sym), settings=lambda: self.settings)
        self.delta = DeltaBroker(self.delta_client)
        self.brokers: dict[str, Broker] = {"paper": self.paper, "delta": self.delta}

        self.market = MarketData(self.market_client, self.prices)
        self.runner = StrategyRunner(self.bus, self.store, self.market, self.strategies)
        self.trader = Trader(self.bus, self.store, self.brokers, self.prices, lambda: self.settings)
        self.executor = Executor(self.bus, self.store, self.brokers)

        self.bus.subscribe(self.prices.on_tick, Tick)
        self.bus.subscribe(self.paper.on_tick, Tick)
        self.bus.subscribe(self.runner.on_candle_closed, CandleClosed)
        self.bus.subscribe(self.trader.on_signal, SignalGenerated)
        self.bus.subscribe(self.executor.on_order_requested, OrderRequested)
        self.bus.subscribe(self.executor.on_order_update, OrderUpdate)
        self.bus.subscribe(self.record)
        self.bus.subscribe(self.live.on_event)

        # Fail closed: every start begins with trading off, whatever it was before.
        self.store.set_enabled(TRADING_KEY, False)
        self._started = False
        self._stream_task: asyncio.Task | None = None
        self._tasks: list[asyncio.Task] = []

    @property
    def broker(self) -> Broker:
        return self.brokers[self.settings.broker]

    def _make_clients(self) -> tuple[DeltaClient, DeltaClient]:
        s = self.settings
        market = self._client_factory(ENVIRONMENTS[s.data_env][0])
        delta = self._client_factory(ENVIRONMENTS[s.delta_env][0], s.delta_api_key, s.delta_api_secret)
        return market, delta

    def _make_stream(self) -> Stream:
        s = self.settings
        private = s.broker == "delta" and s.has_credentials  # orders/positions channels only for the live broker
        return self._stream_factory(
            ENVIRONMENTS[s.data_env][1], self.bus, self.closer,
            s.delta_api_key if private else "", s.delta_api_secret if private else "",
        )

    async def record(self, event: Event) -> None:
        if not isinstance(event, Tick):
            self.store.record_event(event.to_dict())

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        self.bus.start()
        self._started = True
        self._stream_task = asyncio.create_task(self.stream.run(), name="stream")
        self._tasks = [
            asyncio.create_task(self.closer.run(), name="bar-clock"),
            asyncio.create_task(self.monitor.run(), name="monitor"),
        ]
        log.info("system_started broker=%s data=%s strategies=%s", self.settings.broker, self.settings.data_env, [s.name for s in self.strategies])

    async def stop(self) -> None:
        tasks = [t for t in (self._stream_task, *self._tasks) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.bus.stop()
        await self.market_client.aclose()
        await self.delta_client.aclose()

    async def _restart_stream(self) -> None:
        if self._stream_task:
            self._stream_task.cancel()
            await asyncio.gather(self._stream_task, return_exceptions=True)
        self.stream = self._make_stream()
        if self._started:
            self._stream_task = asyncio.create_task(self.stream.run(), name="stream")

    # -- settings -----------------------------------------------------------

    async def update_settings(self, changes: dict[str, Any], confirm: str = "") -> Settings:
        return await self._apply(apply_changes(self.settings, changes, confirm))

    async def clear_credentials(self) -> Settings:
        return await self._apply(clear_credentials(self.settings))

    async def _apply(self, new: Settings) -> Settings:
        old = self.settings
        changed = changed_fields(old, new)
        if not changed:
            return old
        self.store.save_settings({f: getattr(new, f) for f in changed})
        self.settings = new

        routing = any(f in ROUTING_FIELDS for f in changed)
        if routing:
            # Where orders or prices come from has changed: stop trading until someone looks again.
            self.set_toggle(TRADING_KEY, False)
            old_clients = (self.market_client, self.delta_client)
            self.market_client, self.delta_client = self._make_clients()
            self.delta.client = self.delta_client
            self.market.delta = self.market_client
            if old.data_env != new.data_env:
                self.prices.clear()
            await self._restart_stream()
            # Requests still in flight on the old clients are allowed to finish.
            asyncio.get_running_loop().call_later(60, lambda: [asyncio.ensure_future(c.aclose()) for c in old_clients])

        log.info("settings_changed fields=%s trading_stopped=%s", changed, routing)
        self.bus.publish(SettingsChanged(changed=changed, trading_stopped=routing))
        return new

    async def test_delta(self, env: str | None = None, api_key: str = "", api_secret: str = "") -> dict[str, Any]:
        """Try credentials without saving them. Blank key/secret means the stored ones."""
        s = self.settings
        env = env if env in ENVIRONMENTS else s.delta_env
        client = self._client_factory(ENVIRONMENTS[env][0], api_key or s.delta_api_key, api_secret or s.delta_api_secret)
        try:
            account = await DeltaBroker(client).account()
            return {"ok": True, "env": env, "balance": account.balance, "currency": account.currency}
        except Exception as exc:
            return {"ok": False, "env": env, "error": str(exc)}
        finally:
            await client.aclose()

    # -- control ------------------------------------------------------------

    def toggle_keys(self) -> set[str]:
        keys = {TRADING_KEY}
        for s in self.strategies:
            keys.add(strategy_key(s.name))
            keys.update(pair_key(s.name, sym) for sym in s.symbols)
        return keys

    def set_toggle(self, key: str, enabled: bool) -> None:
        if key not in self.toggle_keys():
            raise ValueError(f"unknown toggle {key!r}")
        self.store.set_enabled(key, enabled)
        self.bus.publish(ToggleChanged(key=key, enabled=enabled))

    async def close_all(self) -> dict[str, Any]:
        """Kill switch: trading off first, then flatten the active broker."""
        self.set_toggle(TRADING_KEY, False)
        return await self.broker.close_all()

    # -- views --------------------------------------------------------------

    def header(self) -> dict[str, Any]:
        s = self.settings
        feed = self.stream.status()
        return {
            "broker": s.broker,
            "delta_env": s.delta_env,
            "data_env": s.data_env,
            "is_real_money": s.is_real_money,
            "broker_ready": self.broker.not_ready() or "",
            "trading": self.store.enabled(TRADING_KEY, default=False),
            "feed_connected": bool(feed.get("connected")),
            "feed_authenticated": bool(feed.get("authenticated")),
            "token_required": bool(self.cfg.api_token),
            "prices": self.prices.snapshot(),
        }

    def strategies_view(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "version": s.version,
                "interval": s.interval,
                "size": s.size,
                "lookback": s.lookback,
                "doc": (type(s).__doc__ or sys.modules[type(s).__module__].__doc__ or "").strip(),
                "enabled": self.store.enabled(strategy_key(s.name)),
                "symbols": [{"symbol": sym, "enabled": self.store.enabled(pair_key(s.name, sym))} for sym in s.symbols],
                "stats": vars(self.runner.stats[s.name]),
            }
            for s in self.strategies
        ]

    def metrics(self) -> dict[str, Any]:
        return {
            "system": self.monitor.snapshot(),
            "bus": self.bus.stats(),
            "feed": self.stream.status(),
            "rest": {
                **{f"market · {k}": v for k, v in sorted(self.market_client.calls.items())},
                **{f"account · {k}": v for k, v in sorted(self.delta_client.calls.items())},
            },
            "orders": self.store.order_counts(),
            "dashboard_clients": len(self.live.clients),
        }
