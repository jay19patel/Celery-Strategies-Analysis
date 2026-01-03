import time
from datetime import datetime, timezone
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
            # Fetch Daily data for calculations (180 days for ~6 months back)
            df_daily_raw = fetch_historical_data(symbol, period=180, interval="1d")
            
            # Fetch 15m data for signals (shorter period is fine)
            df = fetch_historical_data(symbol, period=5, interval="15m")

            if df_daily_raw.empty or len(df_daily_raw) < 90: # Need enough data for months
                # Fallback or error
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

            # --- Resampling to Weekly and Monthly ---
            ohlc_dict = {'High': 'max', 'Low': 'min'}
            df_weekly = df_daily_raw.resample('W').agg(ohlc_dict)
            df_monthly = df_daily_raw.resample('M').agg(ohlc_dict)
            
            # --- Prepare Stats DataFrame (on Daily Index) ---
            # We will map everything to the Daily Index first, then to 15m
            stats_df = pd.DataFrame(index=df_daily_raw.index)
            
            # 1. Daily Levels (1, 2, 3)
            # Prev Day 1 (Yesterday)
            stats_df['PD_H1'] = df_daily_raw['High'].shift(1)
            stats_df['PD_L1'] = df_daily_raw['Low'].shift(1)
            # Prev Day 2
            stats_df['PD_H2'] = df_daily_raw['High'].shift(2)
            stats_df['PD_L2'] = df_daily_raw['Low'].shift(2)
            # Prev Day 3
            stats_df['PD_H3'] = df_daily_raw['High'].shift(3)
            stats_df['PD_L3'] = df_daily_raw['Low'].shift(3)

            # 2. Weekly Levels (1, 2, 3)
            # Use bfill to align "Week Ending Sunday" value to all days of that week
            # Shift 1 means "Last Week's High" assigned to "This Week's Sunday"
            for i in range(1, 4):
                 # Shift weekly data
                w_shifted = df_weekly.shift(i)
                # Reindex to daily, backfilling so days in Week X get value from Week X-end
                w_reindexed = w_shifted.reindex(stats_df.index, method='bfill')
                stats_df[f'PW_H{i}'] = w_reindexed['High']
                stats_df[f'PW_L{i}'] = w_reindexed['Low']

            # 3. Monthly Levels (1, 2, 3)
            for i in range(1, 4):
                m_shifted = df_monthly.shift(i)
                m_reindexed = m_shifted.reindex(stats_df.index, method='bfill')
                stats_df[f'PM_H{i}'] = m_reindexed['High']
                stats_df[f'PM_L{i}'] = m_reindexed['Low']

            # --- Map Stats to 15m DataFrame ---
            # Map based on Date (normalization)
            # If 15m date is not in stats_df (e.g. today is new day not in daily yet?), use ffill
            # Actually, normalize map is safer if stats_df has the date.
            
            # Ensure stats_df has rows for all dates in df
            # If df has today's data but df_daily doesn't have today's row (fetched earlier?),
            # fetch_historical_data usually returns up to now.
            
            # Perform mapping
            cols_to_map = [c for c in stats_df.columns]
            for col in cols_to_map:
                df[col] = df.index.normalize().map(stats_df[col])
            
            # Forward fill any missing values (e.g. if today's date missing in daily?)
            df[cols_to_map] = df[cols_to_map].ffill()
            
            # Drop rows with NaN in critical columns (start of data)
            # Check a few key ones
            df = df.dropna(subset=['PD_H1', 'PW_H1', 'PM_H1'])

            if df.empty or len(df) < 2:
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

            # --- Step 4: Get previous candle close ---
            prev_close = df['Close'].shift(1)

            # --- Step 5: Define Breakout Conditions ---
            # User Request: "High ko break then buy and Low ko break then sell" (Trend Breakout)
            
            # SELL Conditions: Price crosses BELOW a Low level
            # Logic: Prev Close > Level AND Current Close < Level
            
            # Daily Lows
            is_break_daily_sell = (
                ((prev_close > df['PD_L1']) & (df['Close'] < df['PD_L1'])) |
                ((prev_close > df['PD_L2']) & (df['Close'] < df['PD_L2'])) |
                ((prev_close > df['PD_L3']) & (df['Close'] < df['PD_L3']))
            )
            # Weekly Lows
            is_break_weekly_sell = (
                ((prev_close > df['PW_L1']) & (df['Close'] < df['PW_L1'])) |
                ((prev_close > df['PW_L2']) & (df['Close'] < df['PW_L2'])) |
                ((prev_close > df['PW_L3']) & (df['Close'] < df['PW_L3']))
            )
            # Monthly Lows
            is_break_monthly_sell = (
                ((prev_close > df['PM_L1']) & (df['Close'] < df['PM_L1'])) |
                ((prev_close > df['PM_L2']) & (df['Close'] < df['PM_L2'])) |
                ((prev_close > df['PM_L3']) & (df['Close'] < df['PM_L3']))
            )
            
            # BUY Conditions: Price crosses ABOVE a High level
            # Logic: Prev Close < Level AND Current Close > Level
            
            # Daily Highs
            is_break_daily_buy = (
                ((prev_close < df['PD_H1']) & (df['Close'] > df['PD_H1'])) |
                ((prev_close < df['PD_H2']) & (df['Close'] > df['PD_H2'])) |
                ((prev_close < df['PD_H3']) & (df['Close'] > df['PD_H3']))
            )
            # Weekly Highs
            is_break_weekly_buy = (
                ((prev_close < df['PW_H1']) & (df['Close'] > df['PW_H1'])) |
                ((prev_close < df['PW_H2']) & (df['Close'] > df['PW_H2'])) |
                ((prev_close < df['PW_H3']) & (df['Close'] > df['PW_H3']))
            )
            # Monthly Highs
            is_break_monthly_buy = (
                ((prev_close < df['PM_H1']) & (df['Close'] > df['PM_H1'])) |
                ((prev_close < df['PM_H2']) & (df['Close'] > df['PM_H2'])) |
                ((prev_close < df['PM_H3']) & (df['Close'] > df['PM_H3']))
            )
            
            # Combine
            sell_condition = is_break_daily_sell | is_break_weekly_sell | is_break_monthly_sell
            buy_condition = is_break_daily_buy | is_break_weekly_buy | is_break_monthly_buy

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
                    timestamp=datetime.now(timezone.utc),
                    price=0.0
                )

            # Get latest row
            latest = df.iloc[-1]
            current_price = latest['Close']
            
            # Re-evaluate latest conditions to identify specific level for confidence
            l_prev_close = df['Close'].iloc[-2] # Previous candle close relative to latest
            l_close = latest['Close']
            
            triggered_level = 0.0
            
            # Helper for check
            def check_cross_up(level): return (l_prev_close < level) and (l_close > level)
            def check_cross_down(level): return (l_prev_close > level) and (l_close < level)
            
            # Determine signal type
            signal_type = SignalType.HOLD
            if latest['PDHLAction'] == 1:
                signal_type = SignalType.BUY
                # BUY triggers on HIGH levels
                # Check Monthly Highs
                if check_cross_up(latest['PM_H1']): triggered_level = latest['PM_H1']
                elif check_cross_up(latest['PM_H2']): triggered_level = latest['PM_H2']
                elif check_cross_up(latest['PM_H3']): triggered_level = latest['PM_H3']
                # Check Weekly Highs
                elif check_cross_up(latest['PW_H1']): triggered_level = latest['PW_H1']
                elif check_cross_up(latest['PW_H2']): triggered_level = latest['PW_H2']
                elif check_cross_up(latest['PW_H3']): triggered_level = latest['PW_H3']
                # Check Daily Highs
                elif check_cross_up(latest['PD_H1']): triggered_level = latest['PD_H1']
                elif check_cross_up(latest['PD_H2']): triggered_level = latest['PD_H2']
                elif check_cross_up(latest['PD_H3']): triggered_level = latest['PD_H3']
                else: triggered_level = latest['PD_H1'] # Fallback
                
            elif latest['PDHLAction'] == -1:
                signal_type = SignalType.SELL
                # SELL triggers on LOW levels
                # Check Monthly Lows
                if check_cross_down(latest['PM_L1']): triggered_level = latest['PM_L1']
                elif check_cross_down(latest['PM_L2']): triggered_level = latest['PM_L2']
                elif check_cross_down(latest['PM_L3']): triggered_level = latest['PM_L3']
                # Check Weekly Lows
                elif check_cross_down(latest['PW_L1']): triggered_level = latest['PW_L1']
                elif check_cross_down(latest['PW_L2']): triggered_level = latest['PW_L2']
                elif check_cross_down(latest['PW_L3']): triggered_level = latest['PW_L3']
                # Check Daily Lows
                elif check_cross_down(latest['PD_L1']): triggered_level = latest['PD_L1']
                elif check_cross_down(latest['PD_L2']): triggered_level = latest['PD_L2']
                elif check_cross_down(latest['PD_L3']): triggered_level = latest['PD_L3']
                else: triggered_level = latest['PD_L1'] # Fallback

            # Calculate confidence
            confidence = 0.0
            if signal_type != SignalType.HOLD and triggered_level != 0:
                # Base confidence for PDHL breakout signal
                base_confidence = 40

                # Breakout strength: how far price has moved through the level
                breakout_diff = abs(latest['Close'] - triggered_level)
                breakout_strength = breakout_diff / triggered_level
                strength_confidence = min(breakout_strength * 5000, 30)  # Up to 30 points

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
                timestamp=datetime.now(timezone.utc),
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
                timestamp=datetime.now(timezone.utc),
                price=0.0
            )
