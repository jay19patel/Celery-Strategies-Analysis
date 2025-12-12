import importlib
from typing import Any, Dict
from app.models.strategy_models import SignalType, StrategyResult
from app.core.celery_app import celery_app
from app.core.settings import get_symbols, get_strategies
from app.core.strategy_manager import StrategyManager
from app.database.mongodb import save_batch_results
from app.database.redis_publisher import publish_batch_complete
from app.core.logger import get_celery_logger
import time

logger = get_celery_logger()


def _load_strategy_class(dotted_path: str):
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _has_actionable_signal(batch_result: Dict[str, Any]) -> bool:
    """
    Returns True if any strategy output contains a signal other than HOLD.
    """
    for symbol_block in batch_result.get("results", []):
        for strategy_entry in symbol_block.get("strategies", []):
            if strategy_entry.get("signal_type") != SignalType.HOLD.value:
                return True
    return False


@celery_app.task(bind=True, name="execute_strategy_task", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def execute_strategy_task(self, strategy_class_path: str, symbol: str, task_number: int, total_tasks: int) -> Dict[str, Any]:
    """
    Execute a single strategy for a symbol
    """
    start_time = time.time()
    strategy_name = strategy_class_path.split('.')[-1]
    
    try:
        logger.info(f"📊 STEP 2.{task_number}/{total_tasks} | Processing: {symbol} | Strategy: {strategy_name}")
        
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
        
        execution_time = time.time() - start_time
        logger.info(
            f"✅ STEP 2.{task_number}/{total_tasks} COMPLETED | {symbol} | {strategy_name} | "
            f"Signal: {result_dict.get('signal_type')} | Confidence: {result_dict.get('confidence', 0):.2f} | "
            f"Time: {execution_time:.2f}s"
        )
        return result_dict
        
    except Exception as e:
        execution_time = time.time() - start_time
        logger.error(
            f"❌ STEP 2.{task_number}/{total_tasks} FAILED | {symbol} | {strategy_name} | "
            f"Error: {str(e)} | Time: {execution_time:.2f}s", 
            exc_info=True
        )
        raise


@celery_app.task(bind=True, name="process_batch_results")
def process_batch_results(self, results: list, batch_metadata: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    STEP 3: Process all strategy results after completion
    """
    try:
        logger.info("=" * 80)
        logger.info("🔄 STEP 3: PROCESSING BATCH RESULTS")
        logger.info("=" * 80)
        
        # Count successful results
        valid_results = [r for r in results if r]
        failed_count = len(results) - len(valid_results)
        
        if failed_count > 0:
            logger.warning(f"⚠️  {failed_count} tasks failed during execution")
        
        logger.info(f"✅ Successfully completed: {len(valid_results)} tasks")
        
        # Aggregate results
        manager = StrategyManager()
        aggregated_result = manager.aggregate_results(valid_results)
        
        # Check for actionable signals
        has_signals = _has_actionable_signal(aggregated_result)
        
        if not has_signals:
            logger.info("=" * 80)
            logger.info("ℹ️  STEP 3 RESULT: All signals are HOLD - Skipping publish/save")
            logger.info(aggregated_result.get("summary", {}))
            logger.info("=" * 80)
            return {
                "batch_id": None,
                "summary": aggregated_result.get("summary", {}),
                "skipped": True,
                "reason": "No actionable signals detected"
            }

        # STEP 3.1: Publish to Redis
        logger.info("-" * 80)
        logger.info("📡 STEP 3.1: Publishing to Redis Pub/Sub")
        pubsub_response = publish_batch_complete({
            "summary": aggregated_result.get("summary", {}),
            "total_results": len(aggregated_result.get("results", [])),
            "results": aggregated_result.get("results", [])
        })
        logger.info(f"✅ STEP 3.1 COMPLETED: Published to channel '{pubsub_response.get('channel')}'")
        
        aggregated_result["pubsub"] = pubsub_response.get("subscriber_count", 0)

        # STEP 3.2: Save to MongoDB
        logger.info("-" * 80)
        logger.info("💾 STEP 3.2: Saving to MongoDB")
        batch_id = save_batch_results(aggregated_result)
        logger.info(f"✅ STEP 3.2 COMPLETED: Batch saved with ID: {batch_id}")
        
        # Final summary
        logger.info("=" * 80)
        logger.info("🎉 STEP 3: BATCH PROCESSING COMPLETED SUCCESSFULLY")
        logger.info(f"   Batch ID: {batch_id}")
        logger.info(f"   Total Results: {len(valid_results)}")
        logger.info(f"   Symbols Processed: {aggregated_result.get('summary', {}).get('total_symbols')}")
        logger.info(f"   Strategies Used: {aggregated_result.get('summary', {}).get('total_strategies')}")
        logger.info("=" * 80)
        
        return {"batch_id": str(batch_id), "summary": aggregated_result.get("summary", {})}
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"❌ STEP 3 FAILED: Error processing batch results: {str(e)}")
        logger.error("=" * 80)
        logger.error("Error details:", exc_info=True)
        raise


@celery_app.task(bind=True, name="run_all_batch_task")
def trigger_batch_execution(self) -> Dict[str, Any]:
    """
    STEP 1: Trigger batch execution using Celery Chord
    """
    try:
        logger.info("=" * 80)
        logger.info("🚀 STEP 1: INITIATING BATCH EXECUTION")
        logger.info("=" * 80)
        
        symbols = get_symbols()
        strategies = get_strategies()
        
        logger.info(f"📋 Configuration:")
        logger.info(f"   Symbols: {symbols}")
        logger.info(f"   Strategies: {[s.split('.')[-1] for s in strategies]}")
        logger.info(f"   Total combinations: {len(symbols)} symbols × {len(strategies)} strategies = {len(symbols) * len(strategies)} tasks")
        
        manager = StrategyManager()
        manager.add_symbols(symbols)
        manager.add_strategies(strategies)

        # Create task signatures with numbering
        tasks_sigs = manager.create_task_signatures_with_numbering()

        if not tasks_sigs:
            logger.warning("⚠️  No tasks to run (empty configuration)")
            logger.info("=" * 80)
            return {"status": "skipped", "reason": "empty_batch"}

        logger.info("-" * 80)
        logger.info(f"✅ STEP 1 COMPLETED: Generated {len(tasks_sigs)} tasks")
        logger.info("=" * 80)
        logger.info("")
        logger.info("=" * 80)
        logger.info(f"🔄 STEP 2: EXECUTING {len(tasks_sigs)} TASKS")
        logger.info("=" * 80)

        # Use Celery Chord: group(tasks) | callback
        from celery import chord
        
        callback = process_batch_results.s(batch_metadata={"triggered_at": "now"})
        chord(tasks_sigs)(callback)
        
        return {"status": "triggered", "tasks_count": len(tasks_sigs)}
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"❌ STEP 1 FAILED: Error triggering batch task: {str(e)}")
        logger.error("=" * 80)
        logger.error("Error details:", exc_info=True)
        raise