import time
import random
import math
from datetime import datetime, timezone
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult, SignalType
from app.utility.data_provider import fetch_historical_data
from app.core.logger import get_strategies_logger

logger = get_strategies_logger()


class DummyHeavyStrategy(BaseStrategy):
    """
    A dummy strategy that simulates heavy calculation and processing time,
    ultimately returning a random signal (BUY/SELL/HOLD).
    Intended for testing performance and worker distribution.
    """
    def __init__(self):
        super().__init__("Dummy Heavy Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()
        logger.info(f"🚀 DummyHeavyStrategy | Starting heavy computation for {symbol}")

        # Fetch latest price just to have a real price for the paper trade
        try:
             df = fetch_historical_data(symbol, period=1, interval="15m")
             live_price = df['Close'].iloc[-1] if df is not None and not df.empty else 100.0
        except Exception as e:
             logger.warning(f"⚠️ DummyHeavyStrategy | Could not fetch price for {symbol}: {e}")
             live_price = 100.0

        # Simulate heavy CPU computation
        _simulate_heavy_calculation()

        # Simulate network or IO delay (manual delay)
        sleep_time = random.uniform(2.0, 5.0)  # Random sleep between 2 to 5 seconds
        logger.info(f"⏳ DummyHeavyStrategy | Simulating IO delay for {sleep_time:.2f} seconds...")
        time.sleep(sleep_time)

        # Randomly decide a signal
        signal_choice = random.choices(
            [SignalType.BUY, SignalType.SELL, SignalType.HOLD],
            weights=[0.3, 0.3, 0.4], # 30% BUY, 30% SELL, 40% HOLD
            k=1
        )[0]

        execution_time = time.time() - start_time
        logger.info(f"✅ DummyHeavyStrategy | Finished processing {symbol} in {execution_time:.2f}s | Result: {signal_choice.name}")

        return StrategyResult(
            strategy_name=self.name,
            symbol=symbol,
            signal_type=signal_choice,
            execution_time=execution_time,
            timestamp=datetime.now(timezone.utc),
            price=live_price
        )

def _simulate_heavy_calculation():
    """
    Simulates a heavy CPU bound task by calculating square roots in a large loop.
    """
    loop_count = 5_000_000
    dummy_val = 0.0
    for i in range(1, loop_count):
        dummy_val += math.sqrt(i)
    return dummy_val
