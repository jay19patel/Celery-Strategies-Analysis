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
        
        # 1. Fetch 15m data FIRST to get the authoritative LIVE PRICE and Current Candle
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

        # Live Price from latest 15m candle
        live_price = df_15m['Close'].iloc[-1]
        
        final_signal = SignalType.HOLD
        confidence = 0.0
        
        try:
            # Pre-calculate 15m Candle Levels (Signal Candle)
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
            
            # Additional Volume check for confidence
            # Original: -6:-2. Now we need to be careful with range if we use it.
            avg_vol_15m = df_15m['Volume'].iloc[-6:-2].mean() if len(df_15m) > 6 else 0
            curr_vol_15m = df_15m['Volume'].iloc[-2]

            # --- Multi-Timeframe Patterns ---
            # User Request: "15m, 1 days, 1 week, 1 month ke hisabse"
            # Logic: "Mother Candle" = Inside Bar Pattern.
            # Pattern: Mother Candle (large) -> Child Candle (inside mother) -> Breakout Candle (current signal)
            
            timeframes = [
                {"interval": "15m", "period": 5, "name": "15 Minute"},
                {"interval": "1d", "period": 400, "name": "1 Day"},
                {"interval": "1w", "period": 2100, "name": "1 Week"},
                {"interval": "1M", "period": 5000, "name": "1 Month"},
            ]
            
            used_timeframe_name = "None"

            for tf in timeframes:
                ttl = 120
                if tf['interval'] == '1M': ttl = 86400
                elif tf['interval'] == '1w': ttl = 14400
                elif tf['interval'] == '1d': ttl = 3600
                
                # For 15m, we already have df_15m. For others, fetch.
                if tf['interval'] == '15m':
                    df_tf = df_15m
                else:
                    df_tf = fetch_historical_data(symbol, period=tf["period"], interval=tf["interval"], ttl=ttl)
                
                # Check Data Length
                # We need:
                # Index -4: Mother
                # Index -3: Child (Inside)
                # Index -2: Signal (Breakout, Closed)
                # Index -1: Running (Ignored)
                # So we need at least 4 candles.
                if df_tf is None or df_tf.empty or len(df_tf) < 4:
                    continue
                    
                # Identify Candles
                mother_high = df_tf['High'].iloc[-4]
                mother_low = df_tf['Low'].iloc[-4]
                mother_close = df_tf['Close'].iloc[-4]
                
                child_high = df_tf['High'].iloc[-3]
                child_low = df_tf['Low'].iloc[-3]
                
                # Check 1: Inside Bar Condition (Child inside Mother)
                is_inside_bar = (child_high <= mother_high) and (child_low >= mother_low)
                
                if not is_inside_bar:
                    continue
                    
                # Check 2: Breakout Condition (Signal Candle breaks Mother)
                # We apply the specific "Breakout + Dip" logic requested for Mother Candle.
                
                # Signal Candle is the Current Closed 15m candle?
                # WAIT: If checking 1D mother candle, we compare the *Current 15m Candle* against the 1D Mother Levels.
                # If checking 15m mother candle, we compare *Current 15m* against 15m Mother.
                # The logic above (curr_close vs mother_high) works if we use 15m current price against Reference Levels.
                
                # BUT, if the Reference is 1D, the "Mother" is 2 days ago, "Child" is Yesterday.
                # Breakout happens TODAY (Intraday).
                
                mc_buy = False
                mc_sell = False
                
                # BUY: Intraday Close > Mother High AND Intraday Low < Mother Close (Dip)
                if (curr_close > mother_high) and (curr_low < mother_close):
                    mc_buy = True
                
                # SELL: Intraday Close < Mother Low AND Intraday High > Mother Close (Dip)
                elif (curr_close < mother_low) and (curr_high > mother_close):
                    mc_sell = True
                    
                if mc_buy:
                    final_signal = SignalType.BUY
                    used_timeframe_name = tf["name"]
                    triggered_level = mother_high
                elif mc_sell:
                    final_signal = SignalType.SELL
                    used_timeframe_name = tf["name"]
                    triggered_level = mother_low
                
                if final_signal != SignalType.HOLD:
                    confidence = 80.0
                    # Volume Confirmation (using 15m volume info)
                    if curr_vol_15m > avg_vol_15m:
                        confidence = min(confidence + 10, 100.0)
                    break # Prioritize first found? Or specific order? (15m > 1d ...)
                    # User didn't specify priority, but usually lower TF signals first or higher?
                    # Let's keep loop order: 15m, 1d, 1w... 
        
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
