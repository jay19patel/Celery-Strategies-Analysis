from typing import List, Dict, Any
from celery.result import GroupResult
from celery import group

from core.tasks import execute_strategy_task


class StrategyManager:
    def __init__(self):
        self._strategy_class_paths: List[str] = []
        self._symbols: List[str] = []

    def add_strategies(self, strategy_class_paths: List[str]) -> None:
        self._strategy_class_paths.extend(strategy_class_paths)

    def add_symbols(self, symbols: List[str]) -> None:
        self._symbols.extend(symbols)

    def run_all(self) -> Dict[str, Any]:
        tasks = []
        for symbol in self._symbols:
            for strategy_path in self._strategy_class_paths:
                tasks.append(execute_strategy_task.s(strategy_path, symbol))

        job: GroupResult = group(tasks).apply_async()
        results = job.get(disable_sync_subtasks=False)

        # Aggregate by symbol
        aggregated: Dict[str, Dict[str, Any]] = {}
        for item in results:
            symbol = item["symbol"]
            aggregated.setdefault(symbol, {"symbol": symbol, "strategies": []})
            aggregated[symbol]["strategies"].append(item)

        summary = {
            "total_symbols": len(self._symbols),
            "total_strategies": len(self._strategy_class_paths),
            "total_tasks": len(tasks),
        }

        return {
            "summary": summary,
            "results": list(aggregated.values()),
        }


