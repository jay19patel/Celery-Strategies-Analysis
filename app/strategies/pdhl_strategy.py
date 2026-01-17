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
        # We will check all, and if multiple match, we take the highest priority (Month > Week > Day)
        # However, usually we just want ANY breakout. But let's check in order.
        
        final_signal = SignalType.HOLD
        confidence = 0.0
        used_timeframe_name = "None"
        triggered_level = 0.0

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
            curr_open = df_15m['Open'].iloc[-2]
            
            # Volume for confidence
            avg_vol_15m = df_15m['Volume'].iloc[-22:-2].mean() if len(df_15m) > 21 else 0
            curr_vol_15m = df_15m['Volume'].iloc[-2]

            # Fetch Higher Timeframe Data
            # Note: Fetching them sequentially. 
            
            # 1. Month
            df_month = fetch_historical_data(symbol, period=5000, interval="1M")
            # 2. Week
            df_week = fetch_historical_data(symbol, period=2100, interval="1w")
            # 3. Day
            df_day = fetch_historical_data(symbol, period=400, interval="1d", ttl=3600)

            # Define checks
            check_list = [
                {"df": df_month, "name": "Prev Month"},
                {"df": df_week, "name": "Prev Week"},
                {"df": df_day, "name": "Prev Day"},
            ]

            for item in check_list:
                df = item["df"]
                name = item["name"]
                
                if df is not None and not df.empty and len(df) >= 2:
                    # Get Ref Candle (Last Closed) -> iloc[-2]
                    # Note: For 1M, if current month is running, -2 is prev month. correct.
                    ref_high = df['High'].iloc[-2]
                    ref_low = df['Low'].iloc[-2]
                    
                    # BUY Condition:
                    # Candle Close > Ref High AND Candle Low < Ref High
                    # (Meaning it started/dipped below the level and closed above it)
                    if curr_close > ref_high and curr_low < ref_high:
                        final_signal = SignalType.BUY
                        used_timeframe_name = name
                        triggered_level = ref_high
                        break # Prioritize higher timeframe (Month checked first)

                    # SELL Condition:
                    # Candle Close < Ref Low AND Candle High > Ref Low
                    # (Meaning it started/spiked above the level and closed below it)
                    elif curr_close < ref_low and curr_high > ref_low:
                        final_signal = SignalType.SELL
                        used_timeframe_name = name
                        triggered_level = ref_low
                        break

            if final_signal != SignalType.HOLD:
                # Calculate Confidence
                base_conf = 60 
                
                # 1. Breakout Strength (how far it moved)
                diff = abs(curr_close - triggered_level)
                strength = (diff / triggered_level) * 100
                base_conf += min(strength * 5, 20)
                
                # 2. Candle Color Alignment
                is_green = curr_close >= curr_open
                if (final_signal == SignalType.BUY and is_green) or (final_signal == SignalType.SELL and not is_green):
                    base_conf += 10
                    
                # 3. Volume Confirmation
                if avg_vol_15m > 0 and curr_vol_15m > avg_vol_15m:
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
                    

