import time
from datetime import datetime, timezone
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
        
        # 1. Fetch 15m data FIRST to get the authoritative LIVE PRICE
        # We use default cache (2 mins) for this as it tracks live moves
        try:
             df_15m = fetch_historical_data(symbol, period=30, interval="15m")
        except Exception as e:
             df_15m = None
             print(f"Error fetching 15m data: {e}")

        if df_15m is None or df_15m.empty:
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

        live_price = df_15m['Close'].iloc[-1]
        
        # Priority order: 1 Month > 1 Week > 1 Day > 1 Hour > 15 Minute
        # Define Timeframes with aggressive Caching (TTL) for higher TFs to save API calls
        timeframes = [
            {"interval": "1M", "period": 5000, "name": "1 Month", "ttl": 86400}, # Cache for 1 Day
            {"interval": "1w", "period": 2100, "name": "1 Week", "ttl": 14400},  # Cache for 4 Hours
            {"interval": "1d", "period": 400, "name": "1 Day", "ttl": 3600},     # Cache for 1 Hour
            {"interval": "1h", "period": 30, "name": "1 Hour", "ttl": 300},      # Cache for 5 Mins
            {"interval": "15m", "period": 30, "name": "15 Minute", "ttl": 120}  # Default Cache
        ]

        final_signal = SignalType.HOLD
        confidence = 0.0
        used_timeframe_name = "None"
        
        try:
            for tf in timeframes:
                # Use cached data if available (using specific TTL)
                if tf["interval"] == "15m":
                     df = df_15m # Reuse already fetched data
                else:
                     df = fetch_historical_data(symbol, period=tf["period"], interval=tf["interval"], ttl=tf["ttl"])
                
                if df is None or df.empty or len(df) < 50:
                    continue
                
                # Use LIVE price for current close
                curr_close = live_price
                
                # --- Setup Detection ---
                # Index -2: Last Closed Candle (Previous)
                prev_high = df['High'].iloc[-2]
                prev_low = df['Low'].iloc[-2]
                
                # --- Signal Logic: Previous Candle Breakout ---
                mc_buy = False
                mc_sell = False
                triggered_level = 0.0

                # Buy if Live Price breaks Previous High
                if curr_close > prev_high:
                    mc_buy = True
                    triggered_level = prev_high
                
                # Sell if Live Price breaks Previous Low
                elif curr_close < prev_low:
                    mc_sell = True
                    triggered_level = prev_low

                # Combine
                signal = 0
                if mc_buy:
                    signal = 1
                elif mc_sell:
                    signal = -1
                
                if signal != 0:
                    final_signal = SignalType.BUY if signal == 1 else SignalType.SELL
                    used_timeframe_name = tf["name"]
                    
                    # Calculate Confidence
                    conf = 50 # Base confidence for breakout
                        
                    # 1. Candle Color Confirmation
                    # If we are Buying, Current Candle should optionally be Green (showing strength in current session)
                    is_green = curr_close >= df['Open'].iloc[-1]
                    if (signal == 1 and is_green) or (signal == -1 and not is_green):
                        conf += 20
                        
                    # 2. Breakout Strength
                    # How far above the level are we?
                    diff = abs(curr_close - triggered_level)
                    strength = (diff / triggered_level) * 100
                    conf += min(strength * 5, 20)
                        
                    # 3. Volume Confirmation
                    # Use cached volume for Previous candles to get average
                    avg_vol = df['Volume'].iloc[-21:-1].mean()
                    # Use current candle volume from the Timeframe DF (latest partial candle)
                    curr_vol_tf = df['Volume'].iloc[-1]
                    
                    if curr_vol_tf > avg_vol:
                        conf += 20
                        
                    confidence = min(conf, 100)
                    break
        
        except Exception as e:
            print(f"Error in MotherCandleStrategy processing {symbol}: {str(e)}")
            pass

        execution_time = time.time() - start_time

        return StrategyResult(
            strategy_name=f"{self.name} ({used_timeframe_name})" if used_timeframe_name != "None" else self.name,
            symbol=symbol,
            signal_type=final_signal,
            confidence=round(confidence / 100.0, 3),
            execution_time=execution_time,
            price=round(live_price, 2),
            timestamp=datetime.now(timezone.utc),
            success=True
        )
