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

        try:
            # Fetch data using our data provider
            df = fetch_historical_data(symbol, period=30, interval="15m")

            if df.empty or len(df) < 20: 
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

            # ==========================================
            # 1. PROCESS 15-MINUTE TIMEFRAME
            # ==========================================
            
            # --- Calculate PriceToEMA (15m) ---
            df_ema = ta.ema(df['Close'], length=300)
            df["PriceToEMA"] = (df["Close"] - df_ema) / df_ema * 100

            # --- Inside Bar Detection (15m) ---
            is_inside_bar = (df['High'].shift(1) > df['High']) & (df['Low'].shift(1) < df['Low'])
            
            # --- Mother Bar Detection (15m) ---
            Mother_Bar = (df['High'].shift(1) > df['High'].shift(2)) & (df['Low'].shift(1) < df['Low'].shift(2))

            # --- Breakout Signals (15m) ---
            # Inside Bar Breakout
            ib_buy_condition = is_inside_bar.shift(-1) & (df['Close'] > df['High'].shift(2))
            ib_sell_condition = is_inside_bar.shift(-1) & (df['Close'] < df['Low'].shift(2))
            
            # Mother Candle Breakout
            MCBuy_Signal = (Mother_Bar == True) & (df['Close'] > df['High'].shift(2)) & (df["PriceToEMA"] <= -1)
            MCSell_Signal = (Mother_Bar == True) & (df['Close'] < df['Low'].shift(2)) & (df["PriceToEMA"] >= 1)

            # Combine (15m)
            combined_buy = ib_buy_condition | MCBuy_Signal
            combined_sell = ib_sell_condition | MCSell_Signal
            
            df['Action'] = np.select([combined_buy, combined_sell], [1, -1], default=0)
            
            # Latest 15m State
            latest_15m = df.iloc[-1]
            signal_15m = 0 # 0: Hold, 1: Buy, -1: Sell
            if latest_15m['Action'] == 1: signal_15m = 1
            elif latest_15m['Action'] == -1: signal_15m = -1

            # ==========================================
            # 2. PROCESS 1-HOUR TIMEFRAME (Resampling)
            # ==========================================
            
            ohlc_dict = {
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }
            df_1h = df.resample('1h').agg(ohlc_dict).dropna()
            
            signal_1h = 0
            latest_1h = None
            
            if not df_1h.empty and len(df_1h) > 20: 
                 # --- Calculate PriceToEMA (1H) ---
                df_1h_ema = ta.ema(df_1h['Close'], length=300)
                # Handle possible NaN if not enough data for 300 EMA on 1h (need 300 hours ~ 12 days)
                # We fetched 30 days, so 30 * 24 = 720 candles. Should be fine.
                
                df_1h["PriceToEMA"] = (df_1h["Close"] - df_1h_ema) / df_1h_ema * 100
                
                # --- Inside Bar Detection (1H) ---
                is_ib_1h = (df_1h['High'].shift(1) > df_1h['High']) & (df_1h['Low'].shift(1) < df_1h['Low'])
                
                # --- Mother Bar Detection (1H) ---
                mb_1h = (df_1h['High'].shift(1) > df_1h['High'].shift(2)) & (df_1h['Low'].shift(1) < df_1h['Low'].shift(2))
                
                # --- Breakout Signals (1H) ---
                # Inside Bar
                ib_buy_1h = is_ib_1h.shift(-1) & (df_1h['Close'] > df_1h['High'].shift(2))
                ib_sell_1h = is_ib_1h.shift(-1) & (df_1h['Close'] < df_1h['Low'].shift(2))
                
                # Mother Candle
                # Note: PriceToEMA might be NaN if not enough data, treat as False condition safely
                p_ema_ok_buy = (df_1h["PriceToEMA"] <= -1).fillna(False)
                p_ema_ok_sell = (df_1h["PriceToEMA"] >= 1).fillna(False)
                
                mc_buy_1h = (mb_1h == True) & (df_1h['Close'] > df_1h['High'].shift(2)) & p_ema_ok_buy
                mc_sell_1h = (mb_1h == True) & (df_1h['Close'] < df_1h['Low'].shift(2)) & p_ema_ok_sell
                
                # Combine (1H)
                comb_buy_1h = ib_buy_1h | mc_buy_1h
                comb_sell_1h = ib_sell_1h | mc_sell_1h
                
                df_1h['Action'] = np.select([comb_buy_1h, comb_sell_1h], [1, -1], default=0)
                
                latest_1h = df_1h.iloc[-1]
                if latest_1h['Action'] == 1: signal_1h = 1
                elif latest_1h['Action'] == -1: signal_1h = -1


            # ==========================================
            # 3. DETERMINE FINAL SIGNAL
            # ==========================================
            # Preference: 1H Signal > 15m Signal?
            # Or just report whichever is active.
            # StrategyResult only holds one signal.
            
            final_signal_type = SignalType.HOLD
            used_timeframe = "None"
            active_latest_row = latest_15m # Default for price/metadata
            
            if signal_1h != 0:
                # 1H Signal detected
                final_signal_type = SignalType.BUY if signal_1h == 1 else SignalType.SELL
                used_timeframe = "1H"
                active_latest_row = latest_1h
            elif signal_15m != 0:
                # 15m Signal detected (and no 1H signal)
                final_signal_type = SignalType.BUY if signal_15m == 1 else SignalType.SELL
                used_timeframe = "15m"
                active_latest_row = latest_15m

            # ==========================================
            # 4. CALCULATE CONFIDENCE
            # ==========================================
            confidence = 0.0
            
            if final_signal_type != SignalType.HOLD:
                # Pattern confirmation
                pattern_confidence = 30 # Base
                
                # PriceToEMA confirmation
                ema_confirmation = 0
                # Check PriceToEMA if available
                pt_ema = active_latest_row.get('PriceToEMA', 0)
                if pd.isna(pt_ema): pt_ema = 0
                
                if final_signal_type == SignalType.BUY and pt_ema < 0:
                     ema_confirmation = min(abs(pt_ema) * 5, 30)
                elif final_signal_type == SignalType.SELL and pt_ema > 0:
                     ema_confirmation = min(abs(pt_ema) * 5, 30)
                
                # Candle confirmation (using Candle color helper if present, else derive)
                candle_confirmation = 0
                is_green = active_latest_row['Close'] >= active_latest_row['Open']
                if final_signal_type == SignalType.BUY and is_green:
                    candle_confirmation = 20
                elif final_signal_type == SignalType.SELL and not is_green:
                    candle_confirmation = 20
                
                # Volume confirmation
                volume_confirmation = 0
                # Check volume vs average if available
                # Logic: Is current volume > avg volume of THAT timeframe?
                current_vol = active_latest_row['Volume']
                
                # Calculate avg volume for the used timeframe
                avg_vol = 0
                if used_timeframe == "1H" and df_1h is not None:
                     avg_vol = df_1h['Volume'].tail(20).mean()
                elif used_timeframe == "15m":
                     avg_vol = df['Volume'].tail(20).mean()
                     
                if avg_vol > 0 and current_vol > avg_vol:
                    volume_confirmation = 20
                    
                confidence = min(pattern_confidence + ema_confirmation + candle_confirmation + volume_confirmation, 100)

            # Execution time
            execution_time = time.time() - start_time
            current_price = latest_15m['Close'] # Always report latest market price (15m close is fresher)

            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=final_signal_type,
                confidence=round(confidence / 100.0, 3),
                execution_time=execution_time,
                price=round(current_price, 2),
                timestamp=datetime.now(timezone.utc),
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
                timestamp=datetime.now(timezone.utc),
                price=0.0
            )
