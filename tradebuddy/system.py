"""Wires every component to the bus, owns runtime settings, and exposes views for the dashboard."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import time
from collections.abc import Callable
from typing import Any, Protocol

import psutil
from fastapi import WebSocket

from tradebuddy.brokers import Broker, DeltaBroker, PaperBroker
from tradebuddy.config import Config
from tradebuddy.delta import DeltaClient
from tradebuddy.email_report import DailyReporter
from tradebuddy.errors import BrokerError
from tradebuddy.events import (
    AIReport,
    CandleClosed,
    Event,
    EventBus,
    FeedHeartbeat,
    MarketAnalysis,
    MarketStats,
    OptionsSnapshot,
    OrderRequested,
    OrderUpdate,
    ProcessHeartbeat,
    SettingsChanged,
    SignalGenerated,
    StrategyEvaluated,
    Tick,
    ToggleChanged,
)
from tradebuddy.guard import PositionGuard
from tradebuddy.jobs import Jobs
from tradebuddy.options import UNDERLYINGS, OptionsBook, OptionsFeed, history_row
from tradebuddy.runner import Evaluator, InlineEvaluator, MarketData, StrategyRunner, pair_key, strategy_key
from tradebuddy.settings import (
    BROKERS,
    DELTA_ROUTING_FIELDS,
    ENVIRONMENTS,
    STREAM_FIELDS,
    Settings,
    apply_changes,
    changed_fields,
    clear_credentials,
    from_stored,
)
from tradebuddy.store import Store
from tradebuddy.strategies import Strategy, discover
from tradebuddy.stream import BarCloser, DeltaStream, PriceBook
from tradebuddy.trading import AUTO_EVERY, TRADING_DEFAULT, AutoStructures, Executor, Trader, trading_key

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
        if self.clients:
            await self.send(event.to_dict())

    async def send(self, message: dict[str, Any]) -> None:
        if not self.clients:
            return
        if message.get("type") in ("Tick", "MarketStats"):
            key, ts = f"{message['type']}:{message.get('symbol', '')}", message.get("ts", 0)
            if ts - self._last_tick.get(key, 0) < 1:
                return
            self._last_tick[key] = ts
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                self.clients.discard(ws)


class Monitor:
    """Process and event-loop load."""

    def __init__(self, role: str = "all") -> None:
        self.role = role
        self.started_at = time.time()
        self.loop_lag_ms = 0.0
        self.loop_lag_max_ms = 0.0
        self._proc = psutil.Process(os.getpid())
        self._proc.cpu_percent(None)

        self.cpu_pct = 0.0
        self.system_cpu_pct = psutil.cpu_percent(None)

    async def run(self, interval: float = 0.5) -> None:
        ticks = 0
        while True:
            before = time.perf_counter()
            await asyncio.sleep(interval)
            lag = max(0.0, (time.perf_counter() - before - interval) * 1000)
            self.loop_lag_ms = round(lag, 2)
            self.loop_lag_max_ms = round(max(self.loop_lag_max_ms, lag), 2)
            ticks += 1
            if ticks % 4 == 0:  # CPU over a fixed 2s window, not "since whoever asked last"
                self.cpu_pct = self._proc.cpu_percent(None)
                self.system_cpu_pct = psutil.cpu_percent(None)

    def snapshot(self) -> dict[str, Any]:
        with self._proc.oneshot():
            memory = self._proc.memory_info().rss
            threads = self._proc.num_threads()
        cpu = self.cpu_pct
        return {
            "role": self.role,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "at": time.time(),
            "uptime_seconds": round(time.time() - self.started_at),
            "process_cpu_pct": cpu,
            "process_memory_mb": round(memory / 1_048_576, 1),
            "threads": threads,
            "asyncio_tasks": len(asyncio.all_tasks()),
            "loop_lag_ms": self.loop_lag_ms,
            "loop_lag_max_ms": self.loop_lag_max_ms,
            "system_cpu_pct": self.system_cpu_pct,
            "system_memory_pct": psutil.virtual_memory().percent,
            "load_avg": [round(x, 2) for x in os.getloadavg()],
        }


class RemoteFeed:
    """Engine-side view of the feed process, built from its heartbeats."""

    STALE_AFTER = 15.0

    def __init__(self) -> None:
        self._status: dict[str, Any] = {"connected": False, "authenticated": False, "messages": {}, "bars_closed_by": {}}
        self.process: dict[str, Any] | None = None
        self.updated_at = 0.0
        self.started_at = time.time()

    async def run(self) -> None:
        return None

    async def on_heartbeat(self, e: FeedHeartbeat) -> None:
        self._status, self.process, self.updated_at = e.status, e.process, time.time()

    def status(self) -> dict[str, Any]:
        status = dict(self._status)
        if time.time() - self.updated_at > self.STALE_AFTER:
            status |= {
                "connected": False, "authenticated": False, "last_error": "no heartbeat from the feed process — is it running?",
                "down_since": status.get("down_since") or self.updated_at or self.started_at,
            }
        return status


class System:
    """The engine. In single-process mode it also owns the Delta stream; in
    distributed mode the stream lives in the feed process and strategies run
    on Celery workers, but every decision is still made here."""

    def __init__(
        self,
        cfg: Config,
        strategies: list[Strategy] | None = None,
        stream_factory: StreamFactory | None = None,
        client_factory: ClientFactory | None = None,
        evaluator: Callable[[System], Evaluator] | None = None,
        local_feed: bool = True,
        role: str = "all",
        options_feed: bool = False,  # single process: also run the options book (distributed: the feed does)
        analyst: Callable[[System], Any] | None = None,  # single process: a market analyst to run here
    ) -> None:
        self.cfg = cfg
        self.bus = EventBus()
        self.store = Store(cfg.db_path)
        self.settings: Settings = from_stored(self.store.load_settings())
        self.strategies = discover() if strategies is None else strategies
        self.prices = PriceBook()
        self.monitor = Monitor(role)
        self.live = Broadcaster()
        self.local_feed = local_feed

        self._client_factory = client_factory or DeltaClient
        self._stream_factory = stream_factory or DeltaStream
        self.market_client, self.delta_client = self._make_clients()

        # Options and analysis, for the dashboard: the latest per symbol, wherever it was made.
        self.options_latest: dict[str, dict[str, Any]] = {}
        self.analysis_latest: dict[str, dict[str, Any]] = {}
        for e in self.store.recent_events(20, types=["MarketAnalysis"]):  # survive a restart
            self.analysis_latest.setdefault(e["symbol"], e)
        reports = self.store.recent_events(50, types=["AIReport"])
        self.ai_latest: dict[str, Any] | None = reports[0] if reports else None  # the last attempt
        self.ai_last_good: dict[str, Any] | None = next((r for r in reports if r.get("ok")), None)
        self._options_minute: dict[str, int] = {}
        self.options_book = OptionsBook(set(UNDERLYINGS.values())) if local_feed and options_feed else None
        self.options_feed = (
            OptionsFeed(self.options_book, self.bus.publish, lambda u: self.market_client.option_tickers(u), self.prices.price)
            if self.options_book else None
        )

        pairs = {(sym, s.interval) for s in self.strategies for sym in s.symbols}
        self.closer = BarCloser(self.bus, pairs) if local_feed else None
        self.remote_feed = None if local_feed else RemoteFeed()
        self.stream: Stream = self._make_stream() if local_feed else self.remote_feed

        self.paper = PaperBroker(
            self.store, self.bus, self.prices, specs=lambda sym: self.market_client.product(sym), settings=lambda: self.settings,
            options_snapshot=lambda sym: (self.options_latest.get(sym) or {}).get("summary"),
        )
        self.delta = DeltaBroker(self.delta_client)
        self.brokers: dict[str, Broker] = {"paper": self.paper, "delta": self.delta}

        self.market = MarketData(self.market_client, self.prices)
        self.evaluator: Evaluator = evaluator(self) if evaluator else InlineEvaluator(self.bus, self.market)
        self.runner = StrategyRunner(
            self.bus, self.store, self.strategies, self.evaluator,
            prices=lambda: {sym: p["price"] for sym, p in self.prices.snapshot().items() if p["fresh"]},
            data_env=lambda: self.settings.data_env,
        )
        self.protection_lock = asyncio.Lock()  # one SL/TP change at a time: guard trails and manual edits
        self.guard = PositionGuard(self.bus, self.store, self.brokers, lambda: self.settings, self.tick_size, self.protection_lock)
        self.trader = Trader(self.bus, self.store, self.brokers, self.prices, lambda: self.settings, self.guard.halt_reason)
        self.executor = Executor(self.bus, self.store, self.brokers)

        self.bus.subscribe(self.prices.on_tick, Tick)
        self.bus.subscribe(self.prices.on_stats, MarketStats)
        self.bus.subscribe(self.paper.on_tick, Tick)
        self.bus.subscribe(self.runner.on_candle_closed, CandleClosed)
        self.bus.subscribe(self.runner.on_evaluated, StrategyEvaluated)
        if self.remote_feed:
            self.bus.subscribe(self.remote_feed.on_heartbeat, FeedHeartbeat)
        self.bus.subscribe(self.trader.on_signal, SignalGenerated)
        self.bus.subscribe(self.executor.on_order_requested, OrderRequested)
        self.bus.subscribe(self.executor.on_order_update, OrderUpdate)
        self.bus.subscribe(self.record)
        self.bus.subscribe(self.on_options, OptionsSnapshot)
        self.bus.subscribe(self.on_analysis, MarketAnalysis)
        self.bus.subscribe(self.on_process, ProcessHeartbeat)
        self.bus.subscribe(self.on_ai_report, AIReport)
        self.bus.subscribe(self.live.on_event)
        self.analyst = analyst(self) if analyst else None

        # Background jobs, for the System page. The feed's and the analyst's come with their heartbeats.
        self.jobs = Jobs(role)
        self.jobs_housekeeping = self.jobs.add("housekeeping", "Deletes events and options minutes past their retention", 3600)
        self.guard.job = self.jobs.add("position guard", "Daily loss limit and auto trailing, every active broker", 3)
        self.jobs_reconcile = self.jobs.add("reconcile", "At start: asks brokers about orders a previous run left open")
        if self.closer:
            self.closer.job = self.jobs.add("bar clock", "Closes bars the stream has not, when their time is up", 1)
        if self.options_feed:
            self.options_feed.job = self.jobs.add("options snapshot", "Summarises the options book per underlying (REST if the socket is quiet)", self.options_feed.every)
        if self.analyst:
            self.analyst.job = self.jobs.add("market analysis", "Insights, forecast, options playbook, AI review", self.analyst.every)
        self.jobs_option_exits = self.jobs.add("paper option exits", "Stop loss, take profit and pre-expiry close of paper option structures", 5)
        self.auto_options = AutoStructures(
            self.trader, self.store, lambda: self.settings,
            snapshots=lambda: {sym: v["summary"] for sym, v in self.options_latest.items() if v.get("summary")},
            history=lambda underlying, since: self.store.options_history(underlying, since, 300),
        )
        self.auto_options.job = self.jobs.add("options auto-trade", "Opens the suggested option structure on paper, when switched on", AUTO_EVERY)
        self.reporter = DailyReporter(self)
        self.reporter.job = self.jobs.add("daily email", "Emails the day's report once, at the hour set in Settings", self.reporter.every)
        self.remote_processes: dict[str, dict[str, Any]] = {}  # role -> latest heartbeat (analyst)

        # Fail closed: a broker that moves real orders starts every run switched off.
        self.store.set_enabled(trading_key("delta"), False)
        self._started = False
        self._stream_task: asyncio.Task | None = None
        self._tasks: list[asyncio.Task] = []

    @property
    def active(self) -> dict[str, Broker]:
        """Brokers switched on in Settings, in display order."""
        return {name: self.brokers[name] for name in self.settings.active_brokers}

    def trading_on(self, broker: str) -> bool:
        return self.store.enabled(trading_key(broker), default=TRADING_DEFAULT[broker])

    async def tick_size(self, symbol: str) -> float:
        return float((await self.market_client.product(symbol)).get("tick_size") or 0.5)

    def _make_clients(self) -> tuple[DeltaClient, DeltaClient]:
        s = self.settings
        market = self._client_factory(ENVIRONMENTS[s.data_env][0])
        delta = self._client_factory(ENVIRONMENTS[s.delta_env][0], s.delta_api_key, s.delta_api_secret)
        return market, delta

    def _make_stream(self) -> Stream:
        s = self.settings
        private = s.delta_active and s.has_credentials  # Delta's orders/positions channels, only when Delta is in use
        extra = {"options": self.options_book} if self.options_book is not None else {}
        return self._stream_factory(
            ENVIRONMENTS[s.data_env][1], self.bus, self.closer,
            s.delta_api_key if private else "", s.delta_api_secret if private else "", **extra,
        )

    # Not written to the event log: prices arrive many times a second, heartbeats every few seconds,
    # and a StrategyEvaluated per strategy per bar is summed up in the runner's stats (its failures
    # are StrategyError, which is kept).
    UNLOGGED = (Tick, MarketStats, FeedHeartbeat, ProcessHeartbeat, StrategyEvaluated, OptionsSnapshot)  # options: summarised per minute below

    async def record(self, event: Event) -> None:
        if not isinstance(event, self.UNLOGGED):
            self.store.record_event(event.to_dict())

    async def housekeeping(self, every: float = 3600.0) -> None:
        """Keeps the event log inside its retention, so a year of running does not fill the disk."""
        while True:
            try:
                with self.jobs_housekeeping.tick() as job:
                    deleted = await asyncio.to_thread(self.store.prune_events)
                    minutes = await asyncio.to_thread(self.store.prune_options)
                    reports = await asyncio.to_thread(self.store.prune_ai_reports)
                    job.note = f"deleted {deleted} events, {minutes} options minutes, {reports} AI reports"
                if deleted or minutes:
                    log.info("events_pruned deleted=%d options_minutes=%d", deleted, minutes)
            except Exception:
                log.exception("events_prune_failed")
            await asyncio.sleep(every)

    async def option_exits(self, every: float = 5.0) -> None:
        """Paper option structures: combined SL/TP on the latest chain, and the close before expiry."""
        while True:
            try:
                with self.jobs_option_exits.tick() as job:
                    if self.paper.options.count():
                        async with self.protection_lock:
                            closed = self.paper.options.check()
                        job.note = f"closed {', '.join(closed)}" if closed else f"{self.paper.options.count()} open"
                    else:
                        job.note = "none open"
            except Exception:
                log.exception("option_exits_failed")
            await asyncio.sleep(every)

    async def _reconcile(self) -> None:
        try:
            with self.jobs_reconcile.tick():
                await self.executor.reconcile(before=time.time())
        except Exception:
            log.exception("reconcile_failed")

    async def on_options(self, e: OptionsSnapshot) -> None:
        self.options_latest[e.symbol] = {"source": e.source, "at": e.ts, "summary": e.summary}
        minute = int(e.ts // 60)
        if self._options_minute.get(e.underlying) != minute:  # one stored row per minute
            self._options_minute[e.underlying] = minute
            self.store.record_options(history_row(e.summary))

    async def on_ai_report(self, e: AIReport) -> None:
        self.ai_latest = e.to_dict()
        self.store.record_ai_report(self.ai_latest)
        if e.ok:
            self.ai_last_good = self.ai_latest

    async def on_process(self, e: ProcessHeartbeat) -> None:
        self.remote_processes[e.role] = {"process": e.process, "jobs": e.jobs, "status": e.status, "at": e.ts}

    def all_jobs(self) -> list[dict[str, Any]]:
        """Every background job in every process. A process that stopped reporting shows its jobs as silent."""
        now = time.time()

        def mark(jobs: list[dict[str, Any]], role: str, at: float, stale: float) -> list[dict[str, Any]]:
            if now - at <= stale:
                return jobs
            return [j | {"state": "silent", "last_error": f"no heartbeat from the {role} for {now - at:.0f}s"} for j in jobs]

        jobs = self.jobs.snapshot()
        if self.remote_feed and self.remote_feed.updated_at:
            jobs += mark(self.remote_feed._status.get("jobs", []), "feed", self.remote_feed.updated_at, RemoteFeed.STALE_AFTER)
        for role, beat in self.remote_processes.items():
            jobs += mark(beat["jobs"], role, beat["at"], 60)
        return jobs

    async def on_analysis(self, e: MarketAnalysis) -> None:
        self.analysis_latest[e.symbol] = e.to_dict()

    def market_status(self) -> dict[str, Any]:
        """Is there live market data for every symbol a strategy trades? Without it strategies are
        paused and nothing new is entered (invariant 8): the dashboard says so on every page."""
        feed = self.stream.status()
        fresh = {sym for sym, p in self.prices.snapshot().items() if p["fresh"]}
        wanted = sorted({sym for s in self.strategies for sym in s.symbols})
        missing = [sym for sym in wanted if sym not in fresh]
        if not feed.get("connected"):
            reason = "market data stream is down"
        elif missing:
            reason = f"no live price for {', '.join(missing)}"
        else:
            reason = ""
        return {
            "live": not reason,
            "reason": reason,
            "missing": missing if feed.get("connected") else wanted,
            "error": feed.get("last_error", ""),
            "down_since": feed.get("down_since"),
            "url": feed.get("url", ""),
        }

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        self.bus.start()
        self._started = True
        self._tasks = [
            asyncio.create_task(self.monitor.run(), name="monitor"),
            asyncio.create_task(self._reconcile(), name="reconcile"),
            asyncio.create_task(self.guard.run(), name="guard"),
            asyncio.create_task(self.housekeeping(), name="housekeeping"),
            asyncio.create_task(self.reporter.run(), name="daily-email"),
            asyncio.create_task(self.option_exits(), name="option-exits"),
            asyncio.create_task(self.auto_options.run(), name="auto-options"),
        ]
        if self.local_feed:
            self._stream_task = asyncio.create_task(self.stream.run(), name="stream")
            self._tasks.append(asyncio.create_task(self.closer.run(), name="bar-clock"))
            if self.options_feed:
                self._tasks.append(asyncio.create_task(self.options_feed.run(), name="options"))
        if self.analyst:
            self._tasks.append(asyncio.create_task(self.analyst.run(), name="analyst"))
        log.info("system_started brokers=%s data=%s strategies=%s", self.settings.active_brokers, self.settings.data_env, [s.name for s in self.strategies])

    async def stop(self) -> None:
        tasks = [t for t in (self._stream_task, *self._tasks) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.bus.stop()
        await self.market_client.aclose()
        await self.delta_client.aclose()

    async def drain(self) -> None:
        """Wait until the bus and any inline evaluations are idle (tests, shutdown)."""
        while True:
            await self.bus.drain()
            drain = getattr(self.evaluator, "drain", None)
            if drain is None or not getattr(self.evaluator, "_tasks", None):
                return
            await drain()

    async def _restart_stream(self) -> None:
        if not self.local_feed:
            return  # the feed process restarts itself on SettingsChanged
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

        delta_routing = any(f in DELTA_ROUTING_FIELDS for f in changed)
        if delta_routing:
            # Where Delta orders go has changed: stop Delta trading until someone looks again.
            self.set_toggle(trading_key("delta"), False)
        if not new.paper_active and old.paper_active:
            self.set_toggle(trading_key("paper"), False)
        if any(f in STREAM_FIELDS for f in changed):
            old_clients = (self.market_client, self.delta_client)
            self.market_client, self.delta_client = self._make_clients()
            self.delta.client = self.delta_client
            self.market.delta = self.market_client
            if old.data_env != new.data_env:
                self.prices.clear()
            await self._restart_stream()
            # Requests still in flight on the old clients are allowed to finish.
            asyncio.get_running_loop().call_later(60, lambda: [asyncio.ensure_future(c.aclose()) for c in old_clients])

        log.info("settings_changed fields=%s delta_trading_stopped=%s", changed, delta_routing)
        self.bus.publish(SettingsChanged(changed=changed, trading_stopped=delta_routing))
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
        keys = {trading_key(b) for b in BROKERS}
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
        """Kill switch: every broker's trading off first, then flatten every broker that is active
        or could still hold positions (a deactivated Delta account with keys)."""
        for name in BROKERS:
            self.set_toggle(trading_key(name), False)
        targets = {n: b for n, b in self.brokers.items() if n in self.settings.active_brokers or not b.not_ready()}
        closed, errors = [], []
        for name, broker in targets.items():
            try:
                result = await broker.close_all()
            except BrokerError as exc:
                errors.append(f"{name}: {exc}")
                continue
            closed += [f"{name}:{sym}" for sym in result["closed"]]
            errors += [f"{name}: {err}" for err in result["errors"]]
        return {"closed": closed, "errors": errors}

    # -- views --------------------------------------------------------------

    def header(self) -> dict[str, Any]:
        s = self.settings
        feed = self.stream.status()
        return {
            "brokers": [
                {
                    "name": name,
                    "env": s.delta_env if name == "delta" else s.data_env,
                    "real_money": name == "delta" and s.is_real_money,
                    "trading": self.trading_on(name),
                    "not_ready": broker.not_ready(),
                }
                for name, broker in self.active.items()
            ],
            "delta_env": s.delta_env,
            "data_env": s.data_env,
            "is_real_money": s.is_real_money,
            "feed_connected": bool(feed.get("connected")),
            "feed_authenticated": bool(feed.get("authenticated")),
            "market": self.market_status(),
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
                "is_default_sl_tp": s.is_default_sl_tp,
                "doc": (type(s).__doc__ or sys.modules[type(s).__module__].__doc__ or "").strip(),
                "enabled": self.store.enabled(strategy_key(s.name)),
                "symbols": [{"symbol": sym, "enabled": self.store.enabled(pair_key(s.name, sym))} for sym in s.symbols],
                "stats": vars(self.runner.stats[s.name]),
            }
            for s in self.strategies
        ]

    def metrics(self) -> dict[str, Any]:
        me = self.monitor.snapshot()
        return {
            "system": me,
            "bus": self.bus.stats(),
            "feed": self.stream.status(),
            "rest": {
                **{f"market · {k}": v for k, v in sorted(self.market_client.calls.items())},
                **{f"account · {k}": v for k, v in sorted(self.delta_client.calls.items())},
            },
            "orders": self.store.order_counts(),
            "event_log": self.store.events_size(),
            "evaluator": self.evaluator.stats(),
            "processes": [
                me, *([self.remote_feed.process] if self.remote_feed and self.remote_feed.process else []),
                *[b["process"] for b in self.remote_processes.values()],
            ],
            "jobs": self.all_jobs(),
            "analyst": (self.remote_processes.get("analyst") or {}).get("status") or (self.analyst.status() if self.analyst else None),
            "options_feed": (self.remote_feed.status() if self.remote_feed else {}).get("options") or (self.options_feed.stats() if self.options_feed else None),
            "dashboard_clients": len(self.live.clients),
        }
