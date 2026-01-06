import time
from datetime import datetime, timezone
import pandas as pd
import numpy as np
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult, SignalType
from app.utility.data_provider import fetch_historical_data


class PDHLStrategy(BaseStrategy):
    """
    Previous Day/Week/Month High/Low (PDHL) Breakout Strategy
    
    Logic:
    - Iterates through timeframes: 1 Month > 1 Week > 1 Day.
    - BUY: Intraday 15m Close > Ref High AND Intraday 15m Low < Ref Close.
    - SELL: Intraday 15m Close < Ref Low AND Intraday 15m High > Ref Close.
    """

    def __init__(self):
        super().__init__("Previous Day HL Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()

        # 1. Fetch 15m data FIRST to get the authoritative LIVE PRICE and Signal Candle
        try:
             df_15m = fetch_historical_data(symbol, period=5, interval="15m")
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
        
        # Priority order: 1 Month > 1 Week > 1 Day
        timeframes = [
            {"interval": "1M", "period": 5000, "name": "Prev Month"},
            {"interval": "1w", "period": 2100, "name": "Prev Week"},
            {"interval": "1d", "period": 400, "name": "Prev Day"},
        ]

        final_signal = SignalType.HOLD
        confidence = 0.0
        used_timeframe_name = "None"
        
        try:
            # Pre-calculate 15m Candle Levels
            # User Request: "running candle nahi chaiye" -> Use Last Closed Candle (iloc[-2])
            if len(df_15m) < 2:
                 execution_time = time.time() - start_time
                 return StrategyResult(
                    strategy_name=f"{self.name} (Insufficient Data)",
                    symbol=symbol,
                    signal_type=SignalType.HOLD,
                    confidence=0.0,
                    execution_time=execution_time,
                    timestamp=datetime.now(timezone.utc),
                    price=round(live_price, 2)
                 )

            curr_close = df_15m['Close'].iloc[-2]
            curr_high = df_15m['High'].iloc[-2]
            curr_low = df_15m['Low'].iloc[-2]
            curr_open = df_15m['Open'].iloc[-2] # For color check
            
            # Volume analysis for confidence
            # Original: -21:-1 (Last 20 excl running). Now Running is ignored (-2 is target).
            # So range shifts back by 1: -22:-2
            avg_vol_15m = df_15m['Volume'].iloc[-22:-2].mean() if len(df_15m) > 21 else 0
            curr_vol_15m = df_15m['Volume'].iloc[-2]

            # --- PREVIOUS DAY DATA ONLY ---
            # User Request: "only previd day ka hi rakhe weeks and month nikal do"
            df_day = fetch_historical_data(symbol, period=400, interval="1d", ttl=3600)
            
            if df_day is not None and not df_day.empty and len(df_day) >= 2:
                # Get Previous Candle (Last Closed Ref Candle)
                prev_high = df_day['High'].iloc[-2]
                prev_low = df_day['Low'].iloc[-2]
                
                # --- Signal Logic ---
                # User Request (Simplified): "Use ONLY High/Low, ignore Open/Close"
                
                triggered_level = 0.0
                buy_signal = False
                sell_signal = False
    
                # BUY: Close > Prev High
                if curr_close > prev_high:
                    buy_signal = True
                    triggered_level = prev_high
                
                # SELL: Close < Prev Low
                elif curr_close < prev_low:
                    sell_signal = True
                    triggered_level = prev_low
                
                if buy_signal:
                    final_signal = SignalType.BUY
                    used_timeframe_name = "Prev Day"
                elif sell_signal:
                    final_signal = SignalType.SELL
                    used_timeframe_name = "Prev Day"
                
                if final_signal != SignalType.HOLD:
                    # Calculate Confidence
                    base_conf = 60 # Higher base for this strong pattern
                    
                    # 1. Breakout Strength / Momentum
                    diff = abs(curr_close - triggered_level)
                    strength = (diff / triggered_level) * 100
                    base_conf += min(strength * 5, 20)
                    
                    # 2. Candle Color Alignment
                    is_green = curr_close >= curr_open
                    if (final_signal == SignalType.BUY and is_green) or (final_signal == SignalType.SELL and not is_green):
                        base_conf += 10
                        
                    # 3. Volume Confirmation
                    if curr_vol_15m > avg_vol_15m:
                         base_conf += 10
                         
                    confidence = min(base_conf, 100)

        except Exception as e:
            print(f"Error in PDHLStrategy for {symbol}: {str(e)}")
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
                    

