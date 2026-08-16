import time
from datetime import datetime, timezone
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult, SignalType
from app.utility.data_provider import fetch_historical_data
import numpy as np
import pandas_ta as ta
from app.core.logger import get_strategies_logger

logger = get_strategies_logger()


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
             logger.error(f"❌ Error fetching 15m data for {symbol}: {e}")

        if df_15m is None or df_15m.empty:
             execution_time = time.time() - start_time
             return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                execution_time=execution_time,
                timestamp=datetime.now(timezone.utc),
                price=0.0
             )

        # NOTE: strategy_name must stay exactly self.name across every return path below.
        # PaperBroker keys accounts by the exact strategy_name string (see CLAUDE.md /
        # app/core/paper_broker.py) - it used to vary here with "(Insufficient Data)" or the
        # matched timeframe name (e.g. "(15 Minute)"), which silently fragmented the account
        # on every cycle where a different suffix (or no suffix) was returned. In particular,
        # check_protective_exit() - which enforces the SL/TP/24h exits - was then called against
        # whichever fragmented account matched *that* cycle's name, so the account holding the
        # actual open position only got checked on the rare cycles where the same timeframe
        # fired again, letting positions sit open far past the 24h limit. Log the sub-detail
        # instead of folding it into the name.

        # Live Price from latest 15m candle
        live_price = df_15m['Close'].iloc[-1]
        
        final_signal = SignalType.HOLD
        
        try:
            if len(df_15m) < 2:
                 logger.info(f"📊 MotherCandleStrategy | {symbol} | Insufficient Data")
                 execution_time = time.time() - start_time
                 return StrategyResult(
                    strategy_name=self.name,
                    symbol=symbol,
                    signal_type=SignalType.HOLD,
                    execution_time=execution_time,
                    timestamp=datetime.now(timezone.utc),
                    price=round(live_price, 2)
                 )

            curr_close = df_15m['Close'].iloc[-2]
            curr_high = df_15m['High'].iloc[-2]
            curr_low = df_15m['Low'].iloc[-2]
            
            avg_vol_15m = df_15m['Volume'].iloc[-6:-2].mean() if len(df_15m) > 6 else 0
            curr_vol_15m = df_15m['Volume'].iloc[-2]

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

                if df_tf is None or df_tf.empty or len(df_tf) < 4:
                    continue
                    
                # Identify Candles
                
                mother_high = df_tf['High'].iloc[-3]
                mother_low = df_tf['Low'].iloc[-3]
                mother_close = df_tf['Close'].iloc[-3]
                
                child_high = df_tf['High'].iloc[-2]
                child_low = df_tf['Low'].iloc[-2]
                
                # Check 1: Inside Bar Condition (Child inside Mother)
                is_inside_bar = (child_high <= mother_high) and (child_low >= mother_low)
                
                if not is_inside_bar:
                    continue
                
                trigger_price = df_15m['Close'].iloc[-1]
                trigger_low = df_15m['Low'].iloc[-1]
                trigger_high = df_15m['High'].iloc[-1]
                
                mc_buy = False
                mc_sell = False
                
                if (trigger_price > mother_high) and (trigger_low < mother_close):
                    mc_buy = True
                
                # SELL: Current Close < Mother Low AND Current High > Mother Close (Dip)
                elif (trigger_price < mother_low) and (trigger_high > mother_close):
                    mc_sell = True
                    
                if mc_buy:
                    final_signal = SignalType.BUY
                    used_timeframe_name = tf["name"]
                    break
                elif mc_sell:
                    final_signal = SignalType.SELL
                    used_timeframe_name = tf["name"]
                    break
        except Exception as e:
            logger.error(f"❌ Error in MotherCandleStrategy processing {symbol}: {str(e)}", exc_info=True)

        execution_time = time.time() - start_time

        if final_signal != SignalType.HOLD:
            logger.info(f"📊 MotherCandleStrategy | {symbol} | triggered by {used_timeframe_name} | {final_signal}")

        return StrategyResult(
            strategy_name=self.name,
            symbol=symbol,
            signal_type=final_signal,
            execution_time=execution_time,
            price=round(live_price, 2),
            timestamp=datetime.now(timezone.utc),
            success=True
        )
