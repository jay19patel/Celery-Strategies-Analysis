import time
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult, SignalType
from app.utility.data_provider import fetch_historical_data
import numpy as np
import pandas_ta as ta


class MotherCandleStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("Mother Candle Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()

        try:
            # Fetch data using our data provider
            df = fetch_historical_data(symbol, period=30, interval="15m")

            # --- Calculate PriceToEMA ---
            df_ema = ta.ema(df['Close'], length=300)
            df["PriceToEMA"] = (df["Close"] - df_ema) / df_ema * 100

            # --- Inside Bar Detection ---
            is_inside_bar = (df['High'].shift(1) > df['High']) & (df['Low'].shift(1) < df['Low'])
            mother_high = df['High'].shift(2)
            mother_low = df['Low'].shift(2)

            # Inside Bar Breakout Signals
            ib_buy_condition = is_inside_bar.shift(-1) & (df['Close'] > mother_high)
            ib_sell_condition = is_inside_bar.shift(-1) & (df['Close'] < mother_low)

            # --- Mother Bar Detection ---
            Mother_Bar = (df['High'].shift(1) > df['High'].shift(2)) & (df['Low'].shift(1) < df['Low'].shift(2))

            # Mother Candle Breakout Signals
            MCBuy_Signal = (Mother_Bar == True) & (df['Close'] > df['High'].shift(2)) & (df["PriceToEMA"] <= -1)
            MCSell_Signal = (Mother_Bar == True) & (df['Close'] < df['Low'].shift(2)) & (df["PriceToEMA"] >= 1)

            # --- Combine Both Strategies ---
            combined_buy = ib_buy_condition | MCBuy_Signal
            combined_sell = ib_sell_condition | MCSell_Signal

            # --- Assign Final Action ---
            df['Action'] = np.select(
                [combined_buy, combined_sell],
                [1, -1],
                default=0
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
                # Pattern confirmation
                pattern_confidence = 30

                # PriceToEMA confirmation
                ema_confirmation = 0
                if signal_type == SignalType.BUY and latest['PriceToEMA'] < 0:
                    ema_confirmation = min(abs(latest['PriceToEMA']) * 5, 30)
                elif signal_type == SignalType.SELL and latest['PriceToEMA'] > 0:
                    ema_confirmation = min(abs(latest['PriceToEMA']) * 5, 30)

                # Candle confirmation
                candle_confirmation = 0
                if signal_type == SignalType.BUY and latest['Candle'] == 'Green':
                    candle_confirmation = 20
                elif signal_type == SignalType.SELL and latest['Candle'] == 'Red':
                    candle_confirmation = 20

                # Volume confirmation (if volume is above average)
                volume_confirmation = 0
                if 'Volume' in df.columns and df['Volume'].mean() > 0:
                    avg_volume = df['Volume'].tail(20).mean()
                    if latest['Volume'] > avg_volume:
                        volume_confirmation = 20

                confidence = min(pattern_confidence + ema_confirmation + candle_confirmation + volume_confirmation, 100)

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
            print(f"Error in MotherCandleStrategy for {symbol}: {str(e)}")
            execution_time = time.time() - start_time
            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                confidence=0.0,
                execution_time=execution_time,
                price=0.0
            )
