"""Trade calculator for Delta Exchange sizing, safe leverage, and bracket levels.

Ported from Trade-Buddy-Broker. Ensures liquidation price is strictly further
away than the stop-loss price by enforcing safety buffers on leverage.
"""

import logging
from typing import Any

from app.core.settings import settings

logger = logging.getLogger(__name__)


class TradeCalculator:
    """Calculates position quantities, safe leverage, and protective exit levels."""

    @staticmethod
    def calculate_quantity(
        capital: float,
        mark_price: float,
        contract_value: float,
        leverage: int,
        side: str = "buy",
        capital_pct: float | None = None,
        risk_ratio: float | None = None,
    ) -> dict[str, Any]:
        """Calculates integer contract quantity using allocated capital percentage and safe leverage.

        Args:
            capital: Available account balance in USD.
            mark_price: Current market price of the instrument.
            contract_value: Lot size / notional multiplier per contract.
            leverage: Exchange-provided maximum or default leverage.
            side: "buy" or "sell".
            capital_pct: Percentage of capital to allocate (defaults to settings.trade_capital_pct).
            risk_ratio: Fractional risk threshold (defaults to settings.risk_ratio).

        Returns:
            Dictionary containing used_capital, integer quantity, leverage, and limits.
        """
        trade_percent = capital_pct if capital_pct is not None else settings.trade_capital_pct
        current_risk_ratio = risk_ratio if risk_ratio is not None else settings.risk_ratio

        if capital <= 0 or mark_price <= 0 or contract_value <= 0:
            return {
                "used_capital": 0.0,
                "quantity": 0,
                "lot_size": contract_value,
                "entry_price": mark_price,
                "leverage": 1,
                "safe_leverage_limit": 1,
            }

        # 🛡️ Safe Leverage Calculation Logic:
        # Liquidation Distance ≈ 1 / Leverage. Stop Loss Distance = risk_ratio.
        # Enforce: 1 / Leverage > risk_ratio with an 80% safety buffer.
        safety_buffer = 0.8
        max_safe_leverage = int(safety_buffer / max(current_risk_ratio, 0.001))

        # Use conservative halving of maximum leverage
        proposed_leverage = int(leverage / 2) if leverage > 1 else 1
        effective_leverage = max(1, min(proposed_leverage, max_safe_leverage))

        used_capital = capital * (trade_percent / 100.0)

        # Raw quantity rounded down to whole contracts
        raw_quantity = (used_capital * effective_leverage) / (mark_price * contract_value)
        quantity = int(raw_quantity)

        return {
            "used_capital": round(used_capital, 4),
            "quantity": quantity,
            "lot_size": contract_value,
            "entry_price": mark_price,
            "leverage": effective_leverage,
            "safe_leverage_limit": max_safe_leverage,
        }

    @staticmethod
    def calculate_stop_target(
        current_price: float,
        side: str,
        liquidation_price: float = 0.0,
        risk_ratio: float | None = None,
        reward_ratio: float | None = None,
    ) -> dict[str, Any]:
        """Calculates stop-loss and target prices based on side and risk/reward ratios.

        Args:
            current_price: Execution or entry price.
            side: "buy" or "sell".
            liquidation_price: Estimated liquidation price from broker.
            risk_ratio: Fractional risk threshold (defaults to settings.risk_ratio).
            reward_ratio: Fractional reward threshold (defaults to settings.reward_ratio).

        Returns:
            Dictionary containing side, entry_price, stop_loss, target, and warnings.
        """
        curr_risk = risk_ratio if risk_ratio is not None else settings.risk_ratio
        curr_reward = reward_ratio if reward_ratio is not None else settings.reward_ratio
        order_side = side.lower()

        if order_side == "buy":
            stop_loss = current_price * (1.0 - curr_risk)
            target = current_price * (1.0 + curr_reward)
            warning_price = liquidation_price + (current_price - liquidation_price) * 0.1 if liquidation_price > 0 else 0.0
        else:
            stop_loss = current_price * (1.0 + curr_risk)
            target = current_price * (1.0 - curr_reward)
            warning_price = liquidation_price - (liquidation_price - current_price) * 0.1 if liquidation_price > 0 else 0.0

        return {
            "side": order_side,
            "entry_price": round(current_price, 4),
            "stop_loss": round(stop_loss, 4),
            "target": round(target, 4),
            "liquidation_price": round(liquidation_price, 4),
            "liquidation_warning_price": round(warning_price, 4),
        }
