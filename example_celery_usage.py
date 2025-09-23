"""
Multi-batch strategy execution with Observer pattern for task monitoring.
"""

from strategies.ema_strategy import EMAStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.custom_strategy import CustomStrategy
from core.task_observer import TaskMonitor, Observable
from models.observer_models import TaskEvent
import time
import concurrent.futures
import threading
import uuid

def execute_strategy_for_symbol(strategy_class, symbol, batch_id, observable):
    """Execute a single strategy for a single symbol with observer notifications."""
    task_id = str(uuid.uuid4())
    strategy_name = getattr(strategy_class, '__name__', 'Unknown Strategy')

    # Notify task creation
    observable.notify_observers(TaskEvent(
        task_id=task_id,
        symbol=symbol,
        strategy_name=strategy_name,
        batch_id=batch_id,
        event_type='created',
        timestamp=time.time()
    ))

    try:
        strategy = strategy_class()
        result = strategy.execute()
        strategy_name = getattr(strategy, 'name', strategy_class.__name__)

        # Convert StrategyResult object to dict format
        if hasattr(result, 'confidence'):
            confidence = result.confidence
            signal = str(result.signal_type) if hasattr(result, 'signal_type') else 'HOLD'
        else:
            confidence = result.get('confidence', 0)
            signal = result.get('signal', 'HOLD')

        task_result = {
            'task_id': task_id,
            'symbol': symbol,
            'strategy_name': strategy_name,
            'confidence': confidence,
            'signal': signal,
            'success': True,
            'batch_id': batch_id
        }

        # Notify task completion
        observable.notify_observers(TaskEvent(
            task_id=task_id,
            symbol=symbol,
            strategy_name=strategy_name,
            batch_id=batch_id,
            event_type='completed',
            timestamp=time.time(),
            result=task_result
        ))

        return task_result

    except Exception as e:
        task_result = {
            'task_id': task_id,
            'symbol': symbol,
            'strategy_name': strategy_name,
            'confidence': 0,
            'signal': 'ERROR',
            'success': False,
            'error': str(e),
            'batch_id': batch_id
        }

        # Notify task completion with error
        observable.notify_observers(TaskEvent(
            task_id=task_id,
            symbol=symbol,
            strategy_name=strategy_name,
            batch_id=batch_id,
            event_type='completed',
            timestamp=time.time(),
            result=task_result
        ))

        return task_result

def execute_batch_parallel(symbols, strategies, batch_id, observable):
    """Execute strategies for symbols in parallel using ThreadPoolExecutor."""
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
            executor.submit(execute_strategy_for_symbol, strategy_class, symbol, batch_id, observable): (strategy_class, symbol)
            for strategy_class, symbol in tasks
        }

        for future in concurrent.futures.as_completed(future_to_task):
            result = future.result()
            results.append(result)

    execution_time = time.time() - start_time
    return results, execution_time

def example_multi_batch_execution_with_observer():
    """Execute multiple batches with Observer pattern monitoring."""
    # Create observer and observable
    observable = Observable()
    monitor = TaskMonitor()
    observable.add_observer(monitor)

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

    # Execute batches in parallel using threading
    def execute_single_batch(batch_id, batch_info):
        symbols = batch_info['symbols']
        batch_name = batch_info['name']

        # Execute batch
        results, execution_time = execute_batch_parallel(symbols, strategies, batch_id, observable)

        # Process results
        processed_results_data = []
        for result in results:
            if result['success']:
                processed_results_data.append(result)

        # Find best strategy for this batch
        strategy_performance = {}
        for result in processed_results_data:
            strategy = result['strategy_name']
            if strategy not in strategy_performance:
                strategy_performance[strategy] = []
            strategy_performance[strategy].append(result['confidence'])

        # Handle case where no strategies succeeded
        if not strategy_performance:
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
    for thread in batch_threads:
        thread.join()

    batch_results = thread_results
    total_execution_time = time.time() - total_start_time

    return batch_results, total_execution_time, monitor

if __name__ == "__main__":
    print("Stock Analysis Strategy Manager - Observer Pattern Multi-Batch Processing\n")

    # Execute with observer pattern
    batch_results, total_time, monitor = example_multi_batch_execution_with_observer()

    # Display final results through observer
    monitor.print_final_summary(batch_results, total_time)