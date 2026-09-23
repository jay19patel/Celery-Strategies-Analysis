import random
import time
from datetime import datetime, timezone

from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import SignalType, StrategyResult
from app.core.logger import get_strategies_logger
from app.broker.delta.price_feed import get_live_price

logger = get_strategies_logger()


class RandomScalpingStrategy(BaseStrategy):
    """
    A random scalping strategy for testing purposes.
    It does not use historical data, fetches live price from websocket,
    randomly decides to BUY or SELL, and randomly assigns 0.01% or 0.02% stoploss/target.
    """
    def __init__(self):
        super().__init__("Random Scalping Strategy")

    def execute(self, symbol: str) -> StrategyResult:
        start_time = time.time()
        try:
            # 1. Fetch live price from the websocket feed, or use historical fallback if not connected
            current_price = get_live_price(symbol)
            
            if not current_price:
                from app.utility.data_provider import fetch_historical_data
                df = fetch_historical_data(symbol, period=1, interval="15m")
                if not df.empty:
                    current_price = float(df['Close'].iloc[-1])
                    logger.warning("random_scalping strategy=%s symbol=%s msg='No live price available, using historical close=%s'", self.name, symbol, current_price)
                else:
                    logger.warning("random_scalping strategy=%s symbol=%s msg='No live price and no historical data, using 60000.0'", self.name, symbol)
                    current_price = 60000.0

            # 2. Randomly decide BUY, SELL, or HOLD
            signal_choice = random.choice([SignalType.BUY, SignalType.SELL])
            
            # 3. Randomly set stop loss and target percentage (0.01% or 0.02%)
            sl_tp_pct = random.choice([0.0001, 0.0002]) # 0.01% or 0.02%

            if signal_choice == SignalType.BUY:
                stop_loss = current_price * (1 - sl_tp_pct)
                target = current_price * (1 + sl_tp_pct)
                logger.info(
                    "strategy_signal strategy=%s symbol=%s signal=BUY price=%s SL=%s TP=%s pct=%s",
                    self.name, symbol, current_price, stop_loss, target, sl_tp_pct
                )
            elif signal_choice == SignalType.SELL:
                stop_loss = current_price * (1 + sl_tp_pct)
                target = current_price * (1 - sl_tp_pct)
                logger.info(
                    "strategy_signal strategy=%s symbol=%s signal=SELL price=%s SL=%s TP=%s pct=%s",
                    self.name, symbol, current_price, stop_loss, target, sl_tp_pct
                )
            else:
                stop_loss = None
                target = None
                logger.info("strategy_signal strategy=%s symbol=%s signal=HOLD price=%s", self.name, symbol, current_price)

            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=signal_choice,
                execution_time=time.time() - start_time,
                timestamp=datetime.now(timezone.utc),
                price=round(current_price, 2),
                stop_loss=round(stop_loss, 2) if stop_loss else None,
                take_profit=round(target, 2) if target else None,
                success=True,
            )

        except Exception as e:
            logger.exception("strategy_execution_failed strategy=%s symbol=%s error=%s", self.name, symbol, e)
            return StrategyResult(
                strategy_name=self.name,
                symbol=symbol,
                signal_type=SignalType.HOLD,
                execution_time=time.time() - start_time,
                timestamp=datetime.now(timezone.utc),
                price=0.0,
                success=False,
            )
