import importlib
from typing import Any, Dict
from app.models.strategy_models import StrategyResult
from app.core.celery_app import celery_app
from app.core.settings import get_symbols, get_strategies
from app.core.strategy_manager import StrategyManager
from app.database.mongodb import save_batch_results
from app.database.redis_publisher import publish_batch_complete
from app.core.logger import get_celery_logger

logger = get_celery_logger()


def _load_strategy_class(dotted_path: str):
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


@celery_app.task(bind=True, name="execute_strategy_task", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def execute_strategy_task(self, strategy_class_path: str, symbol: str) -> Dict[str, Any]:
    try:
        logger.info(f"Executing strategy task: {strategy_class_path} for symbol {symbol}")
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
        
        logger.info(f"Successfully executed strategy task for {symbol}")
        return result_dict
    except Exception as e:
        logger.error(f"Error executing strategy task for {symbol}: {str(e)}", exc_info=True)
        raise


@celery_app.task(bind=True, name="run_all_batch_task")
def run_all_batch_task(self) -> Dict[str, Any]:
    try:
        logger.info("Starting batch task execution")
        symbols = get_symbols()
        strategies = get_strategies()

        logger.info(f"Batch task: {len(symbols)} symbols, {len(strategies)} strategies")
        
        manager = StrategyManager()
        manager.add_symbols(symbols)
        manager.add_strategies(strategies)

        result = manager.run_all()

        # Publish batch completion to Redis pub/sub FIRST and capture response
        # No batch_id yet (we haven't saved), so publish summary and results only
        logger.info("Publishing batch completion (pre-save)")
        pubsub_response = publish_batch_complete({
            "summary": result.get("summary", {}),
            "total_results": len(result.get("results", [])),
            "results": result.get("results", [])
        })

        # Store pub/sub response in the result payload so it persists to Mongo
        result["pubsub"] = pubsub_response

        # Save batch results to MongoDB
        logger.info("Saving batch results to MongoDB")
        batch_id = save_batch_results(result)

        logger.info(f"Batch task completed successfully: {batch_id}")
        return {"batch_id": str(batch_id), "summary": result.get("summary", {})}
    except Exception as e:
        logger.error(f"Error executing batch task: {str(e)}", exc_info=True)
        raise


