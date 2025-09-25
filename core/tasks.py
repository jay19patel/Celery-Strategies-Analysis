import importlib
from typing import Any, Dict

from models.strategy_models import StrategyResult
from core.celery_app import celery_app


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


