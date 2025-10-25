import time
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
            df = fetch_historical_data(symbol, period="5d", interval="5m")

            def define_ema_buy_conditions(df):
                # 9 EMA crosses above 15 EMA (Golden Cross) - buy signal
                condition1 = (df['9EMA'].shift(1) <= df['15EMA'].shift(1)) & (df['9EMA'] > df['15EMA'])
                # Additional confirmation: price above 9 EMA
                condition2 = df['Close'] > df['9EMA']
                # Volume confirmation (if available)
                volume_condition = True
                if 'Volume' in df.columns:
                    volume_condition = df['Volume'] > df['Volume'].rolling(10).mean()
                return condition1 & condition2 & volume_condition

            def define_ema_sell_conditions(df):
                # 9 EMA crosses below 15 EMA (Death Cross) - sell signal
                condition1 = (df['9EMA'].shift(1) >= df['15EMA'].shift(1)) & (df['9EMA'] < df['15EMA'])
                # Additional confirmation: price below 9 EMA
                condition2 = df['Close'] < df['9EMA']
                # Volume confirmation (if available)
                volume_condition = True
                if 'Volume' in df.columns:
                    volume_condition = df['Volume'] > df['Volume'].rolling(10).mean()
                return condition1 & condition2 & volume_condition

            df['Action'] = np.select([define_ema_buy_conditions(df), define_ema_sell_conditions(df)], ['buy', 'sell'], default='hold')

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

            # Calculate confidence based on EMA separation and additional factors
            confidence = 0.0
            if signal_type != SignalType.HOLD:
                # EMA separation confidence
                ema_diff = abs(latest['9EMA'] - latest['15EMA'])
                ema_separation_confidence = min(ema_diff / latest['Close'] * 1000, 40)  # Normalize

                # RSI confirmation
                rsi_confirmation = 0
                if signal_type == SignalType.BUY and 30 < latest['RSI'] < 70:
                    rsi_confirmation = 20
                elif signal_type == SignalType.SELL and 30 < latest['RSI'] < 70:
                    rsi_confirmation = 20

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

                confidence = min(ema_separation_confidence + rsi_confirmation + price_confirmation + candle_confirmation, 100)

            execution_time = time.time() - start_time

            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=signal_type,
                confidence=round(confidence / 100.0, 3),
                execution_time=execution_time,
                price=round(current_price, 2),
                sucess=True
                
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
                price=0.0
            )