from datetime import datetime, timezone
import importlib
import time
from typing import Any, Dict
import uuid

from app.models.strategy_models import SignalType, StrategyResult
from app.core.celery_app import celery_app
from app.core.settings import get_schedule_seconds, get_strategies, get_symbols, settings
from app.core.strategy_manager import StrategyManager
from app.database.sqlite_adapter import get_collection, save_batch_results
from app.database.redis_publisher import get_redis_client, publish_batch_complete, publish_message
from app.core.logger import get_celery_logger, get_signals_logger, get_performance_logger
from app.core.paper_broker import PaperBroker

logger = get_celery_logger()
signals_logger = get_signals_logger()
performance_logger = get_performance_logger()

# Lazily initialized to handle prefork correctly
_paper_broker = None

def get_paper_broker():
    global _paper_broker
    if _paper_broker is None:
        _paper_broker = PaperBroker()
    return _paper_broker


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


@celery_app.task(bind=True, name="execute_strategy_task")
def execute_strategy_task(self, strategy_class_path: str, symbol: str, task_number: int, total_tasks: int) -> Dict[str, Any]:
    """
    Execute a single strategy for a symbol
    """
    start_time = time.time()
    strategy_name = strategy_class_path.split('.')[-1]
    
    try:
        logger.info(
            "strategy_execution_started task=%s/%s symbol=%s strategy=%s",
            task_number, total_tasks, symbol, strategy_name,
        )
        
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
            "strategy_execution_completed task=%s/%s symbol=%s strategy=%s signal=%s confidence=%.2f duration_seconds=%.2f",
            task_number, total_tasks, symbol, strategy_name, result_dict.get("signal_type"),
            result_dict.get("confidence", 0), execution_time,
        )
        return result_dict
        
    except Exception as e:
        execution_time = time.time() - start_time
        logger.exception(
            "strategy_execution_failed task=%s/%s symbol=%s strategy=%s duration_seconds=%.2f error=%s",
            task_number, total_tasks, symbol, strategy_name, execution_time, e,
        )
        # Return a None or error dict so the chord continues and we can filter it later
        # Returning None is standard for "failed but handled"
        return None


