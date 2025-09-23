"""
Example usage of integrated Celery-based strategy execution.
Demonstrates how to handle overlapping batches of 10 symbols × 10 strategies.
"""

from core.strategy_manager import StrategyManager
from strategies.ema_strategy import EMAStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.custom_strategy import CustomStrategy
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def example_batch_execution():
    """Execute batch of symbols with all strategies using Celery."""
    print("=== Celery Batch Execution (10 symbols × 5 strategies = 50 tasks) ===")

    manager = StrategyManager()
    manager.add_strategy(EMAStrategy)
    manager.add_strategy(RSIStrategy)
    manager.add_strategy(CustomStrategy)

    symbols = ['AAPL', 'GOOGL', 'MSFT', 'TSLA', 'AMZN', 'META', 'NFLX', 'NVDA', 'AMD', 'INTC']

    # Submit batch
    batch_task_id = manager.execute_symbols_strategies_batch(symbols, "batch_001")
    print(f"Batch submitted with task ID: {batch_task_id}")

    # Monitor batch status
    time.sleep(2)
    batch_status = manager.get_batch_results(batch_task_id)
    print(f"Batch status: {batch_status['status']}")

def example_overlapping_batches():
    """Demonstrate overlapping batches - real-world scenario."""
    print("\n=== Overlapping Batches Simulation ===")

    manager = StrategyManager()
    manager.add_strategy(EMAStrategy)
    manager.add_strategy(RSIStrategy)
    manager.add_strategy(CustomStrategy)

    symbols_batch1 = ['AAPL', 'GOOGL', 'MSFT', 'TSLA', 'AMZN']
    symbols_batch2 = ['META', 'NFLX', 'NVDA', 'AMD', 'INTC']

    # Submit first batch
    task1_id = manager.execute_symbols_strategies_batch(symbols_batch1, "batch_001")
    print(f"Batch 1 submitted: {task1_id}")

    # Simulate delay (user calls again)
    time.sleep(1)

    # Submit second batch (overlapping)
    task2_id = manager.execute_symbols_strategies_batch(symbols_batch2, "batch_002")
    print(f"Batch 2 submitted: {task2_id}")

    print("Both batches processing in parallel via Celery queue...")

if __name__ == "__main__":
    print("Stock Analysis Strategy Manager - Celery Batch Processing\n")

    print("Setup required:")
    print("1. Start Redis: redis-server")
    print("2. Start Celery worker: celery -A core.celery_config worker --loglevel=info --concurrency=20")
    print("3. Run examples below\n")

    # Uncomment to test
    # example_batch_execution()
    example_overlapping_batches()

    print("=== Ready for 10+ symbols × 5+ strategies batch processing ===")