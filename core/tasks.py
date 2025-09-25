import importlib
from typing import Any, Dict

from models.strategy_models import StrategyResult
from core.celery_app import celery_app
from core.settings import get_symbols, get_strategies
from core.strategy_manager import StrategyManager
import os
import json
from datetime import datetime, timezone


def _load_strategy_class(dotted_path: str):
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


@celery_app.task(bind=True, name="execute_strategy_task", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def execute_strategy_task(self, strategy_class_path: str, symbol: str) -> Dict[str, Any]:
    StrategyClass = _load_strategy_class(strategy_class_path)
    strategy = StrategyClass()
    result: StrategyResult = strategy.execute(symbol)
    result_dict = result.dict()
    # Ensure JSON-serializable payload for downstream aggregation and file output
    if isinstance(result_dict.get("timestamp"), object):
        try:
            result_dict["timestamp"] = result.timestamp.isoformat()
        except Exception:
            pass
    return result_dict


@celery_app.task(bind=True, name="run_all_batch_task")
def run_all_batch_task(self) -> Dict[str, Any]:
    symbols = get_symbols()
    strategies = get_strategies()

    manager = StrategyManager()
    manager.add_symbols(symbols)
    manager.add_strategies(strategies)

    result = manager.run_all()

    # Save to results directory
    os.makedirs("results", exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join("results", f"results_{timestamp}.json")
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)

    return {"saved": output_file, "summary": result.get("summary", {})}


