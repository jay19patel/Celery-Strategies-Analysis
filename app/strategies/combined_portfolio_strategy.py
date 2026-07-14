import time
from datetime import datetime, timezone

from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import SignalType, StrategyResult
from app.utility.features import STRATEGIES, build_features, latest_signal
from app.utility.data_provider import fetch_historical_data
from app.core.logger import get_strategies_logger

logger = get_strategies_logger()

FETCH_PERIOD_DAYS = 30
FETCH_INTERVAL = "1h"


class CombinedPortfolioStrategy(BaseStrategy):
    """
    Combined strategy that incorporates both the best LONG and best SHORT combos 
    from the Portfolio project's exhaustive search.
    """
    def __init__(self):
        super().__init__("Combined Portfolio Strategy (Long & Short)")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()
        try:
            df = fetch_historical_data(symbol, period=FETCH_PERIOD_DAYS, interval=FETCH_INTERVAL)
            if df is None or df.empty or len(df) < 150:
                # Not enough history yet for this combo's indicators
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
            
            # Check LONG signal
            dir_long, price = latest_signal(features_df, "strategy_01_long")
            # Check SHORT signal
            dir_short, _ = latest_signal(features_df, "strategy_02_short")

            signal_type = SignalType.HOLD
            confidence = 0.0

            is_buy = (dir_long == STRATEGIES["strategy_01_long"]["direction"])
            is_sell = (dir_short == STRATEGIES["strategy_02_short"]["direction"])

            if is_buy and not is_sell:
                signal_type = SignalType.BUY
                confidence = 1.0
            elif is_sell and not is_buy:
                signal_type = SignalType.SELL
                confidence = 1.0

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
            logger.error(f"❌ Error in CombinedPortfolioStrategy for {symbol}: {str(e)}", exc_info=True)
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
