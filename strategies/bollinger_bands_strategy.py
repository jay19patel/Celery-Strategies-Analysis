import time
from core.base_strategy import BaseStrategy
from models.strategy_models import StrategyResult, SignalType
from utility.data_provider import fetch_historical_data
import numpy as np

class BollingerBandsStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("Bollinger Bands Mean Reversion Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()

        try:
            # Fetch data using our data provider
            df = fetch_historical_data(symbol, period="5d", interval="5m")

            # Calculate Bollinger Bands
            window = 20
            df['BB_Middle'] = df['Close'].rolling(window=window).mean()
            df['BB_Std'] = df['Close'].rolling(window=window).std()
            df['BB_Upper'] = df['BB_Middle'] + (df['BB_Std'] * 2)
            df['BB_Lower'] = df['BB_Middle'] - (df['BB_Std'] * 2)

            # Calculate %B indicator
            df['BB_PercentB'] = (df['Close'] - df['BB_Lower']) / (df['BB_Upper'] - df['BB_Lower'])

            def define_bb_buy_conditions(df):
                # Price touches or goes below lower band (oversold) - buy signal
                condition1 = df['Close'] <= df['BB_Lower']
                # %B below 0.2 (oversold)
                condition2 = df['BB_PercentB'] < 0.2
                # RSI confirms oversold
                condition3 = df['RSI'] < 40
                # Price starting to bounce back
                condition4 = df['Close'] > df['Close'].shift(1)
                return condition1 & condition2 & condition3 & condition4

            def define_bb_sell_conditions(df):
                # Price touches or goes above upper band (overbought) - sell signal
                condition1 = df['Close'] >= df['BB_Upper']
                # %B above 0.8 (overbought)
                condition2 = df['BB_PercentB'] > 0.8
                # RSI confirms overbought
                condition3 = df['RSI'] > 60
                # Price starting to fall back
                condition4 = df['Close'] < df['Close'].shift(1)
                return condition1 & condition2 & condition3 & condition4

            df['Action'] = np.select([define_bb_buy_conditions(df), define_bb_sell_conditions(df)], ['buy', 'sell'], default='hold')

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

            # Calculate confidence based on Bollinger Band position and other factors
            confidence = 0.0
            if signal_type != SignalType.HOLD:
                # %B position confidence
                percent_b = latest['BB_PercentB']
                if signal_type == SignalType.BUY:
                    bb_confidence = (0.2 - percent_b) / 0.2 * 40 if percent_b < 0.2 else 0
                else:  # SELL
                    bb_confidence = (percent_b - 0.8) / 0.2 * 40 if percent_b > 0.8 else 0

                # RSI confirmation
                rsi_confirmation = 0
                if signal_type == SignalType.BUY and latest['RSI'] < 40:
                    rsi_confirmation = (40 - latest['RSI']) / 40 * 25
                elif signal_type == SignalType.SELL and latest['RSI'] > 60:
                    rsi_confirmation = (latest['RSI'] - 60) / 40 * 25

                # Price momentum
                momentum_confirmation = 0
                if signal_type == SignalType.BUY and latest['Close'] > latest['Close']:
                    momentum_confirmation = 20
                elif signal_type == SignalType.SELL and latest['Close'] < latest['Close']:
                    momentum_confirmation = 20

                # Candle confirmation
                candle_confirmation = 0
                if signal_type == SignalType.BUY and latest['Candle'] == 'Green':
                    candle_confirmation = 15
                elif signal_type == SignalType.SELL and latest['Candle'] == 'Red':
                    candle_confirmation = 15

                confidence = min(bb_confidence + rsi_confirmation + momentum_confirmation + candle_confirmation, 100)

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
            print(f"Error in BollingerBandsStrategy for {symbol}: {str(e)}")
            execution_time = time.time() - start_time
            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                confidence=0.0,
                execution_time=execution_time,
                price=0.0
            )