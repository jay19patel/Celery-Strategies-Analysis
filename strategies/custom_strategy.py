import random
import time
from core.base_strategy import BaseStrategy
from models.strategy_models import StrategyResult, SignalType

class CustomStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("Custom Strategy")

    def execute(self) -> StrategyResult:
        start_time = time.time()

        # Dummy execution time with random sleep between 5-10 seconds
        sleep_time = random.uniform(5, 10)
        time.sleep(sleep_time)

        # Generate random signal and confidence for demo purposes
        signal_types = [SignalType.BUY, SignalType.SELL, SignalType.HOLD]
        random_signal_type = random.choice(signal_types)
        confidence = round(random.uniform(0.3, 0.85), 2)

        execution_time = time.time() - start_time

        return StrategyResult(
            strategy_name=self.name,
            signal_type=random_signal_type,
            confidence=confidence,
            execution_time=execution_time,
            price=random.uniform(100, 500)
        )