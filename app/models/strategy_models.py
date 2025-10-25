from pydantic import BaseModel
from enum import Enum
from datetime import datetime, timezone
from typing import Optional

class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"

class StrategyResult(BaseModel):
    strategy_name: str
    symbol: str
    signal_type: SignalType
    confidence: float
    execution_time: float
    timestamp: datetime = datetime.now(timezone.utc)
    price: Optional[float] = None
    sucess: Optional[bool] = False

    class Config:
        use_enum_values = True