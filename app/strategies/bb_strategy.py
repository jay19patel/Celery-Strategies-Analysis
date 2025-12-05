import time
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult, SignalType
from app.utility.data_provider import fetch_historical_data
import numpy as np
import pandas_ta as ta

class BBStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("Bollinger Bands Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()

        try:
            # Fetch data using our data provider
            df = fetch_historical_data(symbol, period=30, interval="15m")

            # --- Calculate Bollinger Bands ---
            bb = ta.bbands(df['Close'], length=20, std=2)

            if bb is None or bb.empty:
                raise Exception("Failed to calculate Bollinger Bands")

            # Rename columns properly
            bb_cols = {}
            for col in bb.columns:
                if 'BBU_' in col:
                    bb_cols[col] = 'UpperBB'
                elif 'BBL_' in col:
                    bb_cols[col] = 'LowerBB'

            bb = bb.rename(columns=bb_cols)

            # Keep only UpperBB and LowerBB
            bb = bb[['UpperBB', 'LowerBB']]

            df = df.join(bb)

            # --- Calculate PriceToEMA ---
            df_ema = ta.ema(df['Close'], length=300)
            df["PriceToEMA"] = (df["Close"] - df_ema) / df_ema * 100

            # --- Signal Conditions ---
            threshold = 0.001  # 0.1% difference

            buy_condition = (
                (df["LowerBB"].notna()) &
                (abs(df["Close"] - df["LowerBB"]) <= threshold * df["LowerBB"]) &
                (df["PriceToEMA"] <= 0)
            )

            sell_condition = (
                (df["UpperBB"].notna()) &
                (abs(df["Close"] - df["UpperBB"]) <= threshold * df["UpperBB"]) &
                (df["PriceToEMA"] >= 0)
            )

            # --- Assign Actions ---
            df['Action'] = np.select(
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

            # Determine signal type based on Action column
            signal_type = SignalType.HOLD
            if latest['Action'] == 1:
                signal_type = SignalType.BUY
            elif latest['Action'] == -1:
                signal_type = SignalType.SELL

            # Calculate confidence
            confidence = 0.0
            if signal_type != SignalType.HOLD:
                # Distance from BB bands
                if signal_type == SignalType.BUY:
                    bb_distance = abs(latest['Close'] - latest['LowerBB']) / latest['LowerBB']
                    bb_confidence = max(0, 40 - (bb_distance * 10000))  # Closer = higher confidence
                else:
                    bb_distance = abs(latest['Close'] - latest['UpperBB']) / latest['UpperBB']
                    bb_confidence = max(0, 40 - (bb_distance * 10000))

                # PriceToEMA confirmation
                ema_confirmation = 0
                if signal_type == SignalType.BUY and latest['PriceToEMA'] < 0:
                    ema_confirmation = min(abs(latest['PriceToEMA']) * 10, 30)
                elif signal_type == SignalType.SELL and latest['PriceToEMA'] > 0:
                    ema_confirmation = min(abs(latest['PriceToEMA']) * 10, 30)

                # Candle confirmation
                candle_confirmation = 0
                if signal_type == SignalType.BUY and latest['Candle'] == 'Green':
                    candle_confirmation = 15
                elif signal_type == SignalType.SELL and latest['Candle'] == 'Red':
                    candle_confirmation = 15

                confidence = min(bb_confidence + ema_confirmation + candle_confirmation, 100)

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
            print(f"Error in BBStrategy for {symbol}: {str(e)}")
            execution_time = time.time() - start_time
            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                confidence=0.0,
                execution_time=execution_time,
                price=0.0
            )
