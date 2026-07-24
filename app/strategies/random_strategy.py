import random
import time
from datetime import datetime, timezone
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult, SignalType
from app.utility.data_provider import fetch_historical_data
from app.core.logger import get_strategies_logger

logger = get_strategies_logger()


class RandomStrategy(BaseStrategy):
    """
    Randomly emits BUY / SELL / HOLD signals on every run, independent of
    price action. Useful as a noise baseline to compare real strategies
    against, or to exercise the paper-trading pipeline with frequent trades.
    """

    # Weighted so a trade (BUY/SELL) is taken most cycles rather than every cycle
    SIGNAL_WEIGHTS = {
        SignalType.BUY: 0.4,
        SignalType.SELL: 0.4,
        SignalType.HOLD: 0.2,
    }

    def __init__(self):
        super().__init__("Random Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()

        try:
            df = fetch_historical_data(symbol, period=5, interval="15m")
            current_price = round(df['Close'].iloc[-1], 2) if df is not None and not df.empty else 0.0

            signal_type = random.choices(
                list(self.SIGNAL_WEIGHTS.keys()),
                weights=list(self.SIGNAL_WEIGHTS.values()),
            )[0]

            execution_time = time.time() - start_time

            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=signal_type,
                execution_time=execution_time,
                price=current_price,
                timestamp=datetime.now(timezone.utc),
                success=True
            )

        except Exception as e:
            logger.error(f"❌ Error in RandomStrategy for {symbol}: {str(e)}", exc_info=True)
            execution_time = time.time() - start_time
            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                execution_time=execution_time,
                timestamp=datetime.now(timezone.utc),
                price=0.0
            )
