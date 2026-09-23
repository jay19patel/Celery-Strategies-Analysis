"""Prometheus exposition backed by the shared health-monitor snapshot."""

from collections.abc import Iterable

from prometheus_client.core import REGISTRY, GaugeMetricFamily

from app.core.health_monitor import get_latest_health


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float, bool)) else 0.0


class HealthSnapshotCollector:
    """Translate cached application health into Prometheus metric families."""

    def collect(self) -> Iterable[GaugeMetricFamily]:
        health = get_latest_health()
        system = health.get("system", {})
        sqlite = health.get("sqlite", {})
        redis = health.get("redis", {})
        celery = health.get("celery", {})
        trading = health.get("trading", {})
        websocket = health.get("websocket", {})
        zeromq = health.get("zeromq", {})

        values = {
            "tradebuddy_system_cpu_percent": _number(system.get("cpu", {}).get("percent")),
            "tradebuddy_system_memory_percent": _number(system.get("memory", {}).get("percent")),
            "tradebuddy_system_disk_percent": _number(system.get("disk", {}).get("percent")),
            "tradebuddy_process_resident_memory_bytes": _number(system.get("process", {}).get("rss_mb")) * 1024 * 1024,
            "tradebuddy_sqlite_up": float(sqlite.get("status") == "pass"),
            "tradebuddy_sqlite_latency_seconds": _number(sqlite.get("latency_ms")) / 1000,
            "tradebuddy_sqlite_size_bytes": _number(sqlite.get("size_mb")) * 1024 * 1024,
            "tradebuddy_redis_up": float(redis.get("status") == "pass"),
            "tradebuddy_redis_latency_seconds": _number(redis.get("latency_ms")) / 1000,
            "tradebuddy_redis_connected_clients": _number(redis.get("connected_clients")),
            "tradebuddy_redis_memory_bytes": _number(redis.get("used_memory_bytes")),
            "tradebuddy_celery_workers": _number(celery.get("active_workers_count")),
            "tradebuddy_celery_active_tasks": len(celery.get("active_tasks", [])),
            "tradebuddy_celery_queue_depth": _number(celery.get("queue_depth")),
            "tradebuddy_celery_tasks_completed_total": _number(celery.get("total_tasks_completed")),
            "tradebuddy_paper_open_positions": _number(trading.get("paper_open_positions")),
            "tradebuddy_live_open_positions": _number(trading.get("live_open_positions")),
            "tradebuddy_live_orders": _number(trading.get("live_orders_count")),
            "tradebuddy_delta_websocket_connected": float(bool(websocket.get("is_connected"))),
            "tradebuddy_zeromq_packets_published_total": _number(zeromq.get("packets_published")),
        }
        for name, value in values.items():
            metric = GaugeMetricFamily(name, f"TradeBuddy metric {name}")
            metric.add_metric([], value)
            yield metric

        table_rows = GaugeMetricFamily(
            "tradebuddy_sqlite_table_rows",
            "Rows stored in each monitored SQLite table",
            labels=["table"],
        )
        for table, count in sqlite.get("table_counts", {}).items():
            table_rows.add_metric([str(table)], _number(count))
        yield table_rows


PROMETHEUS_REGISTRY = REGISTRY
PROMETHEUS_REGISTRY.register(HealthSnapshotCollector())
