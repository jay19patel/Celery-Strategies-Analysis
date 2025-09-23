"""
Example usage of integrated Celery-based strategy execution.
Demonstrates how to handle overlapping batches of 10 symbols × 10 strategies.
"""

from strategies.ema_strategy import EMAStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.custom_strategy import CustomStrategy
import time
import logging
import concurrent.futures
import threading

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def execute_strategy_for_symbol(strategy_class, symbol):
    """Execute a single strategy for a single symbol."""
    try:
        strategy = strategy_class()
        # The strategy execute method doesn't take symbol parameter based on the code
        result = strategy.execute()
        strategy_name = getattr(strategy, 'name', strategy_class.__name__)

        # Convert StrategyResult object to dict format
        if hasattr(result, 'confidence'):
            confidence = result.confidence
            signal = str(result.signal_type) if hasattr(result, 'signal_type') else 'HOLD'
        else:
            confidence = result.get('confidence', 0)
            signal = result.get('signal', 'HOLD')

        print(f"   ✅ {symbol} - {strategy_name}: {confidence:.1f}%")

        return {
            'symbol': symbol,
            'strategy_name': strategy_name,
            'confidence': confidence,
            'signal': signal,
            'success': True
        }
    except Exception as e:
        strategy_name = getattr(strategy_class, '__name__', 'Unknown Strategy')
        print(f"   ❌ {symbol} - {strategy_name}: ERROR - {str(e)}")

        return {
            'symbol': symbol,
            'strategy_name': strategy_name,
            'confidence': 0,
            'signal': 'ERROR',
            'success': False,
            'error': str(e)
        }

def execute_batch_parallel(symbols, strategies, batch_name="batch"):
    """Execute strategies for symbols in parallel using ThreadPoolExecutor."""
    print(f"🚀 Executing {batch_name}: {len(symbols)} symbols × {len(strategies)} strategies = {len(symbols) * len(strategies)} tasks")

    start_time = time.time()
    results = []

    # Create all tasks
    tasks = []
    for symbol in symbols:
        for strategy_class in strategies:
            tasks.append((strategy_class, symbol))

    # Execute in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        future_to_task = {
            executor.submit(execute_strategy_for_symbol, strategy_class, symbol): (strategy_class, symbol)
            for strategy_class, symbol in tasks
        }

        completed = 0
        print(f"   ⏳ Processing {len(tasks)} tasks...")
        for future in concurrent.futures.as_completed(future_to_task):
            result = future.result()
            results.append(result)
            completed += 1

            if completed % 3 == 0 or completed == len(tasks):
                progress = (completed / len(tasks)) * 100
                elapsed = time.time() - start_time
                print(f"   📈 Progress: {completed}/{len(tasks)} ({progress:.1f}%) - {elapsed:.1f}s elapsed")

    execution_time = time.time() - start_time
    print(f"✅ {batch_name} completed in {execution_time:.2f} seconds")

    return results, execution_time

