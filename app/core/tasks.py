import importlib
from typing import Any, Dict
from datetime import datetime, timezone
from bson import ObjectId
from app.models.strategy_models import SignalType, StrategyResult
from app.core.celery_app import celery_app
from app.core.settings import get_symbols, get_strategies, settings
from app.core.strategy_manager import StrategyManager
from app.database.mongodb import save_batch_results, get_collection
from app.database.redis_publisher import publish_batch_complete, publish_message
from app.core.logger import get_celery_logger
from app.core.paper_broker import PaperBroker
import time

logger = get_celery_logger()

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
        # Return a None or error dict so the chord continues and we can filter it later
        # Returning None is standard for "failed but handled"
        return None


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

        # STEP 3.1: Prepare Data & Publish to Redis
        logger.info("-" * 80)
        logger.info("📡 STEP 3.1: Publishing to Redis Pub/Sub")
        
        # Generate Batch ID upfront
        batch_oid = ObjectId()
        batch_id_str = str(batch_oid)
        
        # Add IDs to results if needed (matching user request structure)
        for symbol_res in aggregated_result.get("results", []):
            for strategy_res in symbol_res.get("strategies", []):
                if "_id" not in strategy_res:
                    strategy_res["_id"] = str(ObjectId())

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
        logger.info(f"✅ STEP 3.1 COMPLETED: Published to channel '{pubsub_response.get('channel')}'")

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
                            
                        # 1. Log signal to MongoDB signals_log collection
                        try:
                            get_collection("signals_log").insert_one({
                                "strategy_name": strat_res.get("strategy_name"),
                                "symbol": symbol,
                                "signal_type": signal,
                                "price": price,
                                "timestamp": timestamp,
                                "execution_time": strat_res.get("execution_time", 0.0)
                            })
                        except Exception as mongo_err:
                            logger.error(f"Failed to log signal to MongoDB: {mongo_err}", exc_info=True)

                        # 2. Publish signal to Redis Pub/Sub strategy channel
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
                            publish_message(settings.pubsub_channel_strategy, signal_payload)
                        except Exception as redis_err:
                            logger.error(f"Failed to publish signal to Redis: {redis_err}", exc_info=True)
                            
                        # 3. Process the signal via PaperBroker
                        broker.process_signal(strat_res.get("strategy_name"), symbol, sig_enum, price, timestamp)
                except Exception as e:
                    logger.error(f"Failed to process signal with broker: {e}", exc_info=True)
        
        # Update result with metadata for storage
        aggregated_result["_id"] = batch_oid  # Use the pre-generated ID
        aggregated_result["pubsub"] = pubsub_response.get("subscriber_count", 0)

        # STEP 3.2: Save to MongoDB
        logger.info("-" * 80)
        logger.info("📡 STEP 3.2: Saving to MongoDB")
        
        # Save (this will use the _id we added to aggregated_result)
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

        try:
            get_collection("system_status").update_one(
                {"_id": "batch_schedule"},
                {"$set": {
                    "last_triggered_at": datetime.now(timezone.utc),
                    "interval_seconds": settings.schedule_seconds,
                }},
                upsert=True,
            )
        except Exception as e:
            logger.error(f"⚠️  Failed to record batch schedule status: {e}")

        symbols = get_symbols()
        strategies = get_strategies()
        
        logger.info(f"📋 Configuration:")
        logger.info(f"   Symbols: {symbols}")
        logger.info(f"   Strategies: {[s.split('.')[-1] for s in strategies]}")
        logger.info(f"   Total combinations: {len(symbols)} symbols × {len(strategies)} strategies = {len(symbols) * len(strategies)} tasks")
        
        # Pre-cache data for all symbols
        logger.info("-" * 80)
        logger.info("💾 STEP 1.1: PRE-CACHING DATA")
        from app.utility.data_provider import fetch_historical_data
        
        pre_cache_count = 0
        for symbol in symbols:
            try:
                # Fetching data here will cache it in Redis
                fetch_historical_data(symbol, period=30, interval="15m")
                pre_cache_count += 1
            except Exception as e:
                logger.error(f"⚠️  Failed to pre-cache data for {symbol}: {str(e)}")
        
        logger.info(f"✅ STEP 1.1 COMPLETED: Pre-cached data for {pre_cache_count}/{len(symbols)} symbols")

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
        logger.info(f"✅ STEP 1.2 COMPLETED: Generated {len(tasks_sigs)} tasks")
        logger.info("=" * 80)
        logger.info("")
        logger.info("=" * 80)
        logger.info(f"🔄 STEP 2: EXECUTING {len(tasks_sigs)} TASKS")
        logger.info("=" * 80)

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
        logger.error("=" * 80)
        logger.error(f"❌ STEP 1 FAILED: Error triggering batch task: {str(e)}")
        logger.error("=" * 80)
        logger.error("Error details:", exc_info=True)
        raise