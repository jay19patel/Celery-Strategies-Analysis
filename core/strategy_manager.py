from typing import List, Dict, Any, Type
import logging
import uuid

from core.base_strategy import BaseStrategy
from models.strategy_models import StrategyResult
from core.celery_config import celery_app
from core.celery_tasks import execute_symbol_batch

class StrategyManager:
    """
    Professional Strategy Manager for executing multiple trading strategies in parallel.

    Designed for high-volume batch processing:
    - 10+ symbols × 5+ strategies = 50+ parallel tasks
    - Uses Celery + Redis for efficient task queuing
    - Handles overlapping batch requests automatically
    """

    def __init__(self):
        """Initialize the Strategy Manager."""
        self.strategies: List[BaseStrategy] = []
        self.logger = logging.getLogger(__name__)

    def add_strategy(self, strategy_class: Type[BaseStrategy], *args, **kwargs) -> None:
        """
        Add a strategy to the manager by passing its class.

        Args:
            strategy_class: The strategy class that inherits from BaseStrategy
            *args: Positional arguments to pass to the strategy constructor
            **kwargs: Keyword arguments to pass to the strategy constructor
        """
        try:
            strategy_instance = strategy_class(*args, **kwargs)
            if not isinstance(strategy_instance, BaseStrategy):
                raise TypeError(f"Strategy must inherit from BaseStrategy, got {type(strategy_instance)}")

            self.strategies.append(strategy_instance)
            self.logger.info(f"Added strategy: {strategy_instance.name}")

        except Exception as e:
            self.logger.error(f"Failed to add strategy {strategy_class.__name__}: {str(e)}")
            raise


    def get_strategy_summary(self) -> Dict[str, Any]:
        """
        Get a summary of all registered strategies.

        Returns:
            Dictionary containing strategy information
        """
        return {
            "total_strategies": len(self.strategies),
            "strategy_names": [strategy.name for strategy in self.strategies]
        }


    # === CELERY BATCH PROCESSING FOR HIGH-VOLUME EXECUTION ===

    def execute_symbols_strategies_batch(self, symbols: List[str], batch_id: str = None) -> str:
        """
        Execute all registered strategies for multiple symbols using Celery.
        Designed for 10+ symbols × 5+ strategies = 50+ parallel tasks.

        Args:
            symbols: List of stock symbols (e.g., ['AAPL', 'GOOGL', 'MSFT'])
            batch_id: Optional batch identifier for tracking

        Returns:
            str: Batch task ID for monitoring progress
        """
        if not self.strategies:
            self.logger.warning("No strategies registered for execution")
            return None

        if not symbols:
            self.logger.warning("No symbols provided for execution")
            return None

        # Generate batch ID if not provided
        if not batch_id:
            batch_id = f"batch_{uuid.uuid4().hex[:8]}"

        # Submit batch processing task
        task = execute_symbol_batch.delay(symbols, [s.name for s in self.strategies], batch_id)

        self.logger.info(f"Submitted batch {batch_id} with {len(symbols) * len(self.strategies)} tasks")
        return task.id

    def get_batch_results(self, task_id: str) -> Dict[str, Any]:
        """
        Get results from batch execution.

        Args:
            task_id: Batch task ID to check

        Returns:
            Dict containing batch status and results
        """
        task_result = celery_app.AsyncResult(task_id)
        return {
            'status': task_result.status,
            'result': task_result.result if task_result.successful() else None,
            'error': str(task_result.result) if task_result.failed() else None
        }

