"""
Task Observer pattern implementation for monitoring strategy execution.
"""

from abc import ABC, abstractmethod
from typing import List, Dict
from models.observer_models import TaskEvent, TaskResult, BatchStatistics, MonitoringSummary


class TaskObserver(ABC):
    """Abstract base class for task observers."""

    @abstractmethod
    def notify(self, event: TaskEvent):
        """Handle task event notifications."""
        pass


class TaskMonitor(TaskObserver):
    """Monitor and store task creation and completion events."""

    def __init__(self):
        self.created_tasks: List[TaskEvent] = []
        self.completed_tasks: List[TaskEvent] = []
        self.results: List[TaskResult] = []
        self.batch_stats: Dict[str, BatchStatistics] = {}

    def notify(self, event: TaskEvent):
        """Process incoming task events."""
        if event.event_type == 'created':
            self.created_tasks.append(event)
            self._update_batch_stats(event.batch_id, 'created')
        elif event.event_type == 'completed':
            self.completed_tasks.append(event)
            self._update_batch_stats(event.batch_id, 'completed', event.result)
            if event.result:
                # Convert dict to TaskResult model
                task_result = TaskResult(**event.result)
                self.results.append(task_result)

    def _update_batch_stats(self, batch_id: str, event_type: str, result: Dict = None):
        """Update batch-level statistics."""
        if batch_id not in self.batch_stats:
            self.batch_stats[batch_id] = BatchStatistics(batch_id=batch_id)

        batch_stat = self.batch_stats[batch_id]

        if event_type == 'created':
            batch_stat.created_count += 1
        elif event_type == 'completed':
            batch_stat.completed_count += 1
            if result and result.get('success', False):
                batch_stat.success_count += 1
            else:
                batch_stat.failure_count += 1

    def get_summary(self) -> MonitoringSummary:
        """Get complete monitoring summary."""
        total_successful = sum(stat.success_count for stat in self.batch_stats.values())
        total_failed = sum(stat.failure_count for stat in self.batch_stats.values())

        return MonitoringSummary(
            total_created=len(self.created_tasks),
            total_completed=len(self.completed_tasks),
            total_successful=total_successful,
            total_failed=total_failed,
            batch_statistics=self.batch_stats,
            results=self.results
        )

    def get_batch_results(self, batch_id: str) -> List[TaskResult]:
        """Get results for a specific batch."""
        return [result for result in self.results if result.batch_id == batch_id]

    def print_final_summary(self, batch_results: Dict, total_execution_time: float):
        """Print comprehensive final results summary."""
        summary = self.get_summary()
        summary.total_execution_time = total_execution_time

        print(f"\n{'='*60}")
        print(f"🎯 MULTI-BATCH EXECUTION RESULTS")
        print(f"{'='*60}")
        print(f"Total execution time: {total_execution_time:.2f} seconds")
        print(f"Total tasks created: {summary.total_created}")
        print(f"Total tasks completed: {summary.total_completed}")
        print(f"Total successful: {summary.total_successful}")
        print(f"Total failed: {summary.total_failed}")
        print(f"Completed batches: {len(batch_results)}/2")

        # Batch-wise statistics using Pydantic models
        print(f"\n📊 BATCH STATISTICS:")
        for batch_id, stats in summary.batch_statistics.items():
            print(f"   {batch_id}: {stats.completed_count}/{stats.created_count} tasks completed")
            print(f"            Success: {stats.success_count}, Failed: {stats.failure_count}")

        # Individual batch results
        for batch_id, result_data in batch_results.items():
            print(f"\n📈 {batch_id.upper()} ({result_data['name']}):")
            print(f"   Symbols: {result_data['symbols']}")
            print(f"   Execution time: {result_data['execution_time']:.2f} seconds")
            print(f"   Best strategy: {result_data['best_strategy']['strategy_name']}")
            print(f"   Best confidence: {result_data['best_strategy']['average_confidence']:.1f}%")
            print(f"   Total results: {result_data['total_results']}")

            # Show top 3 symbol results for this batch using TaskResult models
            batch_task_results = self.get_batch_results(batch_id)
            if batch_task_results:
                print("   Top 3 results:")
                sorted_results = sorted(batch_task_results, key=lambda x: x.confidence, reverse=True)
                for i, result in enumerate(sorted_results[:3]):
                    print(f"     {i+1}. {result.symbol} - {result.strategy_name}: {result.confidence:.1f}%")

        # Batch comparison
        if len(batch_results) > 1:
            batch_timings = {batch_id: data['execution_time'] for batch_id, data in batch_results.items()}
            print(f"\n⚡ PERFORMANCE COMPARISON:")
            print(f"   Fastest batch: {min(batch_timings.items(), key=lambda x: x[1])[0]} ({min(batch_timings.values()):.2f}s)")
            print(f"   Slowest batch: {max(batch_timings.items(), key=lambda x: x[1])[0]} ({max(batch_timings.values()):.2f}s)")
            print(f"   Average batch time: {sum(batch_timings.values()) / len(batch_timings):.2f}s")

            # Compare best strategies across batches
            print(f"\n🏆 BEST STRATEGIES BY BATCH:")
            for batch_id, result_data in batch_results.items():
                best = result_data['best_strategy']
                print(f"   {batch_id}: {best['strategy_name']} ({best['average_confidence']:.1f}%)")


class Observable:
    """Observable subject that notifies registered observers of task events."""

    def __init__(self):
        self.observers: List[TaskObserver] = []

    def add_observer(self, observer: TaskObserver):
        """Register a new observer."""
        self.observers.append(observer)

    def remove_observer(self, observer: TaskObserver):
        """Unregister an observer."""
        if observer in self.observers:
            self.observers.remove(observer)

    def notify_observers(self, event: TaskEvent):
        """Notify all registered observers of an event."""
        for observer in self.observers:
            observer.notify(event)