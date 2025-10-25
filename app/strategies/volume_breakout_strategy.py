import time
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult, SignalType
from app.utility.data_provider import fetch_historical_data
import numpy as np

class VolumeBreakoutStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("Volume Breakout Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()

        try:
            # Fetch data using our data provider
            df = fetch_historical_data(symbol, period="5d", interval="5m")

            # Calculate volume indicators
            df['Volume_SMA_20'] = df['Volume'].rolling(window=20).mean() if 'Volume' in df.columns else 1
            df['Volume_Ratio'] = df['Volume'] / df['Volume_SMA_20'] if 'Volume' in df.columns else 1

            # Calculate price volatility
            df['Price_Range'] = df['High'] - df['Low']
            df['Avg_Range'] = df['Price_Range'].rolling(window=20).mean()
            df['Range_Ratio'] = df['Price_Range'] / df['Avg_Range']

            def define_volume_buy_conditions(df):
                # High volume breakout above resistance
                condition1 = df['Volume_Ratio'] > 2.0  # Volume 2x higher than average
                # Price breakout above recent high
                condition2 = df['Close'] > df['High'].rolling(window=10).max().shift(1)
                # Strong candle body
                condition3 = df['Body'] > 40
                # Green candle
                condition4 = df['Candle'] == 'Green'
                # RSI not extremely overbought
                condition5 = df['RSI'] < 80
                # EMA support
                condition6 = df['Close'] > df['15EMA']
                return condition1 & condition2 & condition3 & condition4 & condition5 & condition6

            def define_volume_sell_conditions(df):
                # High volume breakdown below support
                condition1 = df['Volume_Ratio'] > 2.0  # Volume 2x higher than average
                # Price breakdown below recent low
                condition2 = df['Close'] < df['Low'].rolling(window=10).min().shift(1)
                # Strong candle body
                condition3 = df['Body'] > 40
                # Red candle
                condition4 = df['Candle'] == 'Red'
                # RSI not extremely oversold
                condition5 = df['RSI'] > 20
                # EMA resistance
                condition6 = df['Close'] < df['15EMA']
                return condition1 & condition2 & condition3 & condition4 & condition5 & condition6

            df['Action'] = np.select([define_volume_buy_conditions(df), define_volume_sell_conditions(df)], ['buy', 'sell'], default='hold')

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

            # Calculate confidence based on volume and breakout strength
            confidence = 0.0
            if signal_type != SignalType.HOLD:
                # Volume confirmation
                volume_confidence = min((latest['Volume_Ratio'] - 1.0) * 20, 40) if latest['Volume_Ratio'] > 1 else 0

                # Price breakout strength
                if signal_type == SignalType.BUY:
                    recent_high = df['High'].rolling(window=10).max().iloc[-2]
                    breakout_strength = (latest['Close'] - recent_high) / recent_high * 1000
                else:  # SELL
                    recent_low = df['Low'].rolling(window=10).min().iloc[-2]
                    breakout_strength = (recent_low - latest['Close']) / recent_low * 1000

                breakout_confidence = min(abs(breakout_strength), 25)

                # Candle body strength
                body_confidence = min(latest['Body'] / 2, 20)

                # RSI position
                rsi_confirmation = 0
                if signal_type == SignalType.BUY and 40 < latest['RSI'] < 80:
                    rsi_confirmation = 15
                elif signal_type == SignalType.SELL and 20 < latest['RSI'] < 60:
                    rsi_confirmation = 15

                confidence = min(volume_confidence + breakout_confidence + body_confidence + rsi_confirmation, 100)

            execution_time = time.time() - start_time

            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=signal_type,
                confidence=round(confidence / 100.0, 3),
                execution_time=execution_time,
                price=round(current_price, 2),
                sucscess=True
            )

        except Exception as e:
            print(f"Error in VolumeBreakoutStrategy for {symbol}: {str(e)}")
            execution_time = time.time() - start_time
            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                confidence=0.0,
                execution_time=execution_time,
                price=0.0
            )