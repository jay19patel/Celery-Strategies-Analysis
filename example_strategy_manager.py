"""
Example usage of StrategyManager with parallel execution.

This file demonstrates how to use the StrategyManager to:
1. Add multiple strategies using their classes
2. Execute all strategies in parallel
3. Handle results from parallel execution
"""

import asyncio
import logging
from core.strategy_manager import StrategyManager
from strategies.ema_strategy import EMAStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.custom_strategy import CustomStrategy

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def main():
    """Demonstrate synchronous parallel execution using ThreadPoolExecutor."""
    print("=== StrategyManager Synchronous Parallel Execution Demo ===\n")

    # Create strategy manager with custom max_workers
    manager = StrategyManager(max_workers=4)

    # Add strategies by passing their classes
    print("Adding strategies to manager...")
    manager.add_strategy(EMAStrategy)
    manager.add_strategy(RSIStrategy)
    manager.add_strategy(CustomStrategy)

    # You can also add strategy instances directly
    # custom_strategy = CustomStrategy()
    # manager.add_strategy_instance(custom_strategy)

    # Display strategy summary
    summary = manager.get_strategy_summary()
    print(f"Strategy Summary: {summary}\n")

    print("Executing all strategies in parallel...")
    print("Note: Each strategy has a random sleep time of 5-10 seconds for demo purposes\n")

    # Execute all strategies in parallel
    results = manager.execute_all_strategies_parallel()

    # Display results
    print("=== Execution Results ===")
    for result in results:
        print(f"Strategy: {result.strategy_name}")
        print(f"Signal: {result.signal_type}")
        print(f"Confidence: {result.confidence}")
        print(f"Execution Time: {result.execution_time:.2f} seconds")
        print(f"Price: ${result.price:.2f}")
        print(f"Timestamp: {result.timestamp}")
        print("-" * 50)

    print(f"\nTotal strategies executed: {len(results)}")

async def async_main():
    """Demonstrate asynchronous parallel execution using asyncio."""
    print("\n=== StrategyManager Asynchronous Parallel Execution Demo ===\n")

    # Create strategy manager
    manager = StrategyManager()

    # Add strategies
    print("Adding strategies to manager...")
    manager.add_strategy(EMAStrategy)
    manager.add_strategy(RSIStrategy)
    manager.add_strategy(CustomStrategy)

    print("Executing all strategies asynchronously...")
    print("Note: Each strategy has a random sleep time of 5-10 seconds for demo purposes\n")

    # Execute all strategies asynchronously
    results = await manager.execute_all_strategies_async()

    # Display results
    print("=== Async Execution Results ===")
    for result in results:
        print(f"Strategy: {result.strategy_name}")
        print(f"Signal: {result.signal_type}")
        print(f"Confidence: {result.confidence}")
        print(f"Execution Time: {result.execution_time:.2f} seconds")
        print(f"Price: ${result.price:.2f}")
        print(f"Timestamp: {result.timestamp}")
        print("-" * 50)

    print(f"\nTotal strategies executed: {len(results)}")

def demo_strategy_management():
    """Demonstrate strategy management features."""
    print("\n=== Strategy Management Demo ===\n")

    manager = StrategyManager()

    # Add strategies
    manager.add_strategy(EMAStrategy)
    manager.add_strategy(RSIStrategy)

    print("Initial strategies:", manager.get_strategy_summary()["strategy_names"])

    # Add another strategy
    manager.add_strategy(CustomStrategy)
    print("After adding Custom Strategy:", manager.get_strategy_summary()["strategy_names"])

    # Remove a strategy
    removed = manager.remove_strategy("RSI Strategy")
    print(f"Removed RSI Strategy: {removed}")
    print("After removal:", manager.get_strategy_summary()["strategy_names"])

    # Clear all strategies
    manager.clear_strategies()
    print("After clearing all:", manager.get_strategy_summary()["strategy_names"])

if __name__ == "__main__":
    # Run synchronous parallel execution demo
    main()

    # Run strategy management demo
    demo_strategy_management()

    # Run asynchronous parallel execution demo
    print("\nRunning async demo...")
    asyncio.run(async_main())

    print("\n=== Demo completed! ===")