import time
from datetime import datetime, timezone
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult, SignalType
from app.utility.data_provider import fetch_historical_data
from app.core.logger import get_strategies_logger
import numpy as np

logger = get_strategies_logger()

class EMAStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("EMA Crossover Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()

        try:
            # Fetch data using our data provider
            df = fetch_historical_data(symbol, period=30, interval="15m")

            # --- Identify Crossovers ---
            # Buy Signal: 9EMA crosses above 15EMA (Golden Cross)
            df['Buy_Signal'] = (df['9EMA'] > df['15EMA']) & (df['9EMA'].shift(1) <= df['15EMA'].shift(1))
            
            # Sell Signal: 9EMA crosses below 15EMA (Death Cross)
            df['Sell_Signal'] = (df['9EMA'] < df['15EMA']) & (df['9EMA'].shift(1) >= df['15EMA'].shift(1))
            
            # --- Assign Actions ---
            df['Action'] = np.select([df['Buy_Signal'], df['Sell_Signal']], ['buy', 'sell'], default=None)

            if df.empty:
                execution_time = time.time() - start_time
                return StrategyResult(
                    strategy_name=self.name,
                    symbol=symbol,
                    signal_type=SignalType.HOLD,
                    execution_time=execution_time,
                    timestamp=datetime.now(timezone.utc),
                    price=0.0
                )

            # Get latest row
            latest = df.iloc[-1]
            current_price = latest['Close']

            # Determine signal type based on Action column
            signal_type = SignalType.HOLD
            if latest['Action'] == 'buy':
                signal_type = SignalType.BUY
            elif latest['Action'] == 'sell':
                signal_type = SignalType.SELL

            execution_time = time.time() - start_time

            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=signal_type,
                execution_time=execution_time,
                price=round(current_price, 2),
                timestamp=datetime.now(timezone.utc),
                success=True
                
            )

        except Exception as e:
            logger.error(f"❌ Error in EMAStrategy for {symbol}: {str(e)}", exc_info=True)
            execution_time = time.time() - start_time
            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                execution_time=execution_time,
                timestamp=datetime.now(timezone.utc),
                price=0.0
            )