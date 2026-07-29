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
                    execution_time=time.time() - start_time,
                    timestamp=datetime.now(timezone.utc),
                    price=0.0,
                    success=False,
                )

            features_df = build_features(df)
            
            signal_type = SignalType.HOLD
            confidence = 0.0
            triggered_strategy_key = None
            price = df['Close'].iloc[-1]

            # Check LONG strategies (OR logic)
            is_buy = False
            for i in range(1, 11):
                strat_key = f"long_{i:02d}"
                if strat_key in STRATEGIES:
                    direction, current_price = latest_signal(features_df, strat_key)
                    price = current_price  # ensure we have the latest price
                    if direction == STRATEGIES[strat_key]["direction"]:
                        is_buy = True
                        triggered_strategy_key = strat_key
                        break

            # Check SHORT strategies (OR logic)
            is_sell = False
            for i in range(1, 11):
                strat_key = f"short_{i:02d}"
                if strat_key in STRATEGIES:
                    direction, current_price = latest_signal(features_df, strat_key)
                    price = current_price
                    if direction == STRATEGIES[strat_key]["direction"]:
                        is_sell = True
                        # If it's both buy and sell, standard practice is to hold (cancel out),
                        # but we check if only sell is triggered.
                        if not is_buy:
                            triggered_strategy_key = strat_key
                        break

            # Resolve signal
            if is_buy and not is_sell:
                signal_type = SignalType.BUY
            elif is_sell and not is_buy:
                signal_type = SignalType.SELL

            if triggered_strategy_key:
                logger.info(f"📊 CombinedPortfolioStrategy | {symbol} | triggered by {triggered_strategy_key} | {signal_type}")

            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=signal_type,
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
                execution_time=time.time() - start_time,
                timestamp=datetime.now(timezone.utc),
                price=0.0,
                success=False,
            )
