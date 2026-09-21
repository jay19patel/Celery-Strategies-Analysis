"""Unified Execution Manager for Paper Trading and Live Broker Execution.

Routes strategy signals to either PaperBroker or DeltaClient based on the active
execution mode and safety arming gates. Enforces risk limits, safe leverage, and
emergency kill-switch functionality.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any, Optional

from app.broker.delta import DeltaAPIError, DeltaClient, TradeCalculator
from app.core.paper_broker import PaperBroker
from app.core.settings import settings
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
        """Get current execution mode ('PAPER' or 'LIVE')."""
        row = self.db.execute_one("SELECT value FROM system_config WHERE key = 'execution_mode';")
        if row and row["value"]:
            return row["value"].upper()
        return settings.execution_mode.upper()

    def set_mode(self, mode: str) -> None:
        """Set execution mode in database."""
        clean_mode = mode.strip().upper()
        if clean_mode not in ("PAPER", "LIVE"):
            raise ValueError("Mode must be 'PAPER' or 'LIVE'")

        now_utc = datetime.now(UTC).isoformat()
        sql = """
            INSERT INTO system_config (key, value, updated_at) VALUES ('execution_mode', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
        """
        self.db.execute_modify(sql, (clean_mode, now_utc))
        logger.info(f"Execution mode set to: {clean_mode}")

    def is_armed(self) -> bool:
        """Check whether live trading is currently armed."""
        row = self.db.execute_one("SELECT value FROM system_config WHERE key = 'live_trading_armed';")
        if row and row["value"] == "1":
            return True
        return settings.live_trading_armed

    def arm_live_trading(self, confirmation: str) -> dict[str, Any]:
        """Arm live trading after validating confirmation phrase."""
        if confirmation != ARM_CONFIRMATION_PHRASE:
            raise ValueError(f"Confirmation phrase must exactly match '{ARM_CONFIRMATION_PHRASE}'")

        if not self.delta_client.is_configured:
            raise DeltaAPIError("Cannot arm live trading: Delta Exchange API keys are not configured.")

        now_utc = datetime.now(UTC).isoformat()
        sql = """
            INSERT INTO system_config (key, value, updated_at) VALUES ('live_trading_armed', '1', ?)
            ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = excluded.updated_at;
        """
        self.db.execute_modify(sql, (now_utc,))
        self.set_mode("LIVE")
        logger.warning("🛡️ LIVE TRADING ARMED AND ACTIVE.")
        return {"armed": True, "mode": "LIVE"}

    def disarm_live_trading(self) -> dict[str, Any]:
        """Disarm live trading and fall back to PAPER mode."""
        now_utc = datetime.now(UTC).isoformat()
        sql = """
            INSERT INTO system_config (key, value, updated_at) VALUES ('live_trading_armed', '0', ?)
            ON CONFLICT(key) DO UPDATE SET value = '0', updated_at = excluded.updated_at;
        """
        self.db.execute_modify(sql, (now_utc,))
        self.set_mode("PAPER")
        logger.info("🛡️ Live trading disarmed. Reverted to PAPER mode.")
        return {"armed": False, "mode": "PAPER"}

    def process_signal(
        self,
        strategy_name: str,
        symbol: str,
        signal_type: SignalType,
        price: float,
        timestamp: datetime,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        """Process a trading signal according to active mode and safety checks.

        Args:
            strategy_name: Name of strategy generating the signal.
            symbol: Trading instrument identifier (e.g. 'BTC-USD').
            signal_type: BUY, SELL, or HOLD.
            price: Current mark/execution price.
            timestamp: Signal timestamp.
            confidence: Signal confidence score (0.0 to 1.0).

        Returns:
            Dictionary detailing execution action taken.
        """
        if signal_type == SignalType.HOLD:
            return {"action": "ignored", "reason": "HOLD signal"}

        mode = self.get_mode()

        # Check strategy specific execution configuration
        strat_cfg = self.db.execute_one(
            "SELECT is_paper_enabled, is_real_enabled FROM strategy_configs WHERE strategy_id = ? OR name = ?;",
            (strategy_name, strategy_name),
        )
        is_paper_enabled = bool(strat_cfg["is_paper_enabled"]) if strat_cfg else True
        is_real_enabled = bool(strat_cfg["is_real_enabled"]) if strat_cfg else False

        # 1. Paper Broker Execution (if enabled for this strategy)
        paper_res = None
        if is_paper_enabled:
            paper_res = self.paper_broker.process_signal(strategy_name, symbol, signal_type, price, timestamp)
        else:
            paper_res = {"action": "paper_disabled", "reason": "Paper trading disabled for this strategy"}

        # 2. Live Broker Execution (if enabled for this strategy AND globally armed)
        is_live_ready = (
            is_real_enabled
            and mode == "LIVE"
            and self.is_armed()
            and self.delta_client.is_configured
        )

        live_res = None
        if is_live_ready:
            live_res = self._execute_live_trade(symbol, signal_type, price, strategy_name)

        # 3. Aggregated execution audit result
        if not is_paper_enabled and not is_real_enabled:
            return {
                "mode": "LOG_ONLY",
                "action": "logged_only_both_disabled",
                "paper_result": paper_res,
                "live_result": None,
                "strategy": strategy_name,
            }

        if is_live_ready:
            return {
                "mode": "LIVE",
                "action": f"paper_and_live_{live_res.get('action', 'executed') if live_res else 'executed'}",
                "paper_result": paper_res,
                "live_result": live_res,
                "strategy": strategy_name,
            }

        return {
            "mode": "PAPER",
            "action": "paper_executed" if is_paper_enabled else "paper_disabled",
            "paper_result": paper_res,
            "live_result": {"action": "live_disabled_or_disarmed"},
            "strategy": strategy_name,
        }

    def _execute_live_trade(
        self,
        symbol: str,
        signal_type: SignalType,
        price: float,
        strategy_name: str,
    ) -> dict[str, Any]:
        """Executes a real trade on Delta Exchange with position sizing & bracket orders."""
        clean_symbol = symbol.replace("-", "").upper()
        side = "buy" if signal_type == SignalType.BUY else "sell"

        try:
            ticker_data = self.delta_client.get_ticker(clean_symbol)
            product_id = int(ticker_data.get("product_id", 0))
            current_price = float(ticker_data.get("mark_price", price))
            leverage = int(ticker_data.get("leverage", 20))
            contract_value = float(ticker_data.get("contract_value", 0.001))

            # Check existing position / flip logic
            active_pos = self.delta_client.get_active_position(clean_symbol)
            if active_pos:
                pos_size = float(active_pos.get("size", 0.0))
                current_side = "buy" if pos_size > 0 else "sell"

                if current_side != side:
                    logger.info(f"🔄 FLIP SIGNAL for {clean_symbol}: Closing {current_side} position...")
                    self.delta_client.close_position(product_id, clean_symbol)
                    return {"action": "position_closed_on_flip", "symbol": clean_symbol}
                else:
                    logger.info(f"Already holding {current_side} position for {clean_symbol}. Skipping.")
                    return {"action": "skipped_already_in_position", "symbol": clean_symbol}

            if self.delta_client.is_already_in_position_or_order(clean_symbol):
                return {"action": "skipped_pending_order", "symbol": clean_symbol}

            # Balance check and trade sizing
            balance_data = self.delta_client.get_balance()
            avail_usd = float(balance_data.get("available_balance_usd", 0.0))
            if avail_usd <= 10.0:
                logger.warning(f"Insufficient Delta balance (${avail_usd:.2f}) to open trade on {clean_symbol}.")
                return {"action": "insufficient_balance", "balance": avail_usd}

            trade_setup = TradeCalculator.calculate_quantity(
                capital=avail_usd,
                mark_price=current_price,
                contract_value=contract_value,
                leverage=leverage,
                side=side,
            )

            quantity = trade_setup["quantity"]
            exec_leverage = trade_setup["leverage"]

            if quantity <= 0:
                logger.warning(f"Calculated quantity is 0 for {clean_symbol}. Skipping live trade.")
                return {"action": "zero_quantity"}

            # Place live entry order
            entry_res = self.delta_client.create_entry(
                product_id=product_id,
                size=quantity,
                side=side,
                entry_price=current_price,
                leverage=exec_leverage,
            )

            # Calculate and submit bracket order (Stop-Loss and Target)
            stop_target = TradeCalculator.calculate_stop_target(
                current_price=current_price,
                side=side,
                liquidation_price=float(ticker_data.get("liquidation_price", 0.0)),
            )

            bracket_res = self.delta_client.create_stoploss_target(
                product_id=product_id,
                symbol=clean_symbol,
                stoploss_price=stop_target["stop_loss"],
                target_price=stop_target["target"],
            )

            # Record in SQLite live_orders
            now_utc = datetime.now(UTC).isoformat()
            order_id = str(entry_res.get("order_id", ""))
            self.db.execute_modify(
                """
                INSERT INTO live_orders (
                    id, product_id, symbol, side, size, order_type,
                    limit_price, stop_price, status, is_bracket, raw_data, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    order_id,
                    product_id,
                    clean_symbol,
                    side,
                    quantity,
                    "MARKET",
                    current_price,
                    stop_target["stop_loss"],
                    "FILLED",
                    1,
                    json.dumps({"entry": entry_res, "bracket": bracket_res, "strategy": strategy_name}),
                    now_utc,
                    now_utc,
                ),
            )

            logger.info(f"🚀 LIVE ORDER PLACED | {clean_symbol} {side.upper()} {quantity} contracts @ ${current_price:.2f}")
            return {
                "mode": "LIVE",
                "action": "live_executed",
                "order_id": order_id,
                "quantity": quantity,
                "leverage": exec_leverage,
                "stop_loss": stop_target["stop_loss"],
                "target": stop_target["target"],
            }

        except Exception as exc:
            logger.exception(f"Live trade execution failed for {clean_symbol}")
            return {"mode": "LIVE", "action": "error", "error": str(exc)}

    def emergency_exit(self) -> dict[str, Any]:
        """Emergency kill switch: disarms live trading and executes Delta emergency exit."""
        self.disarm_live_trading()
        if self.delta_client.is_configured:
            return self.delta_client.emergency_exit()
        return {"success": True, "message": "Live trading disarmed (no Delta client configured)."}


def get_execution_manager() -> ExecutionManager:
    """Returns singleton ExecutionManager instance."""
    return ExecutionManager.get_instance()
