"""Enumerations for domain-wide constants.

Using StrEnum so values serialise cleanly to JSON without extra work.
"""

from __future__ import annotations

from enum import StrEnum


class OrderSide(StrEnum):
    """Direction of a trade."""

    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    """Lifecycle of an order from placement to terminal state."""

    PENDING = "pending"
    PLACED = "placed"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"


class BrokerType(StrEnum):
    """Which execution venue handled the order."""

    PAPER = "paper"
    DELTA = "delta"


class DeltaEnvironment(StrEnum):
    """Delta Exchange environments."""

    DEMO = "demo"
    LIVE = "live"


class EventLevel(StrEnum):
    """Severity of an event in the event log."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class TradeCloseReason(StrEnum):
    """Why a paper position was closed."""

    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    MANUAL = "manual"
    TIMEOUT = "timeout"
    LIQUIDATION = "liquidation"
    SIGNAL = "signal"
    RISK_HALT = "risk_halt"


class PredictionStance(StrEnum):
    """Directional claim a verdict card makes."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    VOL_UP = "vol_up"
    VOL_DOWN = "vol_down"
    RANGE = "range"
    UNCLEAR = "unclear"


class PredictionMeasure(StrEnum):
    """What a prediction is actually about."""

    DIRECTION = "direction"
    VOLATILITY = "volatility"
    RANGE = "range"
    NONE = ""
