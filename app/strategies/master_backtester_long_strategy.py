"""LONG-only strategy ported from the Master Backtester project's exhaustive
combo search (best LONG combo by raw PnL, out of ~350K candidates tested on
ETHUSD 1h history).

IMPORTANT CAVEAT: when validated on an out-of-sample period (a full year of
data this combo was never searched/fitted against), this specific combo
LOST money (-3.2% vs +248.6% in-sample) - a strong sign the in-sample result
was overfitting/noise, not a real edge. It's included here for completeness
and comparison, but its live BUY signals should be treated with real
skepticism, not acted on with confidence. See master_backtester_short_strategy.py
(the SHORT counterpart), which DID hold up out-of-sample.
"""

import time
from datetime import datetime, timezone

from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import SignalType, StrategyResult
from app.strategies._master_backtester_features import STRATEGIES, build_features, latest_signal
from app.utility.data_provider import fetch_historical_data

STRATEGY_KEY = "strategy_01_long"
FETCH_PERIOD_DAYS = 30  # >= CONDITION_WINDOW(100 bars)/24 + every indicator's own warmup, with margin
FETCH_INTERVAL = "1h"  # matches the interval this combo was found/validated on


class MasterBacktesterLongStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("Master Backtester - Best LONG Combo (unvalidated out-of-sample)")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()
        try:
            df = fetch_historical_data(symbol, period=FETCH_PERIOD_DAYS, interval=FETCH_INTERVAL)
            if df is None or df.empty or len(df) < 150:
                # Not enough history yet for this combo's indicators (needs
                # ~100+ bars) to be meaningful - HOLD rather than guess.
                return StrategyResult(
                    strategy_name=self.name,
                    symbol=symbol,
                    signal_type=SignalType.HOLD,
                    confidence=0.0,
                    execution_time=time.time() - start_time,
                    timestamp=datetime.now(timezone.utc),
                    price=0.0,
                    success=False,
                )

            features_df = build_features(df)
            direction, price = latest_signal(features_df, STRATEGY_KEY)

            signal_type = SignalType.BUY if direction == STRATEGIES[STRATEGY_KEY]["direction"] else SignalType.HOLD
            confidence = 1.0 if signal_type != SignalType.HOLD else 0.0

            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=signal_type,
                confidence=confidence,
                execution_time=time.time() - start_time,
                timestamp=datetime.now(timezone.utc),
                price=round(price, 2),
                success=True,
            )

        except Exception as e:
            print(f"Error in MasterBacktesterLongStrategy for {symbol}: {str(e)}")
            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                confidence=0.0,
                execution_time=time.time() - start_time,
                timestamp=datetime.now(timezone.utc),
                price=0.0,
                success=False,
            )
