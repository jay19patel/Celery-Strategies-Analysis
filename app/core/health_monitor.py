"""Health and system monitoring system for the Celery trading pipeline.

Runs periodic health checks in a background daemon thread and exposes system
metrics (CPU %, RAM %, Disk %, SQLite DB size, Celery workers, Redis, and live broker)
via ``get_latest_health()`` for the OpenAlgo-style dashboard monitoring API.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import psutil
except ImportError:
    psutil = None

from app.database.sqlite_db import get_sqlite_db

logger = logging.getLogger(__name__)

# Configuration via env vars
_HEALTH_ENABLED = os.getenv("HEALTH_MONITOR_ENABLED", "true").lower() == "true"
_SAMPLE_INTERVAL = int(os.getenv("HEALTH_SAMPLE_INTERVAL", "5"))  # 5 seconds for fast monitoring
_STOP_CHECK_SEC = 0.5

# Background state
_collector_thread: Optional[threading.Thread] = None
_collector_running = False
_collector_lock = threading.Lock()
_cache_lock = threading.Lock()
_cached_metrics: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_system_resources() -> Dict[str, Any]:
    """Collect real-time CPU, RAM, Disk, and system uptime using psutil."""
    if psutil is None:
        return {
            "status": "warn",
            "message": "psutil library not available in runtime environment",
            "cpu": {"percent": 0.0, "cores": 1, "per_core": [0.0]},
            "memory": {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "percent": 0.0},
            "disk": {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "percent": 0.0},
            "process": {"pid": os.getpid(), "rss_mb": 0.0},
            "uptime_seconds": 0,
        }

    try:
        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_count = psutil.cpu_count(logical=True) or 1
        cpu_per_core = psutil.cpu_percent(percpu=True, interval=None)

        mem = psutil.virtual_memory()
        ram_total_gb = round(mem.total / (1024 ** 3), 2)
        ram_used_gb = round(mem.used / (1024 ** 3), 2)
        ram_free_gb = round(mem.available / (1024 ** 3), 2)
        ram_percent = mem.percent

        disk = psutil.disk_usage("/")
        disk_total_gb = round(disk.total / (1024 ** 3), 2)
        disk_used_gb = round(disk.used / (1024 ** 3), 2)
        disk_free_gb = round(disk.free / (1024 ** 3), 2)
        disk_percent = disk.percent

        boot_time = psutil.boot_time()
        uptime_seconds = int(time.time() - boot_time)

        # Process info
        proc = psutil.Process()
        proc_mem = proc.memory_info()
        proc_rss_mb = round(proc_mem.rss / (1024 * 1024), 2)

        return {
            "status": "pass" if cpu_percent < 90 and ram_percent < 90 else "warn",
            "cpu": {
                "percent": cpu_percent,
                "cores": cpu_count,
                "per_core": cpu_per_core,
            },
            "memory": {
                "total_gb": ram_total_gb,
                "used_gb": ram_used_gb,
                "free_gb": ram_free_gb,
                "percent": ram_percent,
            },
            "disk": {
                "total_gb": disk_total_gb,
                "used_gb": disk_used_gb,
                "free_gb": disk_free_gb,
                "percent": disk_percent,
            },
            "process": {
                "pid": os.getpid(),
                "rss_mb": proc_rss_mb,
            },
            "uptime_seconds": uptime_seconds,
        }
    except Exception as exc:
        logger.error(f"System resources check failed: {exc}", exc_info=True)
        return {"status": "fail", "error": str(exc)}


def _check_redis() -> Dict[str, Any]:
    """Check Redis connectivity, latency, and basic stats."""
    try:
        from app.database.redis_publisher import get_redis_client

        client = get_redis_client()
        start = time.perf_counter()
        client.ping()
        latency_ms = round((time.perf_counter() - start) * 1000, 2)

        info = client.info(section="server")
        memory_info = client.info(section="memory")
        return {
            "status": "pass",
            "latency_ms": latency_ms,
            "uptime_seconds": info.get("uptime_in_seconds", 0),
            "connected_clients": client.info(section="clients").get("connected_clients", 0),
            "used_memory_human": memory_info.get("used_memory_human", "0M"),
        }
    except Exception as exc:
        logger.error(f"Redis health check failed: {exc}")
        return {"status": "fail", "error": str(exc)}


def _check_sqlite() -> Dict[str, Any]:
    """Check SQLite database file size, query latency, and integrity."""
    try:
        db = get_sqlite_db()
        start = time.perf_counter()
        row = db.execute_one("SELECT 1 as ping;")
        latency_ms = round((time.perf_counter() - start) * 1000, 2)

        stats = db.get_database_stats()
        return {
            "status": "pass" if row and row.get("ping") == 1 else "fail",
            "latency_ms": latency_ms,
            "size_mb": stats.get("size_mb", 0.0),
            "wal_size_mb": stats.get("wal_size_mb", 0.0),
            "table_counts": stats.get("table_counts", {}),
        }
    except Exception as exc:
        logger.error(f"SQLite health check failed: {exc}")
        return {"status": "fail", "error": str(exc)}


def _check_celery_workers() -> Dict[str, Any]:
    """Check active Celery workers and inspect registered tasks."""
    try:
        from app.core.celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=1.0)
        ping_res = inspect.ping() if inspect else None
        if not ping_res:
            return {
                "status": "warn",
                "active_workers_count": 0,
                "workers": [],
                "message": "No active Celery workers responded to ping",
            }

        worker_names = list(ping_res.keys())
        return {
            "status": "pass",
            "active_workers_count": len(worker_names),
            "workers": worker_names,
        }
    except Exception as exc:
        return {"status": "unknown", "error": str(exc), "active_workers_count": 0}


def _check_batch_staleness() -> Dict[str, Any]:
    """Check whether the last batch ran within an acceptable window."""
    try:
        from app.core.settings import settings

        db = get_sqlite_db()
        row = db.execute_one("SELECT data FROM system_status WHERE id = 'batch_schedule';")
        if not row or not row.get("data"):
            return {"status": "unknown", "message": "No batch has run yet"}

        import json
        doc = json.loads(row["data"])
        last_triggered_raw = doc.get("last_triggered_at")
        if not last_triggered_raw:
            return {"status": "unknown", "message": "No timestamp in batch_schedule"}

        if isinstance(last_triggered_raw, str):
            last_triggered = datetime.fromisoformat(last_triggered_raw.replace("Z", "+00:00"))
        else:
            last_triggered = last_triggered_raw

        age_seconds = (datetime.now(timezone.utc) - last_triggered).total_seconds()
        threshold = settings.schedule_seconds * 2.5
        is_stale = age_seconds > threshold

        return {
            "status": "fail" if is_stale else "pass",
            "last_triggered_at": last_triggered.isoformat(),
            "age_seconds": round(age_seconds, 1),
            "threshold_seconds": threshold,
            "is_stale": is_stale,
        }
    except Exception as exc:
        logger.error(f"Batch staleness check failed: {exc}")
        return {"status": "unknown", "error": str(exc)}


def _check_trading_status() -> Dict[str, Any]:
    """Report active execution mode, live positions, and paper positions."""
    try:
        from app.broker.execution_manager import get_execution_manager

        mgr = get_execution_manager()
        mode = mgr.get_mode()
        armed = mgr.is_armed()
        delta_ready = mgr.delta_client.is_configured

        db = get_sqlite_db()
        paper_row = db.execute_one("SELECT COUNT(*) as cnt FROM broker_accounts WHERE open_position IS NOT NULL;")
        paper_open = paper_row["cnt"] if paper_row else 0

        live_row = db.execute_one("SELECT COUNT(*) as cnt FROM live_positions WHERE size != 0;")
        live_open = live_row["cnt"] if live_row else 0

        orders_row = db.execute_one("SELECT COUNT(*) as cnt FROM live_orders WHERE status = 'OPEN' OR status = 'FILLED';")
        live_orders = orders_row["cnt"] if orders_row else 0

        return {
            "status": "pass",
            "execution_mode": mode,
            "live_trading_armed": armed,
            "delta_api_configured": delta_ready,
            "paper_open_positions": paper_open,
            "live_open_positions": live_open,
            "live_orders_count": live_orders,
        }
    except Exception as exc:
        logger.error(f"Trading status check failed: {exc}")
        return {"status": "unknown", "error": str(exc)}


# ---------------------------------------------------------------------------
# Collector loop & public API
# ---------------------------------------------------------------------------

def _collect_all_metrics() -> Dict[str, Any]:
    """Run all checks and compile into a single health payload."""
    now_utc = datetime.now(timezone.utc).isoformat()
    system = _check_system_resources()
    redis_info = _check_redis()
    sqlite_info = _check_sqlite()
    celery_info = _check_celery_workers()
    batch_info = _check_batch_staleness()
    trading_info = _check_trading_status()

    # Determine overall status
    statuses = [
        system.get("status", "pass"),
        redis_info.get("status", "pass"),
        sqlite_info.get("status", "pass"),
        batch_info.get("status", "pass"),
    ]
    if "fail" in statuses:
        overall = "fail"
    elif "warn" in statuses:
        overall = "warn"
    else:
        overall = "pass"

    return {
        "status": overall,
        "collected_at": now_utc,
        "system": system,
        "sqlite": sqlite_info,
        "redis": redis_info,
        "celery": celery_info,
        "batch_staleness": batch_info,
        "trading": trading_info,
    }


def _collector_loop() -> None:
    """Daemon thread that periodically updates cached metrics."""
    global _cached_metrics
    logger.info("🩺 Health monitor collector loop started.")

    while _collector_running:
        try:
            metrics = _collect_all_metrics()
            with _cache_lock:
                _cached_metrics = metrics
            try:
                from app.core.event_bus import get_event_bus
                get_event_bus().publish("telemetry", metrics)
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:
            logger.error(f"Health monitor collector error: {exc}", exc_info=True)

        # Interruptible sleep
        elapsed = 0.0
        while elapsed < _SAMPLE_INTERVAL and _collector_running:
            time.sleep(_STOP_CHECK_SEC)
            elapsed += _STOP_CHECK_SEC

    logger.info("🩺 Health monitor collector loop stopped.")


def start_health_collector() -> None:
    """Starts the health collector daemon thread if enabled."""
    global _collector_thread, _collector_running

    if not _HEALTH_ENABLED:
        logger.info("Health monitoring is disabled via HEALTH_MONITOR_ENABLED=false")
        return

    with _collector_lock:
        if _collector_running:
            return
        _collector_running = True
        _collector_thread = threading.Thread(
            target=_collector_loop,
            daemon=True,
            name="HealthCollectorThread",
        )
        _collector_thread.start()


def stop_health_collector() -> None:
    """Stops the health collector thread."""
    global _collector_running
    with _collector_lock:
        _collector_running = False


def get_latest_health() -> Dict[str, Any]:
    """Returns the latest cached health metrics, or collects immediately if empty."""
    with _cache_lock:
        if _cached_metrics:
            return dict(_cached_metrics)

    # First read before collector has run: collect synchronously
    return _collect_all_metrics()
