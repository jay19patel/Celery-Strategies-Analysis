import time
from datetime import datetime, timezone
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult, SignalType
from app.utility.data_provider import fetch_historical_data
import numpy as np

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
                    confidence=0.0,
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

            # Calculate confidence based on EMA separation and additional factors
            confidence = 0.0
            if signal_type != SignalType.HOLD:
                # EMA separation confidence
                ema_diff = abs(latest['9EMA'] - latest['15EMA'])
                ema_separation_confidence = min(ema_diff / latest['Close'] * 1000, 40)  # Normalize

                # Price position relative to EMAs
                price_confirmation = 0
                if signal_type == SignalType.BUY and latest['Close'] > latest['9EMA'] > latest['15EMA']:
                    price_confirmation = 25
                elif signal_type == SignalType.SELL and latest['Close'] < latest['9EMA'] < latest['15EMA']:
                    price_confirmation = 25

                # Candle confirmation
                candle_confirmation = 0
                if signal_type == SignalType.BUY and latest['Candle'] == 'Green':
                    candle_confirmation = 15
                elif signal_type == SignalType.SELL and latest['Candle'] == 'Red':
                    candle_confirmation = 15

                confidence = min(ema_separation_confidence + price_confirmation + candle_confirmation, 100)

            execution_time = time.time() - start_time

            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=signal_type,
                confidence=round(confidence / 100.0, 3),
                execution_time=execution_time,
                price=round(current_price, 2),
                timestamp=datetime.now(timezone.utc),
                success=True
                
            )

        except Exception as e:
            print(f"Error in EMAStrategy for {symbol}: {str(e)}")
            execution_time = time.time() - start_time
            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                confidence=0.0,
                execution_time=execution_time,
                timestamp=datetime.now(timezone.utc),
                price=0.0
            )