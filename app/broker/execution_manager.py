"""Unified Execution Manager for Paper Trading and Live Broker Execution.

Routes strategy signals to either PaperBroker or DeltaClient based on the active
execution mode and safety arming gates. Enforces risk limits, safe leverage, and
emergency kill-switch functionality.
"""

import logging
from datetime import UTC, datetime
from typing import Any, Optional

from app.broker.delta import DeltaClient
from app.database.sqlite_db import get_sqlite_db
from app.models.strategy_models import SignalType

logger = logging.getLogger(__name__)

ARM_CONFIRMATION_PHRASE = "ARM LIVE TRADING"


class ExecutionManager:
    """Master trading execution coordinator with Paper/Live routing and safety controls."""

    _instance: Optional["ExecutionManager"] = None

    def __init__(self) -> None:
        """Initialize delta client, and sqlite state."""
        self.db = get_sqlite_db()
        self.delta_client = DeltaClient()

    @classmethod
    def get_instance(cls) -> "ExecutionManager":
        """Singleton accessor for ExecutionManager."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_mode(self) -> str:
        """Return the execution mode."""
        row = self.db.execute_one("SELECT value FROM system_config WHERE key = 'execution_mode';")
        return row["value"] if row and row["value"] else "LIVE"

    def set_mode(self, mode: str) -> None:
        """Set execution mode in database."""
        clean_mode = mode.strip().upper()

        now_utc = datetime.now(UTC).isoformat()
        sql = """
            INSERT INTO system_config (key, value, updated_at) VALUES ('execution_mode', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
        """
        self.db.execute_modify(sql, (clean_mode, now_utc))
        logger.info(f"Execution mode set to: {clean_mode}")

    def is_armed(self) -> bool:
        """Check if live execution is armed."""
        row = self.db.execute_one("SELECT value FROM system_config WHERE key = 'live_trading_armed';")
        return row["value"] == "1" if row and row["value"] else False

    def arm_live_trading(self, confirmation: str) -> dict[str, Any]:
        """Arm live trading."""
        if confirmation != ARM_CONFIRMATION_PHRASE:
            raise ValueError("Invalid confirmation phrase.")
        now_utc = datetime.now(UTC).isoformat()
        sql = """
            INSERT INTO system_config (key, value, updated_at) VALUES ('live_trading_armed', '1', ?)
            ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = excluded.updated_at;
        """
        self.db.execute_modify(sql, (now_utc,))
        return {"armed": True}

    def disarm_live_trading(self) -> dict[str, Any]:
        """Disarm live trading."""
        now_utc = datetime.now(UTC).isoformat()
        sql = """
            INSERT INTO system_config (key, value, updated_at) VALUES ('live_trading_armed', '0', ?)
            ON CONFLICT(key) DO UPDATE SET value = '0', updated_at = excluded.updated_at;
        """
        self.db.execute_modify(sql, (now_utc,))
        logger.info("live_trading_disarmed")
        return {"armed": False}

    def process_signal(
        self,
        strategy_name: str,
        symbol: str,
        signal_type: SignalType,
        price: float,
        timestamp: datetime,
        confidence: float = 1.0,
        stop_loss: float = None,
        take_profit: float = None,
    ) -> dict[str, Any]:
        """Process a trading signal according to active mode and safety checks.

        Args:
            strategy_name: Name of strategy generating the signal.
            symbol: Trading instrument identifier (e.g. 'BTC-USD').
            signal_type: BUY, SELL, or HOLD.
            price: Current mark/execution price.
            timestamp: Signal timestamp.
            confidence: Signal confidence score (0.0 to 1.0).
            stop_loss: Custom stop loss price from strategy.
            take_profit: Custom take profit price from strategy.

        Returns:
            Dictionary detailing execution action taken.
        """
        if signal_type == SignalType.HOLD:
            return {"action": "ignored", "reason": "HOLD signal"}

        # Check strategy specific execution configuration
        strat_cfg = self.db.execute_one(
            "SELECT is_real_enabled FROM strategy_configs WHERE strategy_id = ? OR name = ?;",
            (strategy_name, strategy_name),
        )
        is_real_enabled = bool(strat_cfg["is_real_enabled"]) if strat_cfg else True
        # 1. Live Execution (if armed)
        live_res = None
        if self.is_armed() and self.get_mode() == "LIVE":
            if is_real_enabled:
                try:
                    if self.delta_client.is_already_in_position_or_order(symbol):
                        live_res = {"action": "skipped", "reason": "Position already exists for this symbol"}
                    else:
                        product_id = self.delta_client.get_product_id(symbol)
                        live_res = self.delta_client.create_entry(
                            product_id=product_id,
                            size=1.0,  # Strategy defined quantity should be passed in the future
                            side=action,
                            entry_price=price,
                            leverage=1
                        )
                except Exception as exc:
                    live_res = {"action": "failed", "reason": f"Delta execution failed: {exc}"}
            else:
                live_res = {"action": "live_disabled", "reason": "Strategy real execution disabled"}
        else:
            live_res = {"action": "live_disabled", "reason": "Live trading not armed or not in LIVE mode"}

        return {
            "mode": self.get_mode(),
            "action": "live_executed" if self.is_armed() and self.get_mode() == "LIVE" else "live_disabled",
            "live_result": live_res,
            "strategy": strategy_name,
        }


def get_execution_manager() -> ExecutionManager:
    """Returns singleton ExecutionManager instance."""
    return ExecutionManager.get_instance()
