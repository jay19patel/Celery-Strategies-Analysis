from pydantic import BaseModel
from enum import Enum
from datetime import datetime
from typing import Optional

class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"

class StrategyResult(BaseModel):
    strategy_name: str
    signal_type: SignalType
    confidence: float
    execution_time: float
    timestamp: datetime = datetime.now()
    price: Optional[float] = None

    class Config:
        use_enum_values = True