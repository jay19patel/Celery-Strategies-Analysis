"""Delta Exchange India REST API client wrapper.

Handles API authentication, order placement, bracket orders (SL/TP),
positions, and emergency exit.
"""

import logging
from functools import wraps
from typing import Any

try:
    from delta_rest_client import DeltaRestClient
    from delta_rest_client.delta_rest_client import OrderType as DeltaOrderType
except ImportError:
    DeltaRestClient = None
    DeltaOrderType = None

from app.core.settings import settings

logger = logging.getLogger(__name__)


class DeltaAPIError(Exception):
    """Base exception for Delta API errors."""


class OrderPlacementError(DeltaAPIError):
    """Raised when placing an order fails."""


class BalanceError(DeltaAPIError):
    """Raised when fetching wallet balance fails."""


class PositionError(DeltaAPIError):
    """Raised when querying or closing positions fails."""


def handle_api_errors(func):
    """Decorator for consistent error handling and logging across Delta API calls."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except DeltaAPIError:
            raise
        except Exception as exc:
            logger.exception(f"Error in Delta API method {func.__name__}")
            raise DeltaAPIError(f"{func.__name__} failed: {exc}") from exc
    return wrapper


class DeltaClient:
    """Production-grade wrapper for Delta Exchange India REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        client_id: int | None = None,
    ) -> None:
        """Initialize DeltaClient with credentials from settings or arguments."""
        self.base_url = base_url or settings.delta_base_url
        self.api_key = api_key or settings.delta_api_key
        self.api_secret = api_secret or settings.delta_api_secret
        self.client_id = client_id if client_id is not None else settings.delta_client_id

        self._client: DeltaRestClient | None = None
        if self.is_configured:
            self._init_client()

    @property
    def is_configured(self) -> bool:
        """Check whether Delta Exchange API credentials have been provided and library is available."""
        if DeltaRestClient is None:
            return False
        return bool(self.api_key and self.api_secret and self.base_url)

    def _init_client(self) -> None:
        """Instantiate DeltaRestClient."""
        if DeltaRestClient is None:
            raise DeltaAPIError("delta-rest-client package is not installed in the runtime environment.")
        try:
            self._client = DeltaRestClient(
                base_url=self.base_url,
                api_key=self.api_key,
                api_secret=self.api_secret,
            )
            logger.info("delta_client_initialized")
        except Exception as exc:
            logger.exception("Failed to initialize DeltaRestClient")
            raise DeltaAPIError(f"Delta initialization failed: {exc}") from exc

    def _ensure_connected(self) -> None:
        """Ensure client is configured and initialized."""
        if not self.is_configured:
            raise DeltaAPIError(
                "Delta Exchange credentials (DELTA_API_KEY, DELTA_API_SECRET) are not configured."
            )
        if self._client is None:
            self._init_client()

    @handle_api_errors
    def get_ticker(self, symbol: str) -> dict[str, Any]:
        """Fetch current ticker, mark price, and contract details for a symbol."""
        self._ensure_connected()
        if not symbol:
            raise ValueError("Symbol is required")

        clean_symbol = symbol.replace("-", "").upper()
        response = self._client.get_ticker(clean_symbol)
        if not response:
            raise DeltaAPIError(f"No ticker data received for {clean_symbol}")
        return response

    @handle_api_errors
    def get_balance(self) -> dict[str, Any]:
        """Fetch USD and INR wallet balances."""
        self._ensure_connected()
        response = self._client.request(method="GET", path="/v2/wallet/balances", auth=True)
        data = response.json()

        if not data or "result" not in data or not data["result"]:
            raise BalanceError("No balance data received from Delta API")

        usd_asset: dict[str, Any] | None = None
        for asset in data["result"]:
            if asset.get("asset_symbol") == "USD":
                usd_asset = asset
                break

        if not usd_asset:
            return {
                "available_balance_usd": 0.0,
                "available_balance_inr": 0.0,
            }

        return {
            "available_balance_usd": float(usd_asset.get("available_balance", 0.0)),
            "available_balance_inr": float(usd_asset.get("available_balance_inr", 0.0)),
        }

    @handle_api_errors
    def get_all_open_positions(self) -> list[dict[str, Any]]:
        """Fetch all active margined positions."""
        self._ensure_connected()
        response = self._client.request(method="GET", path="/v2/positions/margined", auth=True)
        data = response.json()
        if not data or "result" not in data:
            return []

        cleaned: list[dict[str, Any]] = []
        for pos in data.get("result", []):
            raw_size = float(pos.get("size", 0.0))
            side = "BUY" if raw_size > 0 else ("SELL" if raw_size < 0 else "FLAT")
            pos_type = "LONG" if raw_size > 0 else ("SHORT" if raw_size < 0 else "FLAT")
            cleaned.append({
                "symbol": pos.get("product_symbol"),
                "product_id": pos.get("product_id"),
                "size": abs(raw_size),
                "raw_size": raw_size,
                "side": side,
                "position_type": pos_type,
                "is_long": raw_size > 0,
                "entry_price": float(pos.get("entry_price", 0.0)),
                "mark_price": float(pos.get("mark_price", 0.0)),
                "liquidation_price": float(pos.get("liquidation_price", 0.0)),
                "leverage": pos.get("product", {}).get("default_leverage", "N/A"),
                "margin": float(pos.get("margin", 0.0)),
                "unrealized_pnl": float(pos.get("unrealized_pnl", 0.0)),
                "realized_pnl": float(pos.get("realized_pnl", 0.0)),
                "created_at": pos.get("created_at"),
            })
        return cleaned

    @handle_api_errors
    def get_active_position(self, symbol: str) -> dict[str, Any] | None:
        """Fetch open position for a specific symbol if one exists."""
        clean_symbol = symbol.replace("-", "").upper()
        positions = self.get_all_open_positions()
        for pos in positions:
            if pos.get("symbol") == clean_symbol:
                return pos
        return None

    @handle_api_errors
    def get_all_open_orders(self) -> list[dict[str, Any]]:
        """Fetch all live and unfilled orders."""
        self._ensure_connected()
        response = self._client.get_live_orders()
        orders = response if isinstance(response, list) else []

        cleaned: list[dict[str, Any]] = []
        for o in orders:
            is_bracket = bool(o.get("bracket_order"))
            stop_price = o.get("stop_price")
            reduce_only = bool(o.get("reduce_only"))
            side = (o.get("side") or "").upper()

            if is_bracket or stop_price is not None:
                order_role = "STOP_LOSS" if stop_price else "TAKE_PROFIT"
            elif reduce_only:
                order_role = "EXIT"
            else:
                order_role = "ENTRY"

            cleaned.append({
                "id": o.get("id"),
                "product_id": o.get("product_id"),
                "symbol": o.get("product_symbol"),
                "side": side,
                "order_role": order_role,
                "size": float(o.get("size", 0.0)),
                "unfilled_size": float(o.get("unfilled_size", 0.0)),
                "order_type": o.get("order_type"),
                "limit_price": o.get("limit_price"),
                "stop_price": stop_price,
                "state": o.get("state"),
                "reduce_only": reduce_only,
                "bracket_order": is_bracket,
                "created_at": o.get("created_at"),
            })
        return cleaned

    @handle_api_errors
    def is_already_in_position_or_order(self, symbol: str) -> bool:
        """Checks whether the symbol already has an open position or live order."""
        clean_symbol = symbol.replace("-", "").upper()
        if self.get_active_position(clean_symbol) is not None:
            return True

        orders = self.get_all_open_orders()
        for o in orders:
            if o.get("symbol") == clean_symbol:
                return True
        return False

    @handle_api_errors
    def create_entry(
        self,
        product_id: int,
        size: float,
        side: str,
        entry_price: float,
        leverage: int,
    ) -> dict[str, Any]:
        """Creates a market entry order after setting desired leverage."""
        self._ensure_connected()
        if product_id <= 0 or size <= 0 or entry_price <= 0 or leverage <= 0:
            raise ValueError("Invalid parameters for create_entry")

        try:
            self._client.set_leverage(product_id, str(leverage))
            logger.info(f"Leverage set to {leverage}x for product {product_id}")
        except Exception as exc:
            raise OrderPlacementError(f"Failed to set leverage: {exc}") from exc

        try:
            order = self._client.place_order(
                product_id=product_id,
                size=size,
                side=side.lower(),
                order_type=DeltaOrderType.MARKET,
                limit_price=str(entry_price),
            )
            if not order or "id" not in order:
                raise OrderPlacementError("Order submitted but no order ID returned")

            return {
                "success": True,
                "order_id": order["id"],
                "product_id": product_id,
                "side": side.lower(),
                "size": size,
                "leverage": leverage,
                "raw": order,
            }
        except Exception as exc:
            raise OrderPlacementError(f"Failed to place entry order: {exc}") from exc

    @handle_api_errors
    def create_stoploss_target(
        self,
        product_id: int,
        symbol: str,
        stoploss_price: float,
        target_price: float,
    ) -> dict[str, Any]:
        """Creates a bracket order (Stop-Loss and Take-Profit) attached to a position."""
        self._ensure_connected()
        clean_symbol = symbol.replace("-", "").upper()

        payload = {
            "product_id": product_id,
            "product_symbol": clean_symbol,
            "stop_loss_order": {
                "order_type": "market_order",
                "stop_price": str(stoploss_price),
            },
            "take_profit_order": {
                "order_type": "market_order",
                "stop_price": str(target_price),
            },
            "bracket_stop_trigger_method": "last_traded_price",
        }

        response = self._client.request(
            method="POST",
            path="/v2/orders/bracket",
            payload=payload,
            auth=True,
        )
        result = response.json()
        if not result or "result" not in result:
            raise OrderPlacementError("Bracket order placement failed")

        return {
            "success": True,
            "bracket": result.get("result", {}),
        }

    @handle_api_errors
    def close_position(self, product_id: int, symbol: str) -> dict[str, Any]:
        """Market close an active position for a symbol using reduce_only."""
        self._ensure_connected()
        pos = self.get_active_position(symbol)
        if not pos:
            return {"success": False, "message": "Position not found"}

        size = float(pos.get("size", 0.0))
        if size == 0:
            return {"success": True, "message": "Position size is 0"}

        close_side = "sell" if size > 0 else "buy"
        res = self._client.place_order(
            product_id=product_id,
            size=abs(size),
            side=close_side,
            order_type=DeltaOrderType.MARKET,
            reduce_only=True,
        )
        return {"success": True, "order": res}

    @handle_api_errors
    def cancel_all_orders(self, product_id: int | None = None) -> dict[str, Any]:
        """Cancel all open orders, optionally filtered by product_id."""
        self._ensure_connected()
        live_orders = self.get_all_open_orders()
        if product_id:
            live_orders = [o for o in live_orders if o.get("product_id") == product_id]

        cancelled_count = 0
        failed_orders: list[dict[str, Any]] = []

        for o in live_orders:
            pid = o.get("product_id")
            oid = o.get("id")
            if pid and oid:
                try:
                    self._client.cancel_order(pid, oid)
                    cancelled_count += 1
                except Exception as exc:  # noqa: BLE001 - Best-effort cancellation across all live orders
                    failed_orders.append({"id": oid, "error": str(exc)})

        return {
            "success": len(failed_orders) == 0,
            "cancelled_count": cancelled_count,
            "failed_orders": failed_orders,
        }

    def emergency_exit(self) -> dict[str, Any]:
        """Emergency panic switch: cancels all orders and closes all open positions."""
        logger.warning("🚨 EMERGENCY EXIT INITIATED")
        result: dict[str, Any] = {
            "success": False,
            "orders_cancelled": 0,
            "positions_closed": False,
            "errors": [],
        }

        # Step 1: Cancel all live orders
        try:
            cancel_res = self.cancel_all_orders()
            result["orders_cancelled"] = cancel_res.get("cancelled_count", 0)
        except Exception as exc:  # noqa: BLE001 - Emergency panic must not raise; collect errors
            result["errors"].append(f"Failed to cancel orders: {exc}")

        # Step 2: Close all positions
        try:
            self._ensure_connected()
            payload = {
                "close_all_portfolio": True,
                "close_all_isolated": True,
                "user_id": self.client_id,
            }
            self._client.request(
                method="POST",
                path="/v2/positions/close_all",
                payload=payload,
                auth=True,
            )
            result["positions_closed"] = True
            logger.warning("🚨 All Delta positions successfully closed via close_all.")
        except Exception as exc:  # noqa: BLE001 - Emergency panic must not raise; collect errors
            result["errors"].append(f"Failed to close positions: {exc}")

        result["success"] = result["positions_closed"] and len(result["errors"]) == 0
        return result
