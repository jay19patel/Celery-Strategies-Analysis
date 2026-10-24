"""The engine: every component, wired once, and every timed job on one scheduler.

It is the only writer of trading state, settings and toggles, inside one process, so nothing needs
a cross-process lock. Run the backend as a single worker (`uvicorn app.main:app`, no `--workers`):
a second process would be a second engine trading the same accounts.

Fail closed at start: auto-trading on Delta is switched off every time the engine starts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlmodel import col, delete

from app.ai.analyst import Analyst
from app.ai.llm import LLM
from app.analysis.service import MarketDesk
from app.brokers.base import Broker
from app.brokers.delta import DeltaBroker
from app.brokers.paper import PaperBroker
from app.config import Config
from app.core.db import kv_get, kv_set, session
from app.core.events import EventHub
from app.core.models import AiBrief, Decision, EventLog, SignalLog
from app.core.scheduler import Scheduler
from app.errors import AppError, BrokerError
from app.market.delta import DeltaClient
from app.market.feed import OPTIONS_REFRESH, MarketFeed
from app.notify import report
from app.notify.notifier import Notifier
from app.settings import (
    DELTA_ROUTING_FIELDS,
    ENVIRONMENTS,
    Settings,
    apply_changes,
    changed_fields,
    clear_delta_credentials,
    from_stored,
)
from app.trading.autotrader import AutoTrader
from app.trading.desk import Desk
from app.trading.guard import Guard
from app.trading.risk import halt_reason, set_halt
from app.trading.runner import StrategyRunner
from app.trading.service import TradingService

log = logging.getLogger(__name__)

EVENT_RETENTION_DAYS = 30
DECISION_RETENTION_DAYS = 90
SCHEDULE_FIELDS = ("auto_interval_minutes", "ai_brief_minutes", "daily_report_hour", "day_timezone")


class Engine:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.started_at = time.time()
        self.hub = EventHub()
        self.scheduler = Scheduler()
        stored = from_stored(kv_get("settings") or {})
        self._settings = stored.model_copy(update={"auto_delta": False})  # off at every start
        kv_set("settings", self._settings.model_dump())

        s = self._settings
        self.feed = MarketFeed(*ENVIRONMENTS[s.data_env], s.underlyings)
        self.desk = Desk(self.feed, self.settings)
        self.delta = DeltaBroker(DeltaClient(ENVIRONMENTS[s.delta_env][0], s.delta_api_key, s.delta_api_secret))
        self.paper = PaperBroker(self.feed, self.settings, self.desk.fee_rate)
        self.brokers: dict[str, Broker] = {"paper": self.paper, "delta": self.delta}
        self.trading = TradingService(self.feed, self.desk, self.brokers, self.settings, self.hub)
        self.guard = Guard(self.trading, self.settings, self.hub)
        self.llm = LLM(self.settings)
        self.analyst = Analyst(self.llm, self.settings, self.hub)
        self.auto = AutoTrader(self.desk, self.trading, self.analyst, self.settings, self.hub)
        self.market = MarketDesk(self.feed, self.settings, self.hub)
        self.runner = StrategyRunner(self.trading, self.market, self.settings, self.hub)
        self.notifier = Notifier(self.settings)
        self.hub.subscribe("notifier", self.notifier.on_event)

    def settings(self) -> Settings:
        return self._settings

    # -- lifecycle -----------------------------------------------------------------------------

    async def start(self) -> None:
        await self.hub.start()
        if self.cfg.engine_enabled:
            self.feed.start()
            self._schedule()
            self.scheduler.start()
        self.hub.publish("EngineStarted", f"engine started; brokers active: {', '.join(self._settings.active_brokers)}")

    async def stop(self) -> None:
        self.scheduler.shutdown()
        await self.feed.stop()
        await self.delta.client.aclose()
        await self.notifier.aclose()
        await self.hub.stop()

    def _schedule(self) -> None:
        s, sch = self._settings, self.scheduler
        sch.every("market.refresh", "Option chains and prices (REST)", OPTIONS_REFRESH, self.feed.refresh, run_now=True)
        sch.every("market.bars", "Store 15m bars (a year)", 300, self.feed.collect_bars, run_now=True)
        sch.every("market.base", "Store 1m base bars (30 days)", 30, lambda: self.feed.collect_bars("1m"), run_now=True)
        sch.every("options.history", "Record the options book each minute", 60, self.market.record_options)
        sch.every("market.analyst", "Market analysis and insights", 300, self.market.analyse)
        sch.every("market.verdicts", "Mark the cards' past claims", 900, self.market.settle)
        sch.every("strategies.run", "Directional strategies on closed bars", 20, self.runner.run)
        sch.every("guard.structures", "Exits: take profit, stop loss, expiry", 5, self.guard.check_structures)
        sch.every("guard.daily", "Daily loss limit", 30, self.guard.check_daily_loss)
        sch.every("auto.cycle", "Auto options decision", s.auto_interval_minutes * 60, self.auto.cycle)
        sch.every("ai.brief", "AI market brief", s.ai_brief_minutes * 60, self.brief_job)
        sch.daily("report.daily", "Daily report", s.daily_report_hour, 0, s.day_timezone, self.scheduled_report)
        sch.every("live.prices", "Live prices to dashboards", 2, self.push_prices)
        sch.every("housekeeping", "Prune old events and decisions", 3600, self.housekeeping)

    # -- settings ------------------------------------------------------------------------------

    async def update_settings(self, changes: dict[str, Any], confirm: str = "") -> Settings:
        return await self._apply(apply_changes(self._settings, changes, confirm))

    async def clear_delta_credentials(self) -> Settings:
        return await self._apply(clear_delta_credentials(self._settings))

    async def _apply(self, new: Settings) -> Settings:
        old, changed = self._settings, changed_fields(self._settings, new)
        if not changed:
            return old
        self._settings = new
        kv_set("settings", new.model_dump())
        if any(f in changed for f in DELTA_ROUTING_FIELDS):
            await self.delta.client.aclose()
            self.delta.client = DeltaClient(ENVIRONMENTS[new.delta_env][0], new.delta_api_key, new.delta_api_secret)
        if new.data_env != old.data_env or new.underlyings != old.underlyings:
            await self.feed.reconfigure(*ENVIRONMENTS[new.data_env], new.underlyings)
        if self.scheduler.jobs and any(f in changed for f in SCHEDULE_FIELDS):
            self._schedule()
        # Field names only: a value may be a secret.
        self.hub.publish("SettingsChanged", f"settings changed: {', '.join(changed)}", fields=changed)
        return new

    async def test_delta(self, env: str | None = None, api_key: str = "", api_secret: str = "") -> dict[str, Any]:
        s = self._settings
        env = env if env in ENVIRONMENTS else s.delta_env
        client = DeltaClient(ENVIRONMENTS[env][0], api_key or s.delta_api_key, api_secret or s.delta_api_secret)
        try:
            balances = await client.balances()
            return {"ok": True, "env": env, "assets": [b.get("asset_symbol") for b in balances if float(b.get("balance") or 0)]}
        except BrokerError as exc:
            return {"ok": False, "env": env, "error": str(exc)}
        finally:
            await client.aclose()

    def resume_broker(self, broker: str, equity: float) -> None:
        set_halt(broker, self._settings.day_timezone, "", equity)
        self.hub.publish("RiskResumed", f"{broker} resumed for the rest of the day from equity {equity:,.2f}", broker=broker)

    # -- jobs ----------------------------------------------------------------------------------

    async def brief_job(self) -> str:
        if why := self.analyst.ready():
            return f"skipped: {why}"
        out = []
        for underlying in self._settings.underlyings:
            try:
                row = await self.analyst.brief(underlying, self.desk.regime(underlying))
                out.append(f"{underlying}: {'ok' if row['content'] else 'failed'}")
            except AppError as exc:
                out.append(f"{underlying}: {exc}")
        return "; ".join(out)

    async def accounts(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in self._settings.active_brokers:
            b = self.brokers[name]
            if why := b.not_ready():
                out[name] = {"error": why}
                continue
            try:
                out[name] = (await b.account()).to_dict()
            except BrokerError as exc:
                out[name] = {"error": str(exc)}
        return out

    async def scheduled_report(self) -> str:
        return await self.daily_report() if self._settings.daily_report else "off"

    async def daily_report(self) -> str:
        s = self._settings
        text = report.build(s, await self.accounts(), self.trading.structures("open"))
        results = await self.notifier.send("Daily report", text)
        self.hub.publish("DailyReport", "daily report: " + (", ".join(f"{k} {v}" for k, v in results.items()) or "no channel enabled"))
        return str(results)

    async def push_prices(self) -> None:
        if not self.hub.clients:
            return
        prices = {}
        for u in self._settings.underlyings:
            perp = self.feed.perp(u)
            stats = perp.stats if perp else {}
            book = self.market.summary(u) or {}
            day = book.get("day") or {}
            prices[u] = {
                "spot": self.feed.spot(u), "mark": perp.mark if perp else None,
                "change_24h_pct": stats.get("change_24h_pct"), "high_24h": stats.get("high_24h"), "low_24h": stats.get("low_24h"),
                "funding_rate_pct": stats.get("funding_rate_pct"), "oi_usd": stats.get("oi_usd"),
                "bid": stats.get("bid"), "ask": stats.get("ask"),
                "atm_iv": day.get("atm_iv"), "pcr_oi": book.get("pcr_oi"), "max_pain": (book.get("nearest") or {}).get("max_pain"),
            }
        self.hub.publish("Prices", persist=False, prices=prices, at=time.time())

    async def housekeeping(self) -> str:
        now = time.time()
        with session() as s:
            events = s.exec(delete(EventLog).where(col(EventLog.ts) < now - EVENT_RETENTION_DAYS * 86_400)).rowcount
            briefs = s.exec(delete(AiBrief).where(col(AiBrief.ts) < now - EVENT_RETENTION_DAYS * 86_400)).rowcount
            decisions = s.exec(delete(Decision).where(col(Decision.ts) < now - DECISION_RETENTION_DAYS * 86_400)).rowcount
            s.exec(delete(SignalLog).where(col(SignalLog.ts) < now - DECISION_RETENTION_DAYS * 86_400))
            s.commit()
        bars = await asyncio.to_thread(self.feed.prune_bars)
        return f"pruned {events} events, {briefs} briefs, {decisions} decisions, {bars} bars"

    # -- status --------------------------------------------------------------------------------

    def broker_status(self) -> list[dict[str, Any]]:
        s = self._settings
        return [
            {
                "name": name, "active": name in s.active_brokers, "ready": not b.not_ready(), "not_ready": b.not_ready(),
                "auto": s.auto_on(name), "halted": halt_reason(name, s.day_timezone),
                "real_money": name == "delta" and s.is_real_money, "env": s.delta_env if name == "delta" else "paper",
            }
            for name, b in self.brokers.items()
        ]

    def status(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "uptime_seconds": round(time.time() - self.started_at),
            "feed": self.feed.status(),
            "jobs": self.scheduler.status(),
            "brokers": self.broker_status(),
            "ai": self.llm.status(),
            "notifications": {"enabled": self.notifier.enabled(self._settings), "channels": self.notifier.channels},
            "events": {"counts": self.hub.counts, "dashboards": self.hub.clients},
            "delta_api": self.delta.client.calls,
        }
