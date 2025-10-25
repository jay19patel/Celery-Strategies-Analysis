import time
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult, SignalType
from app.utility.data_provider import fetch_historical_data
import numpy as np

class MACDStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("MACD Convergence Divergence Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()

        try:
            # Fetch data using our data provider
            df = fetch_historical_data(symbol, period="5d", interval="5m")

            # Calculate MACD
            exp1 = df['Close'].ewm(span=12, adjust=False).mean()
            exp2 = df['Close'].ewm(span=26, adjust=False).mean()
            df['MACD'] = exp1 - exp2
            df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
            df['MACD_Histogram'] = df['MACD'] - df['MACD_Signal']

            def define_macd_buy_conditions(df):
                # MACD crosses above signal line (bullish crossover)
                condition1 = (df['MACD'].shift(1) <= df['MACD_Signal'].shift(1)) & (df['MACD'] > df['MACD_Signal'])
                # MACD is below zero line (buying in oversold territory)
                condition2 = df['MACD'] < 0
                # Histogram is increasing
                condition3 = df['MACD_Histogram'] > df['MACD_Histogram'].shift(1)
                # RSI not overbought
                condition4 = df['RSI'] < 70
                # Price above short EMA
                condition5 = df['Close'] > df['9EMA']
                return condition1 & condition2 & condition3 & condition4 & condition5

            def define_macd_sell_conditions(df):
                # MACD crosses below signal line (bearish crossover)
                condition1 = (df['MACD'].shift(1) >= df['MACD_Signal'].shift(1)) & (df['MACD'] < df['MACD_Signal'])
                # MACD is above zero line (selling in overbought territory)
                condition2 = df['MACD'] > 0
                # Histogram is decreasing
                condition3 = df['MACD_Histogram'] < df['MACD_Histogram'].shift(1)
                # RSI not oversold
                condition4 = df['RSI'] > 30
                # Price below short EMA
                condition5 = df['Close'] < df['9EMA']
                return condition1 & condition2 & condition3 & condition4 & condition5

            df['Action'] = np.select([define_macd_buy_conditions(df), define_macd_sell_conditions(df)], ['buy', 'sell'], default='hold')

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

            # Calculate confidence based on MACD strength and other factors
            confidence = 0.0
            if signal_type != SignalType.HOLD:
                # MACD line strength
                macd_strength = abs(latest['MACD'] - latest['MACD_Signal'])
                macd_confidence = min(macd_strength * 1000, 30)  # Normalize

                # Histogram momentum
                histogram_momentum = 0
                if signal_type == SignalType.BUY and latest['MACD_Histogram'] > latest['MACD_Histogram']:
                    histogram_momentum = 20
                elif signal_type == SignalType.SELL and latest['MACD_Histogram'] < latest['MACD_Histogram']:
                    histogram_momentum = 20

                # RSI confirmation
                rsi_confirmation = 0
                if signal_type == SignalType.BUY and 30 < latest['RSI'] < 70:
                    rsi_confirmation = 20
                elif signal_type == SignalType.SELL and 30 < latest['RSI'] < 70:
                    rsi_confirmation = 20

                # EMA trend confirmation
                ema_confirmation = 0
                if signal_type == SignalType.BUY and latest['9EMA'] > latest['15EMA']:
                    ema_confirmation = 15
                elif signal_type == SignalType.SELL and latest['9EMA'] < latest['15EMA']:
                    ema_confirmation = 15

                # Price momentum
                price_momentum = 0
                if signal_type == SignalType.BUY and latest['Close'] > latest['Close']:
                    price_momentum = 15
                elif signal_type == SignalType.SELL and latest['Close'] < latest['Close']:
                    price_momentum = 15

                confidence = min(macd_confidence + histogram_momentum + rsi_confirmation + ema_confirmation + price_momentum, 100)

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
            print(f"Error in MACDStrategy for {symbol}: {str(e)}")
            execution_time = time.time() - start_time
            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                confidence=0.0,
                execution_time=execution_time,
                price=0.0
            )