def example_multi_batch_execution_with_detailed_analysis():
    """Execute multiple batches with separate results and comprehensive timing analysis."""
    print("\n=== Multi-Batch Execution with Detailed Analysis ===")

    # Define strategies
    strategies = [EMAStrategy, RSIStrategy, CustomStrategy]

    # Define multiple batches
    batches = {
        'batch_1': {
            'symbols': ['AAPL', 'GOOGL', 'MSFT', 'TSLA', 'AMZN'],
            'name': 'Tech Giants Batch'
        },
        'batch_2': {
            'symbols': ['META', 'NFLX', 'NVDA', 'AMD', 'INTC'],
            'name': 'Tech & Entertainment Batch'
        }
    }

    # Track timing for entire process
    total_start_time = time.time()
    batch_results = {}
    batch_timings = {}

    print(f"Executing {len(batches)} batches with multiple symbols and strategies...")

    # Execute batches in parallel using threading
    def execute_single_batch(batch_id, batch_info):
        symbols = batch_info['symbols']
        batch_name = batch_info['name']

        print(f"\n📦 Starting {batch_id} ({batch_name})")
        print(f"   Symbols: {symbols}")

        # Execute batch
        results, execution_time = execute_batch_parallel(symbols, strategies, f"{batch_id} ({batch_name})")

        # Process results using StrategyResultProcessor format
        processed_results_data = []
        successful_count = 0
        failed_count = 0

        for result in results:
            if result['success']:
                processed_results_data.append({
                    'symbol': result['symbol'],
                    'strategy_name': result['strategy_name'],
                    'confidence': result['confidence'],
                    'signal': result['signal']
                })
                successful_count += 1
            else:
                failed_count += 1

        print(f"   📊 Results: {successful_count} successful, {failed_count} failed")

        # Find best strategy for this batch
        strategy_performance = {}
        for result in processed_results_data:
            strategy = result['strategy_name']
            if strategy not in strategy_performance:
                strategy_performance[strategy] = []
            strategy_performance[strategy].append(result['confidence'])

        # Handle case where no strategies succeeded
        if not strategy_performance:
            print(f"   ⚠️  Warning: No successful strategy executions for {batch_id}")
            best_strategy_name = "No Strategy"
            best_avg_confidence = 0.0
        else:
            best_strategy_name = max(strategy_performance.keys(),
                                   key=lambda k: sum(strategy_performance[k]) / len(strategy_performance[k]))
            best_avg_confidence = sum(strategy_performance[best_strategy_name]) / len(strategy_performance[best_strategy_name])

        return batch_id, {
            'results': processed_results_data,
            'execution_time': execution_time,
            'name': batch_name,
            'symbols': symbols,
            'best_strategy': {
                'strategy_name': best_strategy_name,
                'average_confidence': best_avg_confidence
            },
            'total_results': len(processed_results_data)
        }

    # Execute all batches in parallel using threads
    batch_threads = []
    thread_results = {}

    def thread_wrapper(batch_id, batch_info):
        result = execute_single_batch(batch_id, batch_info)
        thread_results[result[0]] = result[1]

    # Start all batch threads
    for batch_id, batch_info in batches.items():
        thread = threading.Thread(target=thread_wrapper, args=(batch_id, batch_info))
        thread.start()
        batch_threads.append(thread)

    # Wait for all threads to complete
    print(f"\n🚀 All {len(batches)} batches running in parallel...")
    for thread in batch_threads:
        thread.join()

    batch_results = thread_results
    total_execution_time = time.time() - total_start_time

    # Collect batch timings
    for batch_id, result_data in batch_results.items():
        batch_timings[batch_id] = result_data['execution_time']

    # Display comprehensive results
    print(f"\n{'='*60}")
    print(f"🎯 MULTI-BATCH EXECUTION RESULTS")
    print(f"{'='*60}")
    print(f"Total execution time: {total_execution_time:.2f} seconds")
    print(f"Completed batches: {len(batch_results)}/{len(batches)}")

    # Individual batch results
    for batch_id, result_data in batch_results.items():
        print(f"\n📊 {batch_id.upper()} ({result_data['name']}):")
        print(f"   Symbols: {result_data['symbols']}")
        print(f"   Execution time: {result_data['execution_time']:.2f} seconds")
        print(f"   Best strategy: {result_data['best_strategy']['strategy_name']}")
        print(f"   Best confidence: {result_data['best_strategy']['average_confidence']:.1f}%")
        print(f"   Total results: {result_data['total_results']}")

        # Show top 3 symbol results for this batch
        print("   Top 3 symbol results:")
        sorted_results = sorted(result_data['results'], key=lambda x: x['confidence'], reverse=True)
        for i, result in enumerate(sorted_results[:3]):
            print(f"     {i+1}. {result['symbol']} - {result['strategy_name']}: {result['confidence']:.1f}%")

    # Batch comparison
    if len(batch_results) > 1:
        print(f"\n📈 BATCH COMPARISON:")
        print(f"   Fastest batch: {min(batch_timings.items(), key=lambda x: x[1])[0]} ({min(batch_timings.values()):.2f}s)")
        print(f"   Slowest batch: {max(batch_timings.items(), key=lambda x: x[1])[0]} ({max(batch_timings.values()):.2f}s)")
        print(f"   Average batch time: {sum(batch_timings.values()) / len(batch_timings):.2f}s")

        # Compare best strategies across batches
        print(f"\n🏆 BEST STRATEGIES BY BATCH:")
        for batch_id, result_data in batch_results.items():
            best = result_data['best_strategy']
            print(f"   {batch_id}: {best['strategy_name']} ({best['average_confidence']:.1f}%)")

    return batch_results, total_execution_time

if __name__ == "__main__":
    print("Stock Analysis Strategy Manager - Multi-Batch Processing\n")

    print("🚀 Starting Multi-Batch Execution with Detailed Analysis...")
    batch_results, total_time = example_multi_batch_execution_with_detailed_analysis()

    print(f"\n✅ Multi-batch execution completed in {total_time:.2f} seconds!")
    print(f"📈 Processed {len(batch_results)} batches successfully")

    print("\n=== Optimized Multi-Batch Processing Complete ===")