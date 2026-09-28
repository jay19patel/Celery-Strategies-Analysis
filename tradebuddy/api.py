"""Everything the dashboard can ask or command, in one place.

The web layer calls these methods directly (single process) or through
ZeroMQ RPC (distributed): `RemoteApi` has the same methods, so routes do not
know which one they hold. Every method takes and returns plain JSON.
"""

from __future__ import annotations

import time
from typing import Any

from tradebuddy.brokers import Broker
from tradebuddy.errors import BrokerError
from tradebuddy.settings import SettingsError
from tradebuddy.system import System
from tradebuddy.transport import RpcClient, RpcError

SIGNAL_EVENTS = ["SignalGenerated", "TradeSkipped", "StrategyError"]
ACTIVITY_EVENTS = [*SIGNAL_EVENTS, "OrderPlaced", "OrderFailed", "OrderUnknown", "PositionClosed"]

# Methods the engine answers over RPC. Anything else is refused.
METHODS = (
    "page_context", "header", "overview", "strategies", "signals", "positions", "orders", "open_orders", "account",
    "paper_stats", "paper_trades", "metrics", "events", "settings", "update_settings", "clear_credentials",
    "test_delta", "toggle", "close_all", "close_position", "protection", "paper_reset",
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
        }

    async def strategies(self) -> list[dict[str, Any]]:
        return self.system.strategies_view()

    async def signals(self, limit: int = 300, strategy: str = "", symbol: str = "") -> list[dict[str, Any]]:
        rows = self.system.store.recent_events(min(limit, 2000), types=SIGNAL_EVENTS)
        return [r for r in rows if (not strategy or r.get("strategy") == strategy) and (not symbol or r.get("symbol") == symbol)]

    async def positions(self, broker: str | None = None) -> list[dict[str, Any]]:
        return [p.to_dict() for p in await self._call(self._broker(broker).positions())]

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

    async def events(self, limit: int = 300, type: str = "") -> list[dict[str, Any]]:
        return self.system.store.recent_events(min(limit, 2000), types=[type] if type else None)

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
        return await self._call(self._broker(broker).close_position(symbol))

    async def protection(self, symbol: str, stop_loss: float, take_profit: float) -> dict[str, Any]:
        try:
            self.system.paper.update_protection(symbol, stop_loss, take_profit)
        except BrokerError as exc:
            raise ApiError(400, str(exc)) from exc
        return {"symbol": symbol, "stop_loss": stop_loss, "take_profit": take_profit}

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
