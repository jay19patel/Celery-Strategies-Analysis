"""Everything the dashboard can ask or command, in one place.

The web layer calls these methods directly (single process) or through
ZeroMQ RPC (distributed): `RemoteApi` has the same methods, so routes do not
know which one they hold. Every method takes and returns plain JSON.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from tradebuddy.brokers import Broker
from tradebuddy.codec import EVENT_TYPES
from tradebuddy.delta import round_to_tick
from tradebuddy.errors import BrokerError
from tradebuddy.options import UNDERLYINGS
from tradebuddy.settings import SettingsError
from tradebuddy.system import System
from tradebuddy.trading import TradeRefused
from tradebuddy.transport import RpcClient, RpcError

SIGNAL_EVENTS = ["SignalGenerated", "TradeSkipped", "StrategyError"]
ACTIVITY_EVENTS = [*SIGNAL_EVENTS, "OrderPlaced", "OrderFailed", "OrderUnknown", "PositionClosed"]
LEVEL_TYPES = {
    "error": sorted(n for n, cls in EVENT_TYPES.items() if cls.LEVEL == "error"),
    "warning": sorted(n for n, cls in EVENT_TYPES.items() if cls.LEVEL in ("warning", "error")),
}

# Methods the engine answers over RPC. Anything else is refused.
METHODS = (
    "page_context", "header", "overview", "strategies", "signals", "positions", "orders", "open_orders", "account",
    "paper_stats", "paper_trades", "metrics", "events", "settings", "update_settings", "clear_credentials",
    "test_delta", "toggle", "close_all", "close_position", "protection", "paper_reset", "place_order", "order_ticket",
    "set_position_control", "risk", "risk_resume", "analysis", "options_history", "database",
    "ai_digest", "ai_report", "test_ai",
)


class ApiError(RpcError):
    """An error with an HTTP status, raised the same way locally and over RPC."""


class Api:
    def __init__(self, system: System) -> None:
        self.system = system
        self._db_stats: tuple[float, dict[str, Any]] = (0.0, {})

    def _broker(self, name: str | None) -> Broker:
        if name is None:
            return next(iter(self.system.active.values()))
        if name not in self.system.brokers:
            raise ApiError(404, f"unknown broker {name!r}")
        return self.system.brokers[name]

    @staticmethod
    async def _call(coro):
        try:
            return await coro
        except BrokerError as exc:
            raise ApiError(502, str(exc)) from exc

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method not in METHODS:
            raise ApiError(404, f"unknown method {method!r}")
        return await getattr(self, method)(**params)

    # -- reads --------------------------------------------------------------

    async def page_context(self) -> dict[str, Any]:
        s = self.system.settings
        return {"active_brokers": s.active_brokers, "paper_active": s.paper_active}

    async def header(self) -> dict[str, Any]:
        return self.system.header()

    async def overview(self) -> dict[str, Any]:
        day = time.time() - 86_400
        accounts = []
        for name, broker in self.system.active.items():
            try:
                accounts.append({"broker": name, "account": (await broker.account()).to_dict(), "error": ""})
            except BrokerError as exc:
                accounts.append({"broker": name, "account": None, "error": str(exc)})
        store = self.system.store
        return {
            "header": self.system.header(),
            "accounts": accounts,
            "counts_24h": {t: store.count_events_since(t, day) for t in ("SignalGenerated", "TradeSkipped", "OrderPlaced", "OrderFailed", "PositionClosed")},
            "strategies": self.system.strategies_view(),
            "recent": store.recent_events(15, types=ACTIVITY_EVENTS),
            "paper": self.system.paper.stats(),
            "risk": await self.risk(),
        }

    async def strategies(self) -> list[dict[str, Any]]:
        return self.system.strategies_view()

    async def signals(self, limit: int = 300, strategy: str = "", symbol: str = "") -> list[dict[str, Any]]:
        rows = self.system.store.recent_events(min(limit, 2000), types=SIGNAL_EVENTS)
        return [r for r in rows if (not strategy or r.get("strategy") == strategy) and (not symbol or r.get("symbol") == symbol)]

    async def positions(self, broker: str | None = None) -> list[dict[str, Any]]:
        b = self._broker(broker)
        controls = self.system.store.controls(b.name)
        out = []
        for p in await self._call(b.positions()):
            row = p.to_dict()
            c = controls.get(p.symbol)
            row["control"] = {k: c[k] for k in ("trailing", "max_steps", "steps")} | {"trailing": bool(c["trailing"])} if c else None
            row["roe_pct"] = p.unrealized_pnl / p.margin * 100 if p.margin else None
            out.append(row)
        return out

    async def orders(self, broker: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        return self.system.store.recent_orders(min(limit, 2000), broker=broker)

    async def open_orders(self, broker: str | None = None) -> list[dict[str, Any]]:
        return await self._call(self._broker(broker).open_orders())

    async def account(self, broker: str | None = None) -> dict[str, Any]:
        return (await self._call(self._broker(broker).account())).to_dict()

    async def paper_stats(self) -> dict[str, Any]:
        return self.system.paper.stats()

    async def paper_trades(self, limit: int = 300) -> list[dict[str, Any]]:
        return self.system.paper.trades(min(limit, 5000))

    async def metrics(self) -> dict[str, Any]:
        return self.system.metrics()

    async def analysis(self) -> dict[str, Any]:
        """Per symbol: the perpetual's 24h stats, the options summary and chains, and the latest analysis."""
        s, prices = self.system, self.system.prices.snapshot()
        symbols = sorted(set(UNDERLYINGS) | set(s.options_latest) | set(s.analysis_latest))
        return {
            "symbols": {
                sym: {
                    "price": (prices.get(sym) or {}).get("price"),
                    "stats": (prices.get(sym) or {}).get("stats"),
                    "options": s.options_latest.get(sym),
                    "analysis": s.analysis_latest.get(sym),
                }
                for sym in symbols
            },
            "ai": "on" if s.settings.ai_ready else ("no key" if s.settings.ai_enabled else "off"),
            "ai_model": s.settings.mistral_model,
        }

    async def options_history(self, symbol: str = "BTCUSD", hours: float = 24) -> list[dict[str, Any]]:
        if symbol not in UNDERLYINGS:
            raise ApiError(404, f"no options for {symbol!r}")
        hours = min(max(hours, 1), 24 * 30)
        step = 60 if hours <= 24 else 900  # a minute apart for a day, a bar apart beyond
        return self.system.store.options_history(UNDERLYINGS[symbol], time.time() - hours * 3600, step)

    async def database(self) -> dict[str, Any]:
        """Tables, row counts and sizes. Counting a large table takes a moment, so the answer is reused for 30s."""
        at, stats = self._db_stats
        if time.time() - at > 30:
            stats = await asyncio.to_thread(self.system.store.db_stats)
            self._db_stats = (time.time(), stats)
        return stats | {"measured_at": self._db_stats[0]}

    # -- TradeBuddy AI -----------------------------------------------------------------

    async def ai_digest(self, include_account: bool = True) -> dict[str, Any]:
        """What TB-AI reads: system, trading and portfolio as plain numbers. No keys, no order or
        exchange ids; positions and account figures only with `include_account`."""
        s, now = self.system, time.time()
        day = now - 86_400
        m = s.metrics()
        r1 = lambda v, n=2: None if v is None else round(v, n)  # noqa: E731

        market = s.market_status()
        failing = [
            {k: j.get(k) for k in ("process", "name", "state", "runs", "errors", "last_error")}
            for j in m["jobs"]
            if j.get("errors") or j.get("state") in ("failed", "silent") or (j.get("next_at") and now - j["next_at"] > max(60, 2 * (j.get("every") or 0)))
        ]
        warn_types = LEVEL_TYPES["warning"]
        recent = [e for e in s.store.recent_events(500, types=warn_types) if e["ts"] >= day]
        by_type: dict[str, int] = {}
        for e in recent:
            by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        errors = [
            {"type": e["type"], "minutes_ago": round((now - e["ts"]) / 60), "what": str(e.get("error") or e.get("message") or e.get("reason") or "")[:160]}
            for e in recent if e["type"] in LEVEL_TYPES["error"]
        ][:6]
        feed = m["feed"]
        system = {
            "market_data": {
                "live": market["live"], "reason": market["reason"], "missing": market["missing"], "error": market["error"][:160],
                "down_minutes": r1((now - market["down_since"]) / 60, 0) if market.get("down_since") else None,
            },
            "feed": {"connected": feed.get("connected"), "reconnects": feed.get("reconnects"), "seconds_since_message": feed.get("seconds_since_message")},
            "processes": [
                {"role": p.get("role"), "cpu_pct": p.get("process_cpu_pct"), "memory_mb": p.get("process_memory_mb"), "loop_lag_ms": p.get("loop_lag_ms"),
                 "uptime_hours": r1((p.get("uptime_seconds") or 0) / 3600, 1), "seconds_since_report": r1(now - (p.get("at") or now), 0)}
                for p in m["processes"]
            ],
            "host": {"cpu_pct": m["system"].get("system_cpu_pct"), "memory_pct": m["system"].get("system_memory_pct"), "load": m["system"].get("load_avg")},
            "jobs": {"total": len(m["jobs"]), "needing_attention": failing},
            "bus": {"queued": sum(w["queue"] for w in m["bus"]["workers"]),
                    "handler_errors": [{"handler": w["name"], "errors": w["errors"], "last": w["last_error"][:120]} for w in m["bus"]["workers"] if w["errors"]]},
            "evaluator": {k: m["evaluator"].get(k) for k in ("mode", "submitted", "failed_to_submit", "in_flight")} | {"workers_online": len(m["evaluator"].get("workers") or [])},
            "warnings_and_errors_24h": by_type, "recent_errors": errors,
            "event_log": m["event_log"],
        }

        skips: dict[str, int] = {}
        for e in s.store.recent_events(2000, types=["TradeSkipped"]):
            if e["ts"] >= day:
                skips[e["reason"]] = skips.get(e["reason"], 0) + 1
        trading: dict[str, Any] = {
            "trading_switches": {b: s.trading_on(b) for b in s.settings.active_brokers},
            "strategies": [
                {"name": v["name"], "timeframe": v["interval"], "on": v["enabled"], "symbols_on": [x["symbol"] for x in v["symbols"] if x["enabled"]],
                 **{k: v["stats"][k] for k in ("runs", "signals", "errors", "paused", "in_flight", "last_result")}}
                for v in s.strategies_view()
            ],
            "last_24h": {t: s.store.count_events_since(t, day) for t in ("SignalGenerated", "TradeSkipped", "OrderPlaced", "OrderFailed", "OrderUnknown", "PositionClosed")},
            "top_skip_reasons_24h": sorted(skips.items(), key=lambda kv: -kv[1])[:6],
            "orders_by_status": s.store.order_counts(),
        }
        if not include_account:
            return {"as_of": now, "system": system, "trading": trading, "portfolio": {"shared": False}}

        positions, accounts = [], []
        for name, broker in s.active.items():
            try:
                acct = await broker.account()
                accounts.append({"broker": name, "currency": acct.currency, "equity": r1(acct.equity), "available": r1(acct.available),
                                 "margin_used": r1(acct.margin_used), "unrealized_pnl": r1(acct.unrealized_pnl)})
                for p in await broker.positions():
                    d = 1 if p.side == "long" else -1
                    positions.append({
                        "broker": name, "symbol": p.symbol, "side": p.side, "size": p.size, "strategy": p.strategy, "entry": p.entry_price, "mark": p.mark_price,
                        "pnl": r1(p.unrealized_pnl), "roe_pct": r1(p.unrealized_pnl / p.margin * 100 if p.margin else None),
                        "to_stop_pct": r1(d * (p.mark_price - p.stop_loss) / p.mark_price * 100 if p.stop_loss else None),
                        "to_target_pct": r1(d * (p.take_profit - p.mark_price) / p.mark_price * 100 if p.take_profit else None),
                        "hours_open": r1((now - p.opened_at) / 3600, 1) if p.opened_at else None,
                    })
            except BrokerError as exc:
                accounts.append({"broker": name, "error": str(exc)[:160]})
        trading["open_positions"] = positions
        paper = s.paper.stats() if s.settings.paper_active else None
        closed = [
            {"strategy": t["strategy"], "symbol": t["symbol"], "side": t["side"], "pnl": r1(t["pnl"], 4), "reason": t["reason"],
             "hours_held": r1((t["closed_at"] - t["opened_at"]) / 3600, 1), "hours_ago": r1((now - t["closed_at"]) / 3600, 1)}
            for t in (s.paper.trades(10) if paper else [])
        ]
        risk = await self.risk()
        portfolio = {
            "shared": True, "accounts": accounts,
            "today": [{k: b.get(k) for k in ("broker", "pnl", "pnl_pct", "limit_pct", "used_pct", "halted", "halt_reason", "error")} for b in risk["brokers"]],
            "paper_performance": {"overall": paper["overall"], "by_strategy": paper["by_strategy"]} if paper else None,
            "recent_closed_trades": closed,
        }
        return {"as_of": now, "system": system, "trading": trading, "portfolio": portfolio}

    async def ai_report(self) -> dict[str, Any]:
        s, settings = self.system, self.system.settings
        history = [
            {"ts": e["ts"], "ok": e.get("ok"), "model": e.get("model"), "error": e.get("error"), "status": e.get("status"),
             "headline": (e.get("report") or {}).get("headline"), "health": (e.get("report") or {}).get("health")}
            for e in s.store.recent_events(30, types=["AIReport"])
        ]
        analyst = (s.remote_processes.get("analyst") or {}).get("status") or (s.analyst.status() if s.analyst else None)
        return {
            "enabled": settings.ai_enabled, "ready": settings.ai_ready, "has_key": bool(settings.mistral_api_key),
            "model": settings.mistral_model, "interval_minutes": settings.ai_interval_minutes, "share_account": settings.ai_share_account,
            "latest": s.ai_latest, "report": s.ai_last_good, "history": history, "analyst": analyst,
        }

    async def test_ai(self, model: str = "") -> dict[str, Any]:
        """Try the stored Mistral key: is it accepted, is the model available, what are its limits."""
        from tradebuddy.mistral import check

        settings = self.system.settings
        if not settings.mistral_api_key:
            raise ApiError(400, "no Mistral API key stored: add one in Settings → AI")
        return await check(settings.mistral_api_key, model.strip() or settings.mistral_model)

    async def events(self, limit: int = 300, type: str = "", level: str = "") -> list[dict[str, Any]]:
        """`level` "warning" -> warnings and errors, "error" -> errors only."""
        types = [type] if type else None
        if level in LEVEL_TYPES:
            types = [t for t in LEVEL_TYPES[level] if not types or t in types] or ["-"]
        return self.system.store.recent_events(min(limit, 2000), types=types)

    async def settings(self) -> dict[str, Any]:
        return self.system.settings.public() | {"token_required": bool(self.system.cfg.api_token)}

    # -- writes -------------------------------------------------------------

    async def update_settings(self, changes: dict[str, Any], confirm: str = "") -> dict[str, Any]:
        try:
            await self.system.update_settings(changes, confirm)
        except SettingsError as exc:
            raise ApiError(400, str(exc)) from exc
        return await self.settings()

    async def clear_credentials(self) -> dict[str, Any]:
        await self.system.clear_credentials()
        return await self.settings()

    async def test_delta(self, env: str | None = None, api_key: str = "", api_secret: str = "") -> dict[str, Any]:
        return await self.system.test_delta(env, api_key.strip(), api_secret.strip())

    async def toggle(self, key: str, enabled: bool) -> dict[str, Any]:
        try:
            self.system.set_toggle(key, enabled)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        return {"key": key, "enabled": enabled}

    async def close_all(self) -> dict[str, Any]:
        return await self.system.close_all()

    async def close_position(self, broker: str, symbol: str) -> dict[str, Any]:
        """Closes on the named broker only: a click on one account never touches another."""
        return await self._call(self._broker(broker).close_position(symbol))

    async def protection(self, broker: str, symbol: str, stop_loss: float, take_profit: float | None = None) -> dict[str, Any]:
        if not stop_loss or stop_loss <= 0:
            raise ApiError(400, "a stop loss is required: a position is never left without one")
        async with self.system.protection_lock:
            await self._call(self._broker(broker).update_protection(symbol, stop_loss, take_profit or None))
        return {"broker": broker, "symbol": symbol, "stop_loss": stop_loss, "take_profit": take_profit or None}

    async def place_order(
        self, brokers: list[str], symbol: str, side: str, request_id: str, stop_loss: float | None = None,
        take_profit: float | None = None, size: int | None = None, margin_pct: float | None = None,
    ) -> dict[str, Any]:
        """One ticket, one or more brokers. With margin_pct each broker sizes from its own available
        margin, so paper and Delta open the same position as a share of their capital. Each broker is
        gated and recorded on its own; one refusing never stops another. The outcomes arrive as
        OrderPlaced / OrderFailed / OrderUnknown."""
        if not brokers:
            raise ApiError(400, "choose at least one broker")
        if size is not None and len(brokers) > 1:
            raise ApiError(400, "contracts mean different exposure on each broker: size several brokers by margin_pct")
        for name in brokers:
            self._broker(name)
        orders, errors = [], {}
        for name in dict.fromkeys(brokers):
            try:
                orders.append(await self.system.trader.manual(name, symbol, side, size, stop_loss, take_profit, request_id, margin_pct))
            except TradeRefused as exc:
                errors[name] = str(exc)
        if not orders:
            raise ApiError(409, "; ".join(f"{b}: {e}" for b, e in errors.items()))
        return {"orders": orders, "errors": errors}

    async def order_ticket(self, symbol: str, side: str = "buy", margin_pct: float | None = None) -> dict[str, Any]:
        """Defaults for the order form, and what the ticket would open on each active broker."""
        s = self.system.settings
        price = self.system.prices.price(symbol)
        if price is None:
            raise ApiError(409, f"no fresh live price for {symbol}")
        spec = await self._call(self.system.market_client.product(symbol))
        tick = float(spec.get("tick_size") or 0.5)
        cv = float(spec.get("contract_value") or 1.0)
        pct = s.trade_margin_pct if margin_pct is None else margin_pct
        d = 1 if side == "buy" else -1
        brokers = []
        for name, b in self.system.active.items():
            row: dict[str, Any] = {"broker": name, "real_money": name == "delta" and s.is_real_money,
                                   "trading": self.system.trading_on(name), "blocked": self.system.guard.halt_reason(name) or b.not_ready()}
            try:
                account = await b.account()
                size = await b.size_for_margin(symbol, price, account.available * pct / 100)
                row |= {"available": account.available, "currency": account.currency, "size": size, "notional": size * cv * price,
                        "margin": account.available * pct / 100, "error": ""}
            except BrokerError as exc:
                row |= {"available": None, "size": 0, "notional": 0, "margin": 0, "error": str(exc)}
            brokers.append(row)
        return {
            "symbol": symbol, "side": side, "price": price, "tick_size": tick, "contract_value": cv, "margin_pct": pct,
            "stop_loss": float(round_to_tick(price * (1 - d * s.stop_loss_pct / 100), tick)),
            "take_profit": float(round_to_tick(price * (1 + d * s.take_profit_pct / 100), tick)),
            "stats": (self.system.prices.snapshot().get(symbol) or {}).get("stats"), "brokers": brokers,
        }

    async def set_position_control(self, broker: str, symbol: str, trailing: bool | None = None, max_steps: int | None = None) -> dict[str, Any]:
        """Per-position trailing: switch it on or off, or change how many times it may trail."""
        self._broker(broker)
        changes: dict[str, Any] = {}
        if trailing is not None:
            changes["trailing"] = bool(trailing)
        if max_steps is not None:
            if isinstance(max_steps, bool) or int(max_steps) != max_steps or not 0 <= max_steps <= 50:
                raise ApiError(400, "max trails must be a whole number from 0 to 50")
            changes["max_steps"] = int(max_steps)
        if not changes:
            raise ApiError(400, "nothing to change")
        if not self.system.store.update_control(broker, symbol, **changes):
            raise ApiError(404, f"no tracked {symbol} position on {broker} yet — the guard picks new positions up within seconds")
        return self.system.store.controls(broker)[symbol]

    async def risk(self) -> dict[str, Any]:
        s = self.system.settings
        return {
            "brokers": [self.system.guard.status.get(name, {"broker": name}) for name in s.active_brokers],
            "trailing": {k: getattr(s, k) for k in ("trailing_enabled", "trailing_trigger_pct", "trailing_extend_pct", "trailing_lock_pct", "trailing_max_steps")},
            "daily_loss_limit_pct": s.daily_loss_limit_pct, "day_timezone": s.day_timezone,
        }

    async def risk_resume(self, broker: str) -> dict[str, Any]:
        """Lift today's daily-loss halt on one broker. Its day restarts from the current equity."""
        from tradebuddy.guard import trading_day

        b = self._broker(broker)
        day = trading_day(self.system.settings.day_timezone)
        if not self.system.store.day_risk(broker, day):
            raise ApiError(404, f"{broker} has no risk record for {day}")
        account = await self._call(b.account())
        self.system.store.resume_day(broker, day, account.equity)
        await self.system.guard.check()
        return await self.risk()

    async def paper_reset(self) -> dict[str, Any]:
        self.system.paper.reset()
        return self.system.paper.stats()


class RemoteApi:
    """Same methods as Api, answered by the engine process over ZeroMQ."""

    def __init__(self, client: RpcClient, local_metrics=None) -> None:
        self.client = client
        self.local_metrics = local_metrics  # the web process adds its own load to /metrics

    def __getattr__(self, method: str):
        if method not in METHODS:
            raise AttributeError(method)

        async def call(**params: Any) -> Any:
            try:
                result = await self.client.call(method, **params)
            except RpcError as exc:
                raise ApiError(exc.status, exc.detail) from exc
            if method == "metrics" and self.local_metrics:
                result["processes"] = [*result.get("processes", []), self.local_metrics()]
            return result

        return call
