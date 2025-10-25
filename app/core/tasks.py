import importlib
from typing import Any, Dict

from app.models.strategy_models import StrategyResult
from app.core.celery_app import celery_app
from app.core.settings import get_symbols, get_strategies
from app.core.strategy_manager import StrategyManager
from app.database.mongodb import save_strategy_result, save_batch_results
from app.database.redis_publisher import publish_strategy_result, publish_batch_complete


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

    # Ensure JSON-serializable payload
    if isinstance(result_dict.get("timestamp"), object):
        try:
            result_dict["timestamp"] = result.timestamp.isoformat()
        except Exception:
            pass

    # Save to MongoDB
    doc_id = save_strategy_result(result_dict)
    result_dict["_id"] = str(doc_id)

    # Publish to Redis pub/sub for real-time subscribers
    publish_strategy_result(result_dict)

    return result_dict


@celery_app.task(bind=True, name="run_all_batch_task")
def run_all_batch_task(self) -> Dict[str, Any]:
    symbols = get_symbols()
    strategies = get_strategies()

    manager = StrategyManager()
    manager.add_symbols(symbols)
    manager.add_strategies(strategies)

    result = manager.run_all()

    # Save batch results to MongoDB
    batch_id = save_batch_results(result)

    # Publish batch completion to Redis pub/sub with complete data
    # This includes all strategy results organized by symbol
    publish_batch_complete({
        "batch_id": str(batch_id),
        "summary": result.get("summary", {}),
        "total_results": len(result.get("results", [])),
        "results": result.get("results", [])  # All strategy results for all symbols
    })

    return {"batch_id": str(batch_id), "summary": result.get("summary", {})}


