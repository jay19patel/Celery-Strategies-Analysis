"""Unit tests for health_monitor system metrics."""

from app.core.health_monitor import _check_sqlite, _check_system_resources, get_latest_health


def test_system_resources_metrics():
    """Verify that psutil returns CPU, RAM, and Disk metrics."""
    res = _check_system_resources()
    assert "cpu" in res
    assert "memory" in res
    assert "disk" in res
    assert res["cpu"]["cores"] >= 1
    assert res["memory"]["total_gb"] > 0
    assert res["disk"]["total_gb"] > 0


def test_sqlite_health_check():
    """Verify that SQLite health check measures latency and file metrics."""
    res = _check_sqlite()
    assert res["status"] == "pass"
    assert "latency_ms" in res
    assert "table_counts" in res


def test_get_latest_health():
    """Verify aggregated health payload."""
    health = get_latest_health()
    assert "system" in health
    assert "sqlite" in health
    assert "trading" in health
    assert health["status"] in ("pass", "warn", "fail")
