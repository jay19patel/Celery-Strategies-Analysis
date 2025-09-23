"""
Celery tasks for stock analysis strategy execution.
Contains all task definitions for parallel strategy processing.
"""

import time
from typing import List
from celery import group
from core.celery_config import celery_app

@celery_app.task(bind=True, name='core.celery_tasks.execute_symbol_strategy')
def execute_symbol_strategy(self, symbol: str, strategy_name: str):
    """
    Celery task to execute a single strategy for a single symbol.

    Args:
        symbol: Stock symbol
        strategy_name: Strategy name to execute

    Returns:
        dict: Strategy execution result
    """
    try:
        start_time = time.time()

        # Import strategy classes dynamically
        from strategies.ema_strategy import EMAStrategy
        from strategies.rsi_strategy import RSIStrategy
        from strategies.custom_strategy import CustomStrategy

        strategy_classes = {
            'EMA Strategy': EMAStrategy,
            'RSI Strategy': RSIStrategy,
            'Custom Strategy': CustomStrategy,
        }

        strategy_class = strategy_classes.get(strategy_name)
        if not strategy_class:
            raise ValueError(f"Unknown strategy: {strategy_name}")

        # Execute strategy
        strategy = strategy_class()
        result = strategy.execute()

        execution_time = time.time() - start_time

        # Return serializable result
        return {
            'symbol': symbol,
            'strategy_name': strategy_name,
            'signal_type': result.signal_type.value if hasattr(result.signal_type, 'value') else str(result.signal_type),
            'confidence': result.confidence,
            'price': result.price,
            'execution_time': execution_time,
            'timestamp': result.timestamp.isoformat() if hasattr(result.timestamp, 'isoformat') else str(result.timestamp),
            'task_id': self.request.id
        }

    except Exception as exc:
        # Retry failed tasks up to 3 times
        raise self.retry(exc=exc, countdown=60, max_retries=3)

@celery_app.task(name='core.celery_tasks.execute_symbol_batch')
def execute_symbol_batch(symbols: List[str], strategy_names: List[str], batch_id: str):
    """
    Celery task to execute multiple symbols with multiple strategies in parallel.

    Args:
        symbols: List of stock symbols
        strategy_names: List of strategy names
        batch_id: Batch identifier

    Returns:
        dict: Batch execution summary
    """
    try:
        start_time = time.time()

        # Create group of parallel tasks
        task_group = group(
            execute_symbol_strategy.s(symbol, strategy_name)
            for symbol in symbols
            for strategy_name in strategy_names
        )

        # Execute all tasks in parallel
        result = task_group.apply_async()
        results = result.get()

        execution_time = time.time() - start_time

        return {
            'batch_id': batch_id,
            'total_tasks': len(symbols) * len(strategy_names),
            'successful_tasks': len([r for r in results if r is not None]),
            'execution_time': execution_time,
            'symbols': symbols,
            'strategies': strategy_names,
            'results': results
        }

    except Exception as exc:
        raise exc