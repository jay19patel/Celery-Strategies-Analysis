"""Unified Execution Manager for Paper Trading and Live Broker Execution.

Routes strategy signals to either PaperBroker or DeltaClient based on the active
execution mode and safety arming gates. Enforces risk limits, safe leverage, and
emergency kill-switch functionality.
"""

import logging
from datetime import UTC, datetime
from typing import Any, Optional

from app.broker.delta import DeltaClient
from app.core.paper_broker import PaperBroker
from app.database.sqlite_db import get_sqlite_db
from app.models.strategy_models import SignalType

logger = logging.getLogger(__name__)

ARM_CONFIRMATION_PHRASE = "ARM LIVE TRADING"


class ExecutionManager:
    """Master trading execution coordinator with Paper/Live routing and safety controls."""

    _instance: Optional["ExecutionManager"] = None

    def __init__(self) -> None:
        """Initialize paper broker, delta client, and sqlite state."""
        self.db = get_sqlite_db()
        self.paper_broker = PaperBroker()
        self.delta_client = DeltaClient()

    @classmethod
    def get_instance(cls) -> "ExecutionManager":
        """Singleton accessor for ExecutionManager."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_mode(self) -> str:
        """Return the only supported execution mode."""
        return "PAPER"

    def set_mode(self, mode: str) -> None:
        """Set execution mode in database."""
        clean_mode = mode.strip().upper()
        if clean_mode != "PAPER":
            raise ValueError("Delta is monitoring-only; execution mode must remain PAPER.")

        now_utc = datetime.now(UTC).isoformat()
        sql = """
            INSERT INTO system_config (key, value, updated_at) VALUES ('execution_mode', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
        """
        self.db.execute_modify(sql, (clean_mode, now_utc))
        logger.info(f"Execution mode set to: {clean_mode}")

    def is_armed(self) -> bool:
        """Live execution is permanently disabled in this paper-first system."""
        return False

    def arm_live_trading(self, confirmation: str) -> dict[str, Any]:
        """Reject real trading; Delta connectivity is read-only monitoring."""
        raise ValueError("Delta trading is disabled. Use the Paper Broker for all execution.")

    def disarm_live_trading(self) -> dict[str, Any]:
        """Disarm live trading and fall back to PAPER mode."""
        now_utc = datetime.now(UTC).isoformat()
        sql = """
            INSERT INTO system_config (key, value, updated_at) VALUES ('live_trading_armed', '0', ?)
            ON CONFLICT(key) DO UPDATE SET value = '0', updated_at = excluded.updated_at;
        """
        self.db.execute_modify(sql, (now_utc,))
        self.set_mode("PAPER")
        logger.info("live_trading_disarmed mode=PAPER")
        return {"armed": False, "mode": "PAPER"}

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
            "SELECT is_paper_enabled, is_real_enabled FROM strategy_configs WHERE strategy_id = ? OR name = ?;",
            (strategy_name, strategy_name),
        )
        is_paper_enabled = bool(strat_cfg["is_paper_enabled"]) if strat_cfg else True

        # 1. Paper Broker Execution (if enabled for this strategy)
        paper_res = None
        if is_paper_enabled:
            paper_res = self.paper_broker.process_signal(
                strategy_name, symbol, signal_type, price, timestamp, stop_loss=stop_loss, take_profit=take_profit
            )
        else:
            paper_res = {"action": "paper_disabled", "reason": "Paper trading disabled for this strategy"}

        if not is_paper_enabled:
            return {
                "mode": "LOG_ONLY",
                "action": "paper_disabled",
                "paper_result": paper_res,
                "live_result": None,
                "strategy": strategy_name,
            }

        return {
            "mode": "PAPER",
            "action": "paper_executed" if is_paper_enabled else "paper_disabled",
            "paper_result": paper_res,
            "live_result": {"action": "monitoring_only"},
            "strategy": strategy_name,
        }


def get_execution_manager() -> ExecutionManager:
    """Returns singleton ExecutionManager instance."""
    return ExecutionManager.get_instance()
