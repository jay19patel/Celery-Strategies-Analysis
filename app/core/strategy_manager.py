from typing import List, Dict, Any


class StrategyManager:
    def __init__(self):
        self._strategy_class_paths: List[str] = []
        self._symbols: List[str] = []

    def add_strategies(self, strategy_class_paths: List[str]) -> None:
        self._strategy_class_paths.extend(strategy_class_paths)

    def add_symbols(self, symbols: List[str]) -> None:
        self._symbols.extend(symbols)

    # DEPRECATED: create_task_signatures removed


    def create_task_signatures_with_numbering(self) -> List[Any]:
        """
        Creates numbered task signatures for better tracking in logs
        """
        from app.core.tasks import execute_strategy_task

        signatures = []
        task_number = 1
        total_tasks = len(self._symbols) * len(self._strategy_class_paths)
        
        for symbol in self._symbols:
            for strategy_path in self._strategy_class_paths:
                signatures.append(
                    execute_strategy_task.s(strategy_path, symbol, task_number, total_tasks)
                )
                task_number += 1
        
        return signatures

    def aggregate_results(self, flat_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Aggregates flat list of results into structured format
        Groups by symbol and includes all strategy results
        """
        aggregated: Dict[str, Dict[str, Any]] = {}
        
        # Filter out None results from failed tasks
        valid_results = [r for r in flat_results if r]

        # Group by symbol
        for item in valid_results:
            symbol = item.get("symbol")
            if not symbol:
                continue
            
            if symbol not in aggregated:
                aggregated[symbol] = {
                    "symbol": symbol, 
                    "strategies": []
                }
            
            aggregated[symbol]["strategies"].append(item)

        # Calculate summary statistics
        unique_symbols = set(r["symbol"] for r in valid_results if "symbol" in r)
        
        summary = {
            "total_symbols": len(unique_symbols),
            "total_strategies": len(self._strategy_class_paths),
            "total_results": len(valid_results),
            "expected_results": len(self._symbols) * len(self._strategy_class_paths),
            "failed_results": (len(self._symbols) * len(self._strategy_class_paths)) - len(valid_results)
        }

        return {
            "summary": summary,
            "results": list(aggregated.values()),
        }

    # DEPRECATED: run_all removed. Use celery tasks.