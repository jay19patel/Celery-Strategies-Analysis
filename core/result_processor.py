"""
Strategy Result Processor for analyzing and ranking strategy execution results.
Processes batch results to find best strategies and order them by confidence.
"""

from typing import List, Dict, Any, Optional
import logging

from models.analysis_models import StrategyAnalysis, ProcessedResults

class StrategyResultProcessor:
    """
    Processes Celery batch results to analyze strategy performance.

    Features:
    - Finds best strategy by highest average confidence
    - Orders all strategies from high to low confidence
    - Calculates success rates and execution statistics
    - Provides detailed analysis for each strategy
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def process_batch_results(self, batch_result: Dict[str, Any]) -> ProcessedResults:
        """
        Process batch execution results and analyze strategy performance.

        Args:
            batch_result: Batch result from Celery execution

        Returns:
            ProcessedResults: Complete analysis of all strategies
        """
        if not batch_result or 'results' not in batch_result:
            raise ValueError("Invalid batch result - missing results data")

        results = batch_result['results']
        if not results:
            raise ValueError("No strategy results found in batch")

        # Group results by strategy
        strategy_groups = self._group_results_by_strategy(results)

        # Analyze each strategy
        strategy_analyses = []
        for strategy_name, strategy_results in strategy_groups.items():
            analysis = self._analyze_strategy(strategy_name, strategy_results)
            strategy_analyses.append(analysis)

        # Sort strategies by average confidence (high to low)
        strategies_by_confidence = sorted(
            strategy_analyses,
            key=lambda x: x.average_confidence,
            reverse=True
        )

        # Find best strategy (highest average confidence)
        best_strategy = strategies_by_confidence[0] if strategies_by_confidence else None

        # Calculate overall statistics
        total_executions = sum(len(results) for results in strategy_groups.values())
        successful_executions = sum(len([r for r in results if r is not None])
                                  for results in strategy_groups.values())
        overall_success_rate = (successful_executions / total_executions * 100) if total_executions > 0 else 0

        return ProcessedResults(
            best_strategy=best_strategy,
            strategies_by_confidence=strategies_by_confidence,
            total_symbols=len(set(r['symbol'] for r in results if r)),
            total_strategies=len(strategy_groups),
            overall_success_rate=overall_success_rate,
            batch_summary=batch_result
        )

    def _group_results_by_strategy(self, results: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Group execution results by strategy name."""
        strategy_groups = {}

        for result in results:
            if result is None:
                continue

            strategy_name = result.get('strategy_name', 'Unknown')
            if strategy_name not in strategy_groups:
                strategy_groups[strategy_name] = []
            strategy_groups[strategy_name].append(result)

        return strategy_groups

    def _analyze_strategy(self, strategy_name: str, strategy_results: List[Dict[str, Any]]) -> StrategyAnalysis:
        """Analyze performance of a single strategy across all symbols."""
        if not strategy_results:
            return StrategyAnalysis(
                strategy_name=strategy_name,
                average_confidence=0.0,
                total_executions=0,
                successful_executions=0,
                success_rate=0.0,
                best_symbol="None",
                best_confidence=0.0,
                execution_details=[]
            )

        # Filter successful executions
        successful_results = [r for r in strategy_results if r is not None and 'confidence' in r]

        # Calculate statistics
        total_executions = len(strategy_results)
        successful_executions = len(successful_results)
        success_rate = (successful_executions / total_executions * 100) if total_executions > 0 else 0

        if successful_results:
            confidences = [float(r['confidence']) for r in successful_results]
            average_confidence = sum(confidences) / len(confidences)

            # Find best performing symbol for this strategy
            best_result = max(successful_results, key=lambda x: float(x['confidence']))
            best_symbol = best_result['symbol']
            best_confidence = float(best_result['confidence'])
        else:
            average_confidence = 0.0
            best_symbol = "None"
            best_confidence = 0.0

        return StrategyAnalysis(
            strategy_name=strategy_name,
            average_confidence=round(average_confidence, 3),
            total_executions=total_executions,
            successful_executions=successful_executions,
            success_rate=round(success_rate, 2),
            best_symbol=best_symbol,
            best_confidence=round(best_confidence, 3),
            execution_details=successful_results
        )

    def print_analysis(self, processed_results: ProcessedResults) -> None:
        """Print detailed analysis of strategy results."""
        print("\n" + "="*60)
        print("STRATEGY PERFORMANCE ANALYSIS")
        print("="*60)

        # Batch summary
        batch = processed_results.batch_summary
        print(f"Batch ID: {batch.get('batch_id', 'N/A')}")
        print(f"Total Symbols: {processed_results.total_symbols}")
        print(f"Total Strategies: {processed_results.total_strategies}")
        print(f"Execution Time: {batch.get('execution_time', 0):.2f} seconds")
        print(f"Overall Success Rate: {processed_results.overall_success_rate:.1f}%")

        # Best strategy
        print(f"\n🏆 BEST STRATEGY (Highest Confidence)")
        print("-" * 40)
        best = processed_results.best_strategy
        if best:
            print(f"Strategy: {best.strategy_name}")
            print(f"Average Confidence: {best.average_confidence:.1f}%")
            print(f"Success Rate: {best.success_rate:.1f}%")
            print(f"Best Symbol: {best.best_symbol} (Confidence: {best.best_confidence:.1f}%)")

        # All strategies ranked by confidence
        print(f"\n📊 ALL STRATEGIES (Ranked by Confidence)")
        print("-" * 60)
        print(f"{'Rank':<5} {'Strategy':<20} {'Avg Confidence':<15} {'Success Rate':<12} {'Best Symbol':<15}")
        print("-" * 60)

        for rank, strategy in enumerate(processed_results.strategies_by_confidence, 1):
            print(f"{rank:<5} {strategy.strategy_name:<20} {strategy.average_confidence:.1f}%{'':<10} {strategy.success_rate:.1f}%{'':<7} {strategy.best_symbol:<15}")

        # Detailed breakdown
        print(f"\n📈 DETAILED BREAKDOWN")
        print("-" * 60)
        for strategy in processed_results.strategies_by_confidence:
            print(f"\n{strategy.strategy_name}:")
            print(f"  • Average Confidence: {strategy.average_confidence:.1f}%")
            print(f"  • Executions: {strategy.successful_executions}/{strategy.total_executions}")
            print(f"  • Success Rate: {strategy.success_rate:.1f}%")
            print(f"  • Best Performance: {strategy.best_symbol} ({strategy.best_confidence:.1f}%)")

            # Show top 3 symbols for this strategy
            if strategy.execution_details:
                top_results = sorted(strategy.execution_details,
                                   key=lambda x: float(x['confidence']), reverse=True)[:3]
                print(f"  • Top Symbols: ", end="")
                for i, result in enumerate(top_results):
                    print(f"{result['symbol']}({result['confidence']:.1f}%)", end="")
                    if i < len(top_results) - 1:
                        print(", ", end="")
                print()

        print("\n" + "="*60)