"""Everything the dashboard can ask or command, in one place.

The web layer calls these methods directly (single process) or through
ZeroMQ RPC (distributed): `RemoteApi` has the same methods, so routes do not
know which one they hold. Every method takes and returns plain JSON.
"""

from __future__ import annotations

import time
from typing import Any

from tradebuddy.brokers import Broker
from tradebuddy.codec import EVENT_TYPES
from tradebuddy.delta import round_to_tick
from tradebuddy.errors import BrokerError
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
    "set_position_control", "risk", "risk_resume",
)


class ApiError(RpcError):
    """An error with an HTTP status, raised the same way locally and over RPC."""


class Api:
    def __init__(self, system: System) -> None:
        self.system = system

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
