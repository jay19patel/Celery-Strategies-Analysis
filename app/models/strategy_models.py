from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"

class StrategyResult(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    strategy_name: str
    symbol: str
    signal_type: SignalType
    execution_time: float
    timestamp: datetime
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    success: bool | None = False
