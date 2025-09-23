import asyncio
import concurrent.futures
from typing import List, Dict, Any, Optional, Type
from datetime import datetime
import logging

from core.base_strategy import BaseStrategy
from models.strategy_models import StrategyResult

class StrategyManager:
    """
    Professional Strategy Manager for executing multiple trading strategies in parallel.

    This class manages a collection of trading strategies and provides methods to:
    - Add strategies dynamically
    - Execute all strategies in parallel for optimal performance
    - Collect and aggregate results from all strategies
    """

    def __init__(self, max_workers: Optional[int] = None):
        """
        Initialize the Strategy Manager.

        Args:
            max_workers: Maximum number of worker threads for parallel execution.
                        If None, uses default ThreadPoolExecutor behavior.
        """
        self.strategies: List[BaseStrategy] = []
        self.max_workers = max_workers
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

    def add_strategy_instance(self, strategy: BaseStrategy) -> None:
        """
        Add a strategy instance directly to the manager.

        Args:
            strategy: An instance of a class that inherits from BaseStrategy
        """
        if not isinstance(strategy, BaseStrategy):
            raise TypeError(f"Strategy must inherit from BaseStrategy, got {type(strategy)}")

        self.strategies.append(strategy)
        self.logger.info(f"Added strategy instance: {strategy.name}")

    def execute_all_strategies_parallel(self) -> List[StrategyResult]:
        """
        Execute all registered strategies in parallel using ThreadPoolExecutor.

        This method provides the best performance for I/O bound operations and
        strategies that may involve network calls or file operations.

        Returns:
            List of StrategyResult objects from all executed strategies
        """
        if not self.strategies:
            self.logger.warning("No strategies registered for execution")
            return []

        start_time = datetime.now()
        results = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all strategy executions
            future_to_strategy = {
                executor.submit(strategy.execute): strategy
                for strategy in self.strategies
            }

            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_to_strategy):
                strategy = future_to_strategy[future]
                try:
                    result = future.result()
                    results.append(result)
                    self.logger.info(f"Strategy '{strategy.name}' completed successfully")

                except Exception as e:
                    self.logger.error(f"Strategy '{strategy.name}' failed: {str(e)}")
                    # Continue with other strategies even if one fails

        end_time = datetime.now()
        total_execution_time = (end_time - start_time).total_seconds()

        self.logger.info(f"Executed {len(results)} strategies in {total_execution_time:.2f} seconds")
        return results

    async def execute_all_strategies_async(self) -> List[StrategyResult]:
        """
        Execute all registered strategies asynchronously using asyncio.

        This method is ideal for truly asynchronous operations and provides
        better resource utilization for concurrent execution.

        Returns:
            List of StrategyResult objects from all executed strategies
        """
        if not self.strategies:
            self.logger.warning("No strategies registered for execution")
            return []

        start_time = datetime.now()

        # Create tasks for all strategy executions
        tasks = []
        for strategy in self.strategies:
            task = asyncio.create_task(self._execute_strategy_async(strategy))
            tasks.append(task)

        # Wait for all tasks to complete
        results = []
        completed_tasks = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(completed_tasks):
            strategy = self.strategies[i]
            if isinstance(result, Exception):
                self.logger.error(f"Strategy '{strategy.name}' failed: {str(result)}")
            else:
                results.append(result)
                self.logger.info(f"Strategy '{strategy.name}' completed successfully")

        end_time = datetime.now()
        total_execution_time = (end_time - start_time).total_seconds()

        self.logger.info(f"Executed {len(results)} strategies in {total_execution_time:.2f} seconds")
        return results

    async def _execute_strategy_async(self, strategy: BaseStrategy) -> StrategyResult:
        """
        Helper method to execute a single strategy asynchronously.

        Args:
            strategy: The strategy to execute

        Returns:
            StrategyResult from the executed strategy
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, strategy.execute)

    def get_strategy_summary(self) -> Dict[str, Any]:
        """
        Get a summary of all registered strategies.

        Returns:
            Dictionary containing strategy information
        """
        return {
            "total_strategies": len(self.strategies),
            "strategy_names": [strategy.name for strategy in self.strategies],
            "max_workers": self.max_workers
        }

    def clear_strategies(self) -> None:
        """Remove all registered strategies from the manager."""
        self.strategies.clear()
        self.logger.info("All strategies cleared from manager")

    def remove_strategy(self, strategy_name: str) -> bool:
        """
        Remove a specific strategy by name.

        Args:
            strategy_name: Name of the strategy to remove

        Returns:
            True if strategy was removed, False if not found
        """
        for i, strategy in enumerate(self.strategies):
            if strategy.name == strategy_name:
                removed_strategy = self.strategies.pop(i)
                self.logger.info(f"Removed strategy: {removed_strategy.name}")
                return True

        self.logger.warning(f"Strategy '{strategy_name}' not found")
        return False