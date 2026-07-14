"""SHORT-only strategy ported from the Master Backtester project's exhaustive
combo search (best SHORT combo by raw PnL, out of ~350K candidates tested on
ETHUSD 1h history).

Validated on an out-of-sample period (a full year this combo was never
searched/fitted against) and RETAINED a real, positive edge (+74.2%
out-of-sample vs +630.6% in-sample - weaker than in-sample, as expected, but
still genuinely profitable). More trustworthy than its LONG counterpart
(master_backtester_long_strategy.py), which did not hold up out-of-sample.
"""

import time
from datetime import datetime, timezone

from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import SignalType, StrategyResult
from app.strategies._master_backtester_features import STRATEGIES, build_features, latest_signal
from app.utility.data_provider import fetch_historical_data

STRATEGY_KEY = "strategy_02_short"
FETCH_PERIOD_DAYS = 30  # >= CONDITION_WINDOW(100 bars)/24 + every indicator's own warmup, with margin
FETCH_INTERVAL = "1h"  # matches the interval this combo was found/validated on


class MasterBacktesterShortStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("Master Backtester - Best SHORT Combo (out-of-sample validated)")

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

            signal_type = SignalType.SELL if direction == STRATEGIES[STRATEGY_KEY]["direction"] else SignalType.HOLD
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
            print(f"Error in MasterBacktesterShortStrategy for {symbol}: {str(e)}")
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
