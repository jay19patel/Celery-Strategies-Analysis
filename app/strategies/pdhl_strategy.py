import time
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult, SignalType
from app.utility.data_provider import fetch_historical_data
import numpy as np
import pandas as pd


class PDHLStrategy(BaseStrategy):
    """
    Previous Day High/Low (PDHL) Breakout Strategy

    Logic:
    - BUY: Previous candle close was below prev day low, current candle breaks above prev day low
    - SELL: Previous candle close was above prev day high, current candle breaks below prev day high
    """

    def __init__(self):
        super().__init__("Previous Day HL Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()

        try:
            # Fetch data - need more days for previous day calculation
            df = fetch_historical_data(symbol, period=30, interval="15m")

            if df.empty or len(df) < 2:
                execution_time = time.time() - start_time
                return StrategyResult(
                    strategy_name=self.name,
                    symbol=symbol,
                    signal_type=SignalType.HOLD,
                    confidence=0.0,
                    execution_time=execution_time,
                    price=0.0
                )

            # --- Step 1: Resample to daily OHLC ---
            daily_ohlc = df.resample('1D').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last'
            })

            # --- Step 2: Shift to get previous day OHLC ---
            daily_ohlc_prev = daily_ohlc.shift(1)[['High', 'Low']]

            # --- Step 3: Map previous day High/Low to each intraday candle ---
            df['Prev_High'] = df.index.normalize().map(daily_ohlc_prev['High'])
            df['Prev_Low'] = df.index.normalize().map(daily_ohlc_prev['Low'])

            # Drop rows where prev day data is not available
            df = df.dropna(subset=['Prev_High', 'Prev_Low'])

            if df.empty or len(df) < 2:
                execution_time = time.time() - start_time
                return StrategyResult(
                    strategy_name=self.name,
                    symbol=symbol,
                    signal_type=SignalType.HOLD,
                    confidence=0.0,
                    execution_time=execution_time,
                    price=0.0
                )

            # --- Step 4: Get previous candle close ---
            prev_close = df['Close'].shift(1)

            # --- Step 5: Define PDHL breakout conditions ---
            # SELL: Previous close was above prev day high, current close breaks below it
            sell_condition = (prev_close > df['Prev_High']) & (df['Close'] < df['Prev_High'])

            # BUY: Previous close was below prev day low, current close breaks above it
            buy_condition = (prev_close < df['Prev_Low']) & (df['Close'] > df['Prev_Low'])

            # --- Step 6: Assign Actions ---
            df['PDHLAction'] = np.select(
                [buy_condition, sell_condition],
                [1, -1],
                default=0
            )

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

            # Determine signal type
            signal_type = SignalType.HOLD
            if latest['PDHLAction'] == 1:
                signal_type = SignalType.BUY
            elif latest['PDHLAction'] == -1:
                signal_type = SignalType.SELL

            # Calculate confidence
            confidence = 0.0
            if signal_type != SignalType.HOLD:
                # Base confidence for PDHL breakout signal
                base_confidence = 40

                # Breakout strength: how far price has moved through the level
                if signal_type == SignalType.BUY:
                    breakout_strength = (latest['Close'] - latest['Prev_Low']) / latest['Prev_Low']
                    strength_confidence = min(breakout_strength * 5000, 30)  # Up to 30 points
                else:  # SELL
                    breakout_strength = (latest['Prev_High'] - latest['Close']) / latest['Prev_High']
                    strength_confidence = min(breakout_strength * 5000, 30)

                # Candle confirmation
                candle_confirmation = 0
                if signal_type == SignalType.BUY and latest['Candle'] == 'Green':
                    candle_confirmation = 15
                elif signal_type == SignalType.SELL and latest['Candle'] == 'Red':
                    candle_confirmation = 15

                # Volume confirmation (if price movement is strong)
                volume_confirmation = 0
                if 'Volume' in df.columns and df['Volume'].notna().any():
                    avg_volume = df['Volume'].tail(20).mean()
                    if latest['Volume'] > avg_volume * 1.2:
                        volume_confirmation = 15

                confidence = min(base_confidence + strength_confidence + candle_confirmation + volume_confirmation, 100)

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
            print(f"Error in PDHLStrategy for {symbol}: {str(e)}")
            execution_time = time.time() - start_time
            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                confidence=0.0,
                execution_time=execution_time,
                price=0.0
            )