@celery_app.task(bind=True, name="process_batch_results")
def process_batch_results(self, results: list, batch_metadata: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    STEP 3: Process all strategy results after completion
    """
    try:
        logger.info("batch_result_processing_started received_tasks=%s", len(results))
        
        # Count successful results
        valid_results = [r for r in results if r]
        failed_count = len(results) - len(valid_results)
        
        if failed_count > 0:
            logger.warning("batch_strategy_failures count=%s", failed_count)
        
        logger.info("batch_strategy_results valid=%s failed=%s", len(valid_results), failed_count)
        
        # Aggregate results
        manager = StrategyManager()
        
        # Extract expected counts from metadata if available
        expected_skills = batch_metadata.get("expected_strategies_count") if batch_metadata else None
        expected_symbols = batch_metadata.get("expected_symbols_count") if batch_metadata else None

        aggregated_result = manager.aggregate_results(
            valid_results,
            expected_symbols_count=expected_symbols,
            expected_strategies_count=expected_skills
        )

        # STEP 3.0: Check stop-loss / take-profit on every open position, every cycle -
        # this must run regardless of this cycle's signal, so a protective exit isn't
        # missed while the strategy is signaling HOLD (see the early-return below).
        broker = get_paper_broker()
        for symbol_res in aggregated_result.get("results", []):
            symbol = symbol_res.get("symbol")
            for strat_res in symbol_res.get("strategies", []):
                price = strat_res.get("price", 0.0)
                if price and price > 0:
                    broker.check_protective_exit(
                        strat_res.get("strategy_name"), symbol, price, datetime.now(timezone.utc)
                    )

        # Check for actionable signals
        has_signals = _has_actionable_signal(aggregated_result)
        
        if not has_signals:
            logger.info("batch_result_skipped reason=no_actionable_signals summary=%s", aggregated_result.get("summary", {}))
            return {
                "batch_id": None,
                "summary": aggregated_result.get("summary", {}),
                "skipped": True,
                "reason": "No actionable signals detected"
            }

        # STEP 3.1: Prepare Data & Publish to Redis
        logger.info("batch_publish_started")
        
        # Generate Batch ID upfront
        batch_id_str = uuid.uuid4().hex[:24]
        
        # Add IDs to results if needed (matching user request structure)
        for symbol_res in aggregated_result.get("results", []):
            for strategy_res in symbol_res.get("strategies", []):
                if "_id" not in strategy_res:
                    strategy_res["_id"] = uuid.uuid4().hex[:24]

        # Construct the requested payload structure
        publish_payload = {
            "type": "batch_complete",
            "data": {
                "batch_id": batch_id_str,
                "summary": aggregated_result.get("summary", {}),
                "total_results": len(aggregated_result.get("results", [])),
                "results": aggregated_result.get("results", [])
            }
        }

        pubsub_response = publish_batch_complete(publish_payload)
        logger.info(
            "batch_publish_completed channel=%s subscribers=%s status=%s",
            pubsub_response.get("channel"), pubsub_response.get("subscriber_count", 0), pubsub_response.get("status"),
        )
        signals_logger.info(
            "batch_complete channel=%s batch_id=%s total_results=%s subscribers=%s status=%s",
            pubsub_response.get("channel"), batch_id_str, len(aggregated_result.get("results", [])),
            pubsub_response.get("subscriber_count", 0), pubsub_response.get("status"),
        )

        # STEP 3.1.5: Pass Actionable Signals to PaperBroker
        for symbol_res in aggregated_result.get("results", []):
            symbol = symbol_res.get("symbol")
            for strat_res in symbol_res.get("strategies", []):
                signal = strat_res.get("signal_type")
                # StrategyResult parses string to Enum, but dictionary is string. Let's convert to Enum
                try:
                    sig_enum = SignalType(signal)
                    if sig_enum != SignalType.HOLD:
                        price = strat_res.get("price", 0.0)
                        timestamp = strat_res.get("timestamp")
                        if isinstance(timestamp, str):
                            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        elif not timestamp:
                            timestamp = datetime.now(timezone.utc)
                            
                        # 1. Publish signal to Redis Pub/Sub strategy channel
                        subscriber_count = 0
                        try:
                            signal_payload = {
                                "type": "SignalGenerated",
                                "data": {
                                    "strategy_name": strat_res.get("strategy_name"),
                                    "symbol": symbol,
                                    "signal_type": signal,
                                    "price": price,
                                    "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
                                    "execution_time": strat_res.get("execution_time", 0.0)
                                }
                            }
                            subscriber_count = publish_message(settings.pubsub_channel_strategy, signal_payload)
                            signals_logger.info(
                                "signal_published channel=%s strategy=%s symbol=%s signal=%s price=%s subscribers=%s",
                                settings.pubsub_channel_strategy, strat_res.get("strategy_name"), symbol,
                                signal, price, subscriber_count,
                            )
                        except Exception as redis_err:
                            logger.exception("signal_publish_failed symbol=%s error=%s", symbol, redis_err)
                            signals_logger.error(
                                "signal_publish_failed channel=%s strategy=%s symbol=%s error=%s",
                                settings.pubsub_channel_strategy, strat_res.get("strategy_name"), symbol, redis_err,
                            )

                        # 2. Process the signal via ExecutionManager (routes to PaperBroker or Live Delta Broker based on mode)
                        from app.broker.execution_manager import get_execution_manager
                        exec_mgr = get_execution_manager()
                        stop_loss = strat_res.get("stop_loss")
                        take_profit = strat_res.get("take_profit")
                        exec_res = exec_mgr.process_signal(
                            strat_res.get("strategy_name"), symbol, sig_enum, price, timestamp, stop_loss=stop_loss, take_profit=take_profit
                        )

                        # 3. Log signal with execution routing info
                        try:
                            get_collection("signals_log").insert_one({
                                "strategy_name": strat_res.get("strategy_name"),
                                "symbol": symbol,
                                "signal_type": signal,
                                "price": price,
                                "timestamp": timestamp,
                                "execution_time": strat_res.get("execution_time", 0.0),
                                "subscribers_received": subscriber_count,
                                "mode": exec_res.get("mode", exec_mgr.get_mode()),
                                "action": exec_res.get("action", "recorded")
                            })
                        except Exception as db_err:
                            logger.exception("signal_persistence_failed symbol=%s error=%s", symbol, db_err)
                except Exception as e:
                    logger.exception("signal_processing_failed symbol=%s error=%s", symbol, e)
        
        # Update result with metadata for storage
        aggregated_result["_id"] = batch_id_str  # Use the pre-generated ID
        aggregated_result["pubsub"] = pubsub_response.get("subscriber_count", 0)

        # STEP 3.2: Save to SQLite Database
        logger.info("batch_persistence_started backend=sqlite")
        
        # Save (this will use the _id we added to aggregated_result)
        batch_id = save_batch_results(aggregated_result)
        logger.info("batch_persistence_completed batch_id=%s", batch_id)
        
        # Final summary
        logger.info(
            "batch_processing_completed batch_id=%s results=%s symbols=%s strategies=%s",
            batch_id, len(valid_results), aggregated_result.get("summary", {}).get("total_symbols"),
            aggregated_result.get("summary", {}).get("total_strategies"),
        )

        # Performance & Statistics log: per-strategy execution timings for this batch
        exec_times = [
            strat_res.get("execution_time", 0.0)
            for symbol_res in aggregated_result.get("results", [])
            for strat_res in symbol_res.get("strategies", [])
        ]
        total_exec_time = sum(exec_times)
        avg_exec_time = (total_exec_time / len(exec_times)) if exec_times else 0.0
        performance_logger.info(
            "batch_performance batch_id=%s total_tasks=%s successful=%s failed=%s symbols=%s strategies=%s "
            "total_duration_seconds=%.2f average_duration_seconds=%.2f subscribers=%s",
            batch_id, len(results), len(valid_results), failed_count,
            aggregated_result.get("summary", {}).get("total_symbols"),
            aggregated_result.get("summary", {}).get("total_strategies"), total_exec_time, avg_exec_time,
            pubsub_response.get("subscriber_count", 0),
        )

        return {"batch_id": str(batch_id), "summary": aggregated_result.get("summary", {})}
        
    except Exception as e:
        logger.exception("batch_result_processing_failed error=%s", e)
        raise


@celery_app.task(bind=True, name="run_all_batch_task")
def trigger_batch_execution(self, force: bool = False) -> Dict[str, Any]:
    """
    STEP 1: Trigger batch execution using Celery Chord.
    When force=False, checks if dynamic get_schedule_seconds() interval has elapsed.
    """
    now_utc = datetime.now(timezone.utc)
    interval = get_schedule_seconds()

    schedule_lock = None
    if not force:
        try:
            schedule_lock = get_redis_client().lock("lock:batch_schedule", timeout=30)
            if not schedule_lock.acquire(blocking=False):
                return {"status": "skipped", "reason": "Batch scheduler lock is held"}

            status_doc = get_collection("system_status").find_one({"_id": "batch_schedule"})
            if status_doc and status_doc.get("last_triggered_at"):
                raw_last = status_doc["last_triggered_at"]
                if isinstance(raw_last, str):
                    last_dt = datetime.fromisoformat(raw_last.replace("Z", "+00:00"))
                else:
                    last_dt = raw_last
                elapsed = (now_utc - last_dt).total_seconds()
                if elapsed < interval:
                    logger.debug(
                        "Batch schedule interval not reached: %.1fs elapsed < %ds interval",
                        elapsed,
                        interval,
                    )
                    schedule_lock.release()
                    schedule_lock = None
                    return {
                        "status": "skipped",
                        "reason": f"Interval not reached ({elapsed:.1f}s / {interval}s)",
                        "elapsed_seconds": elapsed,
                        "interval_seconds": interval,
                    }
        except Exception as check_err:
            if schedule_lock is not None:
                try:
                    schedule_lock.release()
                except Exception:
                    logger.warning("Failed to release batch scheduler lock", exc_info=True)
                schedule_lock = None
            logger.warning("Unable to enforce the batch schedule gate: %s", check_err, exc_info=True)
            raise

    try:
        logger.info("batch_dispatch_started force=%s interval_seconds=%s", force, interval)

        try:
            get_collection("system_status").update_one(
                {"_id": "batch_schedule"},
                {"$set": {
                    "last_triggered_at": now_utc.isoformat(),
                    "interval_seconds": interval,
                }},
                upsert=True,
            )
        except Exception as e:
            logger.exception("batch_schedule_status_write_failed error=%s", e)
            raise
        finally:
            if schedule_lock is not None:
                try:
                    schedule_lock.release()
                except Exception:
                    logger.warning("Failed to release batch scheduler lock", exc_info=True)
                schedule_lock = None

        symbols = get_symbols()
        strategies = get_strategies()
        
        logger.info(
            "batch_configuration symbols=%s strategies=%s task_count=%s",
            symbols, [s.split(".")[-1] for s in strategies], len(symbols) * len(strategies),
        )
        
        # Pre-cache data for all symbols
        logger.info("batch_precache_started symbol_count=%s", len(symbols))
        from app.utility.data_provider import fetch_historical_data
        
        pre_cache_count = 0
        for symbol in symbols:
            try:
                # Fetching data here will cache it in Redis
                fetch_historical_data(symbol, period=30, interval="15m")
                pre_cache_count += 1
            except Exception as e:
                logger.warning("batch_precache_failed symbol=%s error=%s", symbol, e)
        
        logger.info("batch_precache_completed cached=%s requested=%s", pre_cache_count, len(symbols))

        manager = StrategyManager()
        manager.add_symbols(symbols)
        manager.add_strategies(strategies)

        # Create task signatures with numbering
        tasks_sigs = manager.create_task_signatures_with_numbering()

        if not tasks_sigs:
            logger.warning("batch_dispatch_skipped reason=empty_configuration")
            return {"status": "skipped", "reason": "empty_batch"}

        logger.info("batch_tasks_generated count=%s", len(tasks_sigs))

        # Use Celery Chord: group(tasks) | callback
        from celery import chord
        
        # Pass expectation metadata to the callback because workers don't share state
        batch_metadata = {
            "triggered_at": "now",
            "expected_symbols_count": len(symbols),
            "expected_strategies_count": len(strategies)
        }
        
        callback = process_batch_results.s(batch_metadata=batch_metadata)
        chord(tasks_sigs)(callback)
        
        return {
            "status": "triggered", 
            "tasks_count": len(tasks_sigs),
            "expected_symbols": len(symbols),
            "expected_strategies": len(strategies),
            "pre_cached_count": pre_cache_count
        }
        
    except Exception as e:
        logger.exception("batch_dispatch_failed error=%s", e)
        raise
    finally:
        if schedule_lock is not None:
            try:
                schedule_lock.release()
            except Exception:
                logger.warning("Failed to release batch scheduler lock", exc_info=True)
