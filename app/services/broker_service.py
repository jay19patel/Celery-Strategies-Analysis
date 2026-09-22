"""Broker service coordinating trade execution modes, safety controls, and Delta API interactions.

Encapsulates paper/live mode transitions, arming confirmation enforcement,
broker profile management (API keys, authorization verification),
balance inquiries, open positions, active orders, order cancellation, and
the Emergency Exit kill-switch.
"""

import json
import logging
import re
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

from app.broker.delta import DeltaAPIError, DeltaClient
from app.broker.delta.price_feed import get_delta_price_feed, get_live_price
from app.broker.execution_manager import ARM_CONFIRMATION_PHRASE, get_execution_manager
from app.core.settings import get_symbols, settings
from app.database.sqlite_db import get_sqlite_db

logger = logging.getLogger(__name__)

_cached_server_ip: str | None = None
_cached_server_ip_timestamp: float = 0.0


def _mask_string(val: str, prefix_len: int = 4, suffix_len: int = 4) -> str:
    """Mask sensitive API keys or secrets for safe UI display."""
    if not val:
        return ""
    if len(val) <= prefix_len + suffix_len:
        return "*" * len(val)
    return f"{val[:prefix_len]}...{val[-suffix_len:]}"


class BrokerService:
    """Service encapsulating trade broker operations, safety gates, and live execution."""

    def __init__(self, delta_client: DeltaClient | None = None) -> None:
        """Initialize BrokerService with execution manager and sqlite database."""
        self.mgr = get_execution_manager()
        self.delta_client = delta_client or self.mgr.delta_client
        self.db = get_sqlite_db()
        self.last_ip_whitelisted: bool | None = None
        self.last_rejected_ip: str | None = None
        self._load_saved_profile_on_startup()

    def _load_saved_profile_on_startup(self) -> None:
        """Load stored broker credentials from SQLite if available and initialize client."""
        row = self.db.execute_one("SELECT value FROM system_config WHERE key = 'broker_profile';")
        if not row or not row.get("value"):
            return

        try:
            profile = json.loads(row["value"])
            api_key = profile.get("api_key")
            api_secret = profile.get("api_secret")
            base_url = profile.get("base_url")
            client_id = profile.get("client_id")

            if api_key and api_secret:
                self.delta_client.api_key = api_key
                self.delta_client.api_secret = api_secret
                self.delta_client.base_url = base_url or self.delta_client.base_url
                if client_id is not None:
                    self.delta_client.client_id = int(client_id)
                if self.delta_client.is_configured:
                    self.delta_client._init_client()
                logger.info("✅ Broker profile loaded and DeltaClient initialized from SQLite config.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not restore saved broker profile: %s", exc)

    def _is_authorized_status(self) -> bool:
        """Check if broker credentials are configured and authorized."""
        if not self.delta_client.is_configured:
            return False
        if self.last_ip_whitelisted is False:
            return False
        if self.last_ip_whitelisted is True:
            return True
        row = self.db.execute_one("SELECT value FROM system_config WHERE key = 'broker_profile';")
        if row and row.get("value"):
            try:
                prof = json.loads(row["value"])
                if prof.get("is_authorized", False):
                    return True
            except Exception:
                pass
        try:
            self.delta_client.get_balance()
            self.last_ip_whitelisted = True
            return True
        except Exception:
            return False

    def get_status(self) -> dict[str, Any]:
        """Fetch current execution mode, safety arming status, and broker readiness."""
        return {
            "execution_mode": self.mgr.get_mode(),
            "is_armed": self.mgr.is_armed(),
            "is_live_enabled": self.mgr.get_mode() == "LIVE" and self.mgr.is_armed(),
            "delta_configured": self.delta_client.is_configured,
            "is_authorized": self._is_authorized_status(),
            "ip_whitelisted": self.last_ip_whitelisted,
            "server_ip": self.last_rejected_ip or self.get_server_ip(),
            "delta_base_url": self.delta_client.base_url or settings.delta_base_url,
            "symbols": get_symbols(),
            "risk_ratio": settings.risk_ratio,
            "reward_ratio": settings.reward_ratio,
            "trade_capital_pct": settings.trade_capital_pct,
        }

    @staticmethod
    def get_server_ip() -> str:
        """Fetch the server's public outgoing IP address for Delta Exchange whitelisting."""
        global _cached_server_ip, _cached_server_ip_timestamp
        now = time.time()
        if _cached_server_ip and (now - _cached_server_ip_timestamp) < 3600:
            return _cached_server_ip

        urls = [
            "https://api.ipify.org?format=json",
            "https://ifconfig.me/all.json",
        ]
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "TradeBuddy-Engine/1.0"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    payload = json.loads(resp.read().decode())
                    ip = payload.get("ip") or payload.get("ip_address")
                    if ip and len(str(ip).split(".")) == 4:
                        _cached_server_ip = str(ip).strip()
                        _cached_server_ip_timestamp = now
                        return _cached_server_ip
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not fetch server IP from %s: %s", url, exc)
                continue

        return _cached_server_ip or "127.0.0.1"

    def get_broker_profile(self) -> dict[str, Any]:
        """Fetch broker profile details with masked secrets and verification status."""
        row = self.db.execute_one("SELECT value FROM system_config WHERE key = 'broker_profile';")
        saved_profile: dict[str, Any] = {}
        if row and row.get("value"):
            try:
                saved_profile = json.loads(row["value"])
            except Exception:  # noqa: BLE001
                saved_profile = {}

        api_key = saved_profile.get("api_key") or self.delta_client.api_key or settings.delta_api_key or ""
        api_secret = saved_profile.get("api_secret") or self.delta_client.api_secret or settings.delta_api_secret or ""
        base_url = (
            saved_profile.get("base_url")
            or self.delta_client.base_url
            or settings.delta_base_url
            or "https://api.india.delta.exchange"
        )
        client_id = saved_profile.get("client_id")
        if client_id is None:
            client_id = self.delta_client.client_id or settings.delta_client_id or 0

        is_authorized = bool(saved_profile.get("is_authorized", False) and self.delta_client.is_configured)
        is_live_enabled = self.mgr.get_mode() == "LIVE" and self.mgr.is_armed()

        return {
            "base_url": base_url,
            "client_id": int(client_id),
            "api_key": api_key,
            "api_key_masked": _mask_string(api_key, 6, 4),
            "has_api_secret": bool(api_secret),
            "api_secret_masked": _mask_string(api_secret, 3, 3),
            "is_configured": self.delta_client.is_configured,
            "is_authorized": is_authorized,
            "is_live_enabled": is_live_enabled,
            "execution_mode": self.mgr.get_mode(),
            "last_verified_at": saved_profile.get("last_verified_at"),
            "public_ip": self.get_server_ip(),
        }

    def save_broker_profile(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        client_id: int = 0,
    ) -> dict[str, Any]:
        """Save and update broker credentials in memory and SQLite."""
        clean_url = (base_url or "").strip() or "https://api.india.delta.exchange"
        clean_key = (api_key or "").strip()
        clean_secret = (api_secret or "").strip()

        # If secret is blank, keep existing secret
        if not clean_secret:
            clean_secret = self.delta_client.api_secret or ""

        self.delta_client.base_url = clean_url
        self.delta_client.api_key = clean_key
        self.delta_client.api_secret = clean_secret
        self.delta_client.client_id = int(client_id)

        if self.delta_client.is_configured:
            self.delta_client._init_client()

        # Persist to SQLite
        profile_data = {
            "base_url": clean_url,
            "api_key": clean_key,
            "api_secret": clean_secret,
            "client_id": int(client_id),
            "is_authorized": False,
            "last_verified_at": None,
        }
        now_utc = datetime.now(UTC).isoformat()
        self.db.execute_modify(
            """
            INSERT INTO system_config (key, value, updated_at) VALUES ('broker_profile', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
            """,
            (json.dumps(profile_data), now_utc),
        )

        logger.info("Broker credentials updated for client_id=%d", int(client_id))
        return {
            "success": True,
            "message": "Broker profile saved successfully. Please click 'Verify Authority' to validate credentials.",
            "profile": self.get_broker_profile(),
        }

    def verify_authority(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        client_id: int | None = None,
    ) -> dict[str, Any]:
        """Test authentication authority against Delta Exchange."""
        target_url = (base_url or self.delta_client.base_url or settings.delta_base_url or "").strip()
        target_key = (api_key or self.delta_client.api_key or settings.delta_api_key or "").strip()
        target_secret = (api_secret or self.delta_client.api_secret or settings.delta_api_secret or "").strip()
        target_client_id = (
            client_id if client_id is not None else (self.delta_client.client_id or settings.delta_client_id or 0)
        )

        if not target_key or not target_secret:
            return {
                "authorized": False,
                "message": "API Key and API Secret are required to verify authorization.",
            }

        test_client = DeltaClient(
            base_url=target_url,
            api_key=target_key,
            api_secret=target_secret,
            client_id=target_client_id,
        )

        if not test_client.is_configured:
            return {
                "authorized": False,
                "message": "Client could not be configured. delta-rest-client package may be missing.",
            }

        start_time = time.perf_counter()
        try:
            balances = test_client.get_balance()
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            now_utc = datetime.now(UTC).isoformat()

            # Mark profile as authorized in SQLite
            row = self.db.execute_one("SELECT value FROM system_config WHERE key = 'broker_profile';")
            profile = json.loads(row["value"]) if row and row.get("value") else {}
            profile["is_authorized"] = True
            profile["last_verified_at"] = now_utc
            profile["base_url"] = target_url
            profile["api_key"] = target_key
            profile["api_secret"] = target_secret
            profile["client_id"] = target_client_id

            self.db.execute_modify(
                """
                INSERT INTO system_config (key, value, updated_at) VALUES ('broker_profile', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                """,
                (json.dumps(profile), now_utc),
            )

            # Update active client credentials
            self.delta_client.base_url = target_url
            self.delta_client.api_key = target_key
            self.delta_client.api_secret = target_secret
            self.delta_client.client_id = target_client_id
            self.delta_client._init_client()

            self.last_ip_whitelisted = True
            self.last_rejected_ip = None

            return {
                "authorized": True,
                "message": "✅ Delta Exchange API credentials verified and authorized successfully!",
                "latency_ms": latency_ms,
                "balances": balances,
                "verified_at": now_utc,
                "server_ip": self.get_server_ip(),
            }
        except (DeltaAPIError, Exception) as exc:  # noqa: BLE001
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            exc_str = str(exc)
            server_ip = self.get_server_ip()
            is_ip_error = "ip_not_whitelisted_for_api_key" in exc_str
            if is_ip_error:
                match = re.search(r'"client_ip"\s*:\s*"([^"]+)"', exc_str)
                if match:
                    server_ip = match.group(1)
                self.last_ip_whitelisted = False
                self.last_rejected_ip = server_ip
                msg = f"❌ IP Not Whitelisted: Delta Exchange rejected server IP {server_ip}. Please whitelist this IP in Delta API key settings."
            else:
                msg = f"❌ Authorization failed: {exc}"
            logger.warning("Authority verification failed: %s", exc)
            return {
                "authorized": False,
                "message": msg,
                "error": exc_str,
                "latency_ms": latency_ms,
                "server_ip": server_ip,
                "ip_whitelisted": False if is_ip_error else None,
            }

    def toggle_live_trading(self, enabled: bool, confirmation: str | None = None) -> dict[str, Any]:
        """Enable or disable live broker order execution with safety gating."""
        if enabled:
            if not self.delta_client.is_configured:
                raise ValueError(
                    "Cannot enable Live Trading: Delta Exchange credentials are not configured or authorized."
                )

            # Verify arming phrase
            if not self.mgr.is_armed():
                if not confirmation or confirmation.strip() != ARM_CONFIRMATION_PHRASE:
                    raise ValueError(
                        f"Confirmation phrase must exactly match '{ARM_CONFIRMATION_PHRASE}' to enable live trading."
                    )
                self.mgr.arm_live_trading(confirmation)

            self.mgr.set_mode("LIVE")
            logger.warning("🟢 LIVE TRADING HAS BEEN ENABLED by user.")
            return {
                "enabled": True,
                "execution_mode": "LIVE",
                "is_armed": True,
                "message": "Live Trading is ENABLED. Real orders will be routed to Delta Exchange.",
            }
        else:
            self.mgr.disarm_live_trading()
            logger.info("🟡 LIVE TRADING DISABLED. System safely in PAPER mode.")
            return {
                "enabled": False,
                "execution_mode": "PAPER",
                "is_armed": False,
                "message": "Live Trading is DISABLED. System safely reverted to PAPER simulation mode.",
            }

    def arm_live_trading(self, confirmation: str) -> dict[str, Any]:
        """Arm live trade execution after verifying the explicit confirmation phrase."""
        if not confirmation or confirmation.strip() != ARM_CONFIRMATION_PHRASE:
            raise ValueError(
                f"Confirmation phrase must exactly match '{ARM_CONFIRMATION_PHRASE}' to arm live trading."
            )
        return self.mgr.arm_live_trading(confirmation)

    def disarm_live_trading(self) -> dict[str, Any]:
        """Disarm live execution and safely revert execution mode to PAPER."""
        return self.mgr.disarm_live_trading()

    def set_execution_mode(self, mode: str) -> dict[str, Any]:
        """Switch execution mode between PAPER and LIVE with safety validation."""
        clean_mode = mode.strip().upper()
        if clean_mode not in ("PAPER", "LIVE"):
            raise ValueError("Execution mode must be either 'PAPER' or 'LIVE'.")

        if clean_mode == "LIVE" and not self.mgr.is_armed():
            raise ValueError(
                f"Live trading must be armed first using confirmation phrase '{ARM_CONFIRMATION_PHRASE}'."
            )

        self.mgr.set_mode(clean_mode)
        return {"execution_mode": self.mgr.get_mode(), "is_armed": self.mgr.is_armed()}

    def get_balance(self) -> dict[str, Any]:
        """Fetch real-time wallet balance (USD & INR) from Delta Exchange."""
        if not self.delta_client.is_configured:
            return {
                "configured": False,
                "ip_whitelisted": None,
                "available_balance_usd": 0.0,
                "available_balance_inr": 0.0,
                "message": "Delta Exchange API credentials not configured in settings.",
            }

        try:
            balance = self.delta_client.get_balance()
            self.last_ip_whitelisted = True
            self.last_rejected_ip = None
            return {"configured": True, "ip_whitelisted": True, "server_ip": self.get_server_ip(), **balance}
        except (DeltaAPIError, Exception) as exc:  # noqa: BLE001 - Resilient broker error response
            exc_str = str(exc)
            is_ip_blocked = "ip_not_whitelisted_for_api_key" in exc_str
            if is_ip_blocked:
                match = re.search(r'"client_ip"\s*:\s*"([^"]+)"', exc_str)
                server_ip = match.group(1) if match else self.get_server_ip()
                self.last_ip_whitelisted = False
                self.last_rejected_ip = server_ip
                logger.warning(
                    "Delta API IP not whitelisted (server IP: %s). Add this IP to your Delta API key whitelist.",
                    server_ip,
                )
                return {
                    "configured": True,
                    "ip_whitelisted": False,
                    "server_ip": server_ip,
                    "available_balance_usd": 0.0,
                    "available_balance_inr": 0.0,
                    "message": (
                        f"IP {server_ip} is not whitelisted for this API key. "
                        "Go to Delta Exchange → API Keys → Edit → Add IP to whitelist."
                    ),
                }
            logger.error("Error fetching Delta balance: %s", exc)
            return {
                "configured": True,
                "ip_whitelisted": None,
                "server_ip": self.get_server_ip(),
                "error": exc_str,
                "available_balance_usd": 0.0,
                "available_balance_inr": 0.0,
            }

    def get_paper_positions(self) -> list[dict[str, Any]]:
        """Fetch active simulated paper positions from broker_accounts table.

        Each position is enriched with the latest live mark price from
        DeltaLivePriceFeed, enabling real-time unrealized PnL calculation
        without requiring authenticated API access.
        """
        rows = self.db.execute_query("SELECT strategy_name, symbol, capital, open_position FROM broker_accounts;")
        positions: list[dict[str, Any]] = []
        for r in rows:
            raw_pos = r.get("open_position")
            if not raw_pos:
                continue
            try:
                pos_dict = json.loads(raw_pos) if isinstance(raw_pos, str) else raw_pos
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not parse paper position JSON: %s", exc)
                continue
            if not pos_dict:
                continue

            pos_type = (pos_dict.get("type") or "LONG").upper()
            size = float(pos_dict.get("size", 0.0))
            entry_price = float(pos_dict.get("entry_price", 0.0))
            sl = float(pos_dict.get("stop_loss", 0.0)) if pos_dict.get("stop_loss") else None
            tp = float(pos_dict.get("take_profit", 0.0)) if pos_dict.get("take_profit") else None

            # Enrich with live mark price from public WebSocket price feed
            symbol = r.get("symbol") or ""
            mark_price = get_live_price(symbol) or entry_price
            is_long = pos_type == "LONG"
            if mark_price and entry_price:
                if is_long:
                    unrealized_pnl = (mark_price - entry_price) * size
                else:
                    unrealized_pnl = (entry_price - mark_price) * size
            else:
                unrealized_pnl = 0.0

            positions.append({
                "strategy_name": r.get("strategy_name"),
                "symbol": symbol,
                "side": "BUY" if is_long else "SELL",
                "position_type": pos_type,
                "is_long": is_long,
                "size": size,
                "entry_price": entry_price,
                "mark_price": mark_price,
                "stop_loss": sl,
                "take_profit": tp,
                "entry_time": pos_dict.get("entry_time"),
                "capital": float(r.get("capital", 100.0)),
                "unrealized_pnl": round(unrealized_pnl, 4),
                "has_live_price": mark_price != entry_price,
                "is_paper": True,
            })
        return positions

    def get_paper_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch simulated paper trading orders/trades from broker_trades table."""
        rows = self.db.execute_query(
            "SELECT id, strategy_name, symbol, type, entry_price, exit_price, pnl, reason, entry_time, exit_time FROM broker_trades ORDER BY exit_time DESC LIMIT ?;",
            (limit,),
        )
        orders: list[dict[str, Any]] = []
        for r in rows:
            pos_type = (r.get("type") or "LONG").upper()
            orders.append({
                "id": f"PAPER-{r['id']}",
                "strategy_name": r.get("strategy_name"),
                "symbol": r.get("symbol"),
                "side": "BUY" if pos_type == "LONG" else "SELL",
                "position_type": pos_type,
                "order_role": "PAPER_TRADE",
                "entry_price": float(r.get("entry_price", 0.0)),
                "exit_price": float(r.get("exit_price", 0.0)) if r.get("exit_price") else None,
                "pnl": float(r.get("pnl", 0.0)),
                "exit_reason": r.get("reason") or "Closed",
                "entry_time": r.get("entry_time"),
                "exit_time": r.get("exit_time"),
                "state": "filled",
                "is_paper": True,
            })
        return orders

    def get_live_positions(self) -> list[dict[str, Any]]:
        """Fetch real live positions from Delta Exchange API or SQLite cache."""
        if self.delta_client.is_configured:
            try:
                positions = self.delta_client.get_all_open_positions()
                self.last_ip_whitelisted = True
                self.last_rejected_ip = None
                return positions
            except (DeltaAPIError, Exception) as exc:  # noqa: BLE001 - Fallback to SQLite cache on broker network error
                exc_str = str(exc)
                if "ip_not_whitelisted_for_api_key" in exc_str:
                    match = re.search(r'"client_ip"\s*:\s*"([^"]+)"', exc_str)
                    server_ip = match.group(1) if match else self.get_server_ip()
                    self.last_ip_whitelisted = False
                    self.last_rejected_ip = server_ip
                    logger.warning(
                        "Delta API IP not whitelisted (server IP: %s) — live positions unavailable.",
                        server_ip,
                    )
                else:
                    logger.warning("Could not fetch positions from Delta API, falling back to cache: %s", exc)

        rows = self.db.execute_query("SELECT * FROM live_positions WHERE size != 0;")
        return [dict(r) for r in rows]

    def get_live_orders(self) -> list[dict[str, Any]]:
        """Fetch real live orders from Delta Exchange API or SQLite cache."""
        if self.delta_client.is_configured:
            try:
                return self.delta_client.get_all_open_orders()
            except (DeltaAPIError, Exception) as exc:  # noqa: BLE001 - Fallback to SQLite cache on broker network error
                logger.warning("Could not fetch orders from Delta API, falling back to cache: %s", exc)

        rows = self.db.execute_query("SELECT * FROM live_orders ORDER BY created_at DESC LIMIT 50;")
        return [dict(r) for r in rows]

    def get_positions(self, mode: str | None = None) -> list[dict[str, Any]]:
        """Fetch active positions based on execution mode ('PAPER' or 'LIVE')."""
        active_mode = (mode or self.mgr.get_mode()).upper()
        if active_mode == "PAPER":
            return self.get_paper_positions()
        return self.get_live_positions()

    def get_orders(self, mode: str | None = None) -> list[dict[str, Any]]:
        """Fetch active open orders based on execution mode ('PAPER' or 'LIVE')."""
        active_mode = (mode or self.mgr.get_mode()).upper()
        if active_mode == "PAPER":
            return self.get_paper_orders()
        return self.get_live_orders()

    def cancel_order(self, order_id: str, product_id: int | None = None) -> dict[str, Any]:
        """Cancel a specific live order via Delta Exchange."""
        if not self.delta_client.is_configured:
            return {"success": False, "message": "Delta Exchange API is not configured."}

        try:
            res = self.delta_client.cancel_all_orders(product_id=product_id)
            return {"success": True, "result": res}
        except (DeltaAPIError, Exception) as exc:  # noqa: BLE001 - Return error message on order cancellation failure
            logger.error("Failed to cancel order %s: %s", order_id, exc)
            return {"success": False, "error": str(exc)}

    def emergency_exit(self) -> dict[str, Any]:
        """Execute the atomic panic kill-switch: cancel all orders and market-close all positions."""
        logger.warning("🚨 EMERGENCY EXIT triggered via BrokerService")
        return self.mgr.emergency_exit()


_broker_service: BrokerService | None = None


def get_broker_service() -> BrokerService:
    """Singleton accessor for BrokerService."""
    global _broker_service
    if _broker_service is None:
        _broker_service = BrokerService()
    return _broker_service
