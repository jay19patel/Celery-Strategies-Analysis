"""Authenticated Delta Exchange WebSocket state for live orders and positions."""

import hashlib
import hmac
import json
import logging
import threading
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

try:
    import websocket
except ImportError:
    websocket = None

from app.core.settings import settings

logger = logging.getLogger(__name__)


class DeltaWebSocketClient:
    """Maintain live broker state in memory from Delta private channels."""

    def __init__(
        self,
        websocket_url: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        on_order: Callable[[dict[str, Any]], None] | None = None,
        on_position: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.ws_url = websocket_url or settings.delta_websocket_url
        self.api_key = api_key or settings.delta_api_key
        self.api_secret = api_secret or settings.delta_api_secret
        self.on_order = on_order
        self.on_position = on_position
        self.ws: Any = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._running = False
        self._is_connected = False
        self._messages_received = 0
        self._last_message_at: str | None = None
        self._last_error: str | None = None
        self._reconnect_count = 0
        self._orders: dict[str, dict[str, Any]] = {}
        self._positions: dict[str, dict[str, Any]] = {}
        self._subscribed_channels = ["orders", "positions"]

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret and websocket is not None)

    def configure(self, api_key: str | None, api_secret: str | None) -> None:
        """Apply credentials and restart an active stream when they change."""
        if (api_key, api_secret) == (self.api_key, self.api_secret):
            return
        was_running = self._running
        if was_running:
            self.stop()
        self.api_key, self.api_secret = api_key, api_secret
        self.clear()
        if was_running and self.is_configured:
            self.start()

    def start(self) -> None:
        """Start the private stream."""
        if not self.is_configured:
            logger.info("delta_private_stream_not_configured")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="DeltaPrivateStream")
        self._thread.start()
        logger.info("delta_private_stream_started")

    def stop(self) -> None:
        self._running = False
        if self.ws:
            self.ws.close()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None
        self._is_connected = False
        logger.info("delta_private_stream_stopped")

    def clear(self) -> None:
        with self._lock:
            self._orders.clear()
            self._positions.clear()

    def _run_loop(self) -> None:
        while self._running:
            try:
                self.ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception:
                logger.exception("delta_private_stream_loop_failed")
            if self._running:
                time.sleep(5)

    def _on_open(self, ws: Any) -> None:
        timestamp = str(int(time.time()))
        signature = hmac.new(self.api_secret.encode(), f"GET{timestamp}/live".encode(), hashlib.sha256).hexdigest()
        ws.send(
            json.dumps(
                {
                    "type": "key-auth",
                    "payload": {"api-key": self.api_key, "signature": signature, "timestamp": timestamp},
                }
            )
        )
        ws.send(
            json.dumps(
                {
                    "type": "subscribe",
                    "payload": {
                        "channels": [
                            {"name": "orders", "symbols": ["all"]},
                            {"name": "positions", "symbols": ["all"]},
                        ]
                    },
                }
            )
        )
        self._is_connected = True
        self._last_error = None
        logger.info("delta_private_stream_connected")

    def _on_message(self, ws: Any, message: str) -> None:
        self._messages_received += 1
        self._last_message_at = datetime.now(UTC).isoformat()
        try:
            data = json.loads(message)
            if data.get("type") == "orders":
                self._handle_orders(data.get("orders", []))
            elif data.get("type") == "positions":
                self._handle_positions(data.get("positions", []))
        except Exception:
            logger.exception("delta_private_message_invalid")

    def _handle_orders(self, orders: list[dict[str, Any]]) -> None:
        terminal_states = {"cancelled", "closed", "filled", "rejected"}
        for order in orders:
            order_id = str(order.get("id", ""))
            if not order_id:
                continue
            state = str(order.get("state", order.get("status", ""))).lower()
            with self._lock:
                if state in terminal_states:
                    self._orders.pop(order_id, None)
                else:
                    self._orders[order_id] = deepcopy(order)
            if self.on_order:
                self.on_order(order)

    def _handle_positions(self, positions: list[dict[str, Any]]) -> None:
        for position in positions:
            symbol = str(position.get("product_symbol") or position.get("symbol") or "")
            if not symbol:
                continue
            try:
                size = float(position.get("size", 0))
            except (TypeError, ValueError):
                size = 0.0
            with self._lock:
                if str(position.get("action", "")).lower() == "delete" or size == 0:
                    self._positions.pop(symbol, None)
                else:
                    self._positions[symbol] = deepcopy(position)
            if self.on_position:
                self.on_position(position)

    def get_orders(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(list(self._orders.values()))

    def get_positions(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(list(self._positions.values()))

    def _on_error(self, ws: Any, error: Exception) -> None:
        self._last_error = str(error)
        logger.error("delta_private_stream_error error=%s", error)

    def _on_close(self, ws: Any, close_status_code: Any, close_msg: Any) -> None:
        self._is_connected = False
        self._reconnect_count += 1
        self.clear()
        logger.info("delta_private_stream_closed code=%s message=%s", close_status_code, close_msg)

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            position_count, order_count = len(self._positions), len(self._orders)
        return {
            "is_configured": self.is_configured,
            "is_running": self._running,
            "is_connected": self._is_connected,
            "ws_url": self.ws_url,
            "subscribed_channels": self._subscribed_channels,
            "messages_received": self._messages_received,
            "last_message_at": self._last_message_at,
            "reconnect_count": self._reconnect_count,
            "last_error": self._last_error,
            "live_positions": position_count,
            "live_orders": order_count,
            "storage": "memory",
        }


_delta_ws_client: DeltaWebSocketClient | None = None


def get_delta_websocket_client() -> DeltaWebSocketClient:
    """Return the process-wide private Delta stream."""
    global _delta_ws_client
    if _delta_ws_client is None:
        _delta_ws_client = DeltaWebSocketClient()
    return _delta_ws_client
