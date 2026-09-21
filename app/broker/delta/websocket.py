"""WebSocket client for Delta Exchange real-time order and position streams.

Subscribes to orders and positions and persists updates to SQLite.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

try:
    import websocket
except ImportError:
    websocket = None

from app.broker.delta.calculator import TradeCalculator
from app.broker.delta.client import DeltaClient
from app.core.settings import settings
from app.database.sqlite_db import get_sqlite_db

logger = logging.getLogger(__name__)


class DeltaWebSocketClient:
    """Manages real-time WebSocket connection to Delta Exchange India."""

    def __init__(
        self,
        websocket_url: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        on_order: Callable[[dict[str, Any]], None] | None = None,
        on_position: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Initialize WebSocket client."""
        self.ws_url = websocket_url or settings.delta_websocket_url
        self.api_key = api_key or settings.delta_api_key
        self.api_secret = api_secret or settings.delta_api_secret
        self.on_order = on_order
        self.on_position = on_position

        self.ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._running: bool = False
        self.db = get_sqlite_db()
        self.delta_client = DeltaClient()

    def start(self) -> None:
        """Start WebSocket listener in a background daemon thread."""
        if not self.api_key or not self.api_secret:
            logger.info("Delta API keys not set. Skipping WebSocket listener start.")
            return

        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="DeltaWebSocket")
        self._thread.start()
        logger.info("🔌 Delta WebSocket listener started in background thread.")

    def stop(self) -> None:
        """Stop WebSocket connection."""
        self._running = False
        if self.ws:
            self.ws.close()
        logger.info("🔌 Delta WebSocket listener stopped.")

    def _run_loop(self) -> None:
        """Connection and reconnect loop."""
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
                logger.exception("WebSocket loop exception")

            if self._running:
                time.sleep(5)

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        """Handle connection open event: subscribe to orders and positions."""
        logger.info("✅ Delta WebSocket connected. Sending subscription payload...")
        sub_payload = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {"name": "orders", "symbols": ["all"]},
                    {"name": "positions", "symbols": ["all"]},
                ]
            }
        }
        ws.send(json.dumps(sub_payload))

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(message)
            channel = data.get("type", "")

            if channel == "orders":
                self._handle_orders(data.get("orders", []))
            elif channel == "positions":
                self._handle_positions(data.get("positions", []))

        except Exception:
            logger.exception("Error parsing WebSocket message")

    def _handle_orders(self, orders: list[dict[str, Any]]) -> None:
        """Process order updates and persist into SQLite live_orders table."""
        for o in orders:
            try:
                oid = str(o.get("id", ""))
                if not oid:
                    continue
                product_id = int(o.get("product_id", 0))
                symbol = str(o.get("product_symbol", o.get("symbol", "")))
                side = str(o.get("side", ""))
                size = float(o.get("size", 0.0))
                order_type = str(o.get("order_type", "MARKET"))
                state = str(o.get("state", "OPEN"))
                now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

                sql = """
                    INSERT INTO live_orders (id, product_id, symbol, side, size, order_type, status, raw_data, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at, raw_data = excluded.raw_data;
                """
                self.db.execute_modify(sql, (oid, product_id, symbol, side, size, order_type, state, json.dumps(o), now_utc, now_utc))

                if self.on_order:
                    self.on_order(o)
            except Exception:
                logger.exception("Failed to handle order update")

    def _handle_positions(self, positions: list[dict[str, Any]]) -> None:
        """Process position updates, persist into SQLite, and attach bracket orders if needed."""
        for pos in positions:
            try:
                action = str(pos.get("action", "")).lower()
                symbol = str(pos.get("product_symbol", pos.get("symbol", "")))
                if not symbol:
                    continue

                if action == "delete":
                    self.db.execute_modify("DELETE FROM live_positions WHERE symbol = ?;", (symbol,))
                    logger.info(f"Position closed on Delta Exchange for {symbol}")
                    continue

                product_id = int(pos.get("product_id", 0))
                size = float(pos.get("size", 0.0))
                side = "buy" if size > 0 else "sell"
                entry_price = float(pos.get("entry_price", 0.0))
                mark_price = float(pos.get("mark_price", entry_price))
                liq_price = float(pos.get("liquidation_price", 0.0))
                leverage = int(pos.get("leverage", 1))
                unrealized = float(pos.get("unrealized_pnl", 0.0))
                realized = float(pos.get("realized_pnl", 0.0))
                now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

                sql = """
                    INSERT INTO live_positions (symbol, product_id, side, size, entry_price, mark_price, liquidation_price, leverage, unrealized_pnl, realized_pnl, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        size = excluded.size,
                        mark_price = excluded.mark_price,
                        unrealized_pnl = excluded.unrealized_pnl,
                        realized_pnl = excluded.realized_pnl,
                        updated_at = excluded.updated_at;
                """
                self.db.execute_modify(sql, (symbol, product_id, side, size, entry_price, mark_price, liq_price, leverage, unrealized, realized, now_utc, now_utc))

                # If brand new position created, automatically ensure bracket stoploss & target
                if action == "create" and entry_price > 0:
                    stop_target = TradeCalculator.calculate_stop_target(entry_price, side, liq_price)
                    self.delta_client.create_stoploss_target(
                        product_id=product_id,
                        symbol=symbol,
                        stoploss_price=stop_target["stop_loss"],
                        target_price=stop_target["target"],
                    )
                    logger.info(f"🛡️ Auto-bracket created for {symbol}: SL={stop_target['stop_loss']}, TP={stop_target['target']}")

                if self.on_position:
                    self.on_position(pos)
            except Exception:
                logger.exception("Failed to handle position update")

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        """Handle WebSocket error."""
        logger.error(f"Delta WebSocket error: {error}")

    def _on_close(self, ws: websocket.WebSocketApp, close_status_code: Any, close_msg: Any) -> None:
        """Handle WebSocket closure."""
        logger.info(f"Delta WebSocket connection closed: code={close_status_code}, msg={close_msg}")
