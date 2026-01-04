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
    
    Simplified Logic:
    - Iterates through timeframes: 1 Month > 1 Week > 1 Day.
    - BUY: Current Price breaks ABOVE the Previous Candle's High.
    - SELL: Current Price breaks BELOW the Previous Candle's Low.
    - Strict check on Level 1 (Previous) only.
    """

    def __init__(self):
        super().__init__("Previous Day HL Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()

        # 1. Fetch 15m data FIRST to get the authoritative LIVE PRICE
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
            # User specifically asked for Day/Week/Month. 
            # "previd day ya previd month ya previd week"
        ]

        final_signal = SignalType.HOLD
        confidence = 0.0
        used_timeframe_name = "None"
        triggered_level = 0.0
        
        try:
            for tf in timeframes:
                # Fetch data (utilizes the smart caching we implemented earlier)
                # TTLs are handled by default in data_provider based on interval or we could pass them explicitly
                # Since we didn't update default args in data_provider to map interval->ttl automatically inside the function (we did it in strategy),
                # let's pass them here for safety/performance consistency with MotherCandle.
                ttl = 120
                if tf['interval'] == '1M': ttl = 86400
                elif tf['interval'] == '1w': ttl = 14400
                elif tf['interval'] == '1d': ttl = 3600
                
                df = fetch_historical_data(symbol, period=tf["period"], interval=tf["interval"], ttl=ttl)
                
                if df is None or df.empty or len(df) < 2:
                    continue
                
                # Get Previous Candle (Last Closed)
                # Index -1 is current (live/incomplete), Index -2 is Previous
                prev_high = df['High'].iloc[-2]
                prev_low = df['Low'].iloc[-2]
                prev_close = df['Close'].iloc[-2]
                
                # Check Breakout
                buy_signal = False
                sell_signal = False
                
                # BUY: Breakout of Previous High
                if live_price > prev_high:
                    buy_signal = True
                    triggered_level = prev_high
                    
                # SELL: Breakout of Previous Low
                elif live_price < prev_low:
                    sell_signal = True
                    triggered_level = prev_low
                
                # Determine Signal
                if buy_signal:
                    final_signal = SignalType.BUY
                    used_timeframe_name = tf["name"]
                elif sell_signal:
                    final_signal = SignalType.SELL
                    used_timeframe_name = tf["name"]
                
                if final_signal != SignalType.HOLD:
                    # Calculate Confidence
                    
                    # 1. Base Score
                    base_conf = 50
                    
                    # 2. Breakout Strength (How far past level?)
                    diff = abs(live_price - triggered_level)
                    pct_diff = (diff / triggered_level) * 100
                    strength = min(pct_diff * 10, 20) # Up to 20 pts for 2% move
                    
                    # 3. Candle Color Alignment
                    candle_conf = 0
                    is_green = live_price >= df['Open'].iloc[-1] # Current candle color
                    if (buy_signal and is_green) or (sell_signal and not is_green):
                        candle_conf = 15
                        
                    # 4. Volume (if available) -> Check 15m volume intensity
                    vol_conf = 0
                    if df_15m['Volume'].iloc[-1] > df_15m['Volume'].tail(20).mean():
                         vol_conf = 15
                         
                    confidence = min(base_conf + strength + candle_conf + vol_conf, 100)
                    
                    # Stop at highest priority signal
                    break
                    
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
