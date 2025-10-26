import time
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult, SignalType
from app.utility.data_provider import fetch_historical_data
import numpy as np

class RSIStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("RSI Oversold/Overbought Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()

        try:
            # Fetch data using our data provider
            df = fetch_historical_data(symbol, period="5d", interval="5m")

            def define_rsi_buy_conditions(df):
                # RSI below 30 (oversold) - buy signal
                condition1 = df['RSI'] < 30
                # Additional confirmation: price above previous low
                condition2 = df['Close'] > df['Low'].shift(1)
                return condition1 & condition2

            def define_rsi_sell_conditions(df):
                # RSI above 70 (overbought) - sell signal
                condition1 = df['RSI'] > 70
                # Additional confirmation: price below previous high
                condition2 = df['Close'] < df['High'].shift(1)
                return condition1 & condition2

            df['Action'] = np.select([define_rsi_buy_conditions(df), define_rsi_sell_conditions(df)], ['buy', 'sell'], default='hold')

            if df.empty:
                execution_time = time.time() - start_time
                return StrategyResult(
                    strategy_name=self.name,
                    symbol=symbol,
                    signal_type=SignalType.HOLD,
                    confidence=0.0,
                    execution_time=execution_time,
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

            # Calculate confidence based on RSI levels and additional factors
            confidence = 0.0
            if signal_type == SignalType.BUY:
                # Higher confidence when RSI is very low (more oversold)
                rsi_confidence = (30 - latest['RSI']) / 30 * 60 if latest['RSI'] < 30 else 0
                # EMA trend confirmation
                ema_confirmation = 20 if latest['9EMA'] > latest['15EMA'] else 10
                # Candle confirmation
                candle_confirmation = 15 if latest['Candle'] == 'Green' else 5
                confidence = min(rsi_confidence + ema_confirmation + candle_confirmation, 100)

            elif signal_type == SignalType.SELL:
                # Higher confidence when RSI is very high (more overbought)
                rsi_confidence = (latest['RSI'] - 70) / 30 * 60 if latest['RSI'] > 70 else 0
                # EMA trend confirmation
                ema_confirmation = 20 if latest['9EMA'] < latest['15EMA'] else 10
                # Candle confirmation
                candle_confirmation = 15 if latest['Candle'] == 'Red' else 5
                confidence = min(rsi_confidence + ema_confirmation + candle_confirmation, 100)

            execution_time = time.time() - start_time

            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=signal_type,
                confidence=round(confidence / 100.0, 3),
                execution_time=execution_time,
                price=round(current_price, 2),
                success=True
            )

        except Exception as e:
            print(f"Error in RSIStrategy for {symbol}: {str(e)}")
            execution_time = time.time() - start_time
            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                confidence=0.0,
                execution_time=execution_time,
                price=0.0
            )