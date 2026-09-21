"""Unit tests for Celery inspection, WebSocket status, and ZeroMQ telemetry."""

from fastapi.testclient import TestClient
from frontend.main import app
from app.core.health_monitor import _check_celery_workers, _check_websocket, _check_zeromq
from app.broker.delta.websocket import get_delta_websocket_client
from app.core.event_bus import get_event_bus

client = TestClient(app)


def test_check_celery_workers_structure():
    """Verify _check_celery_workers returns expected keys and types."""
    res = _check_celery_workers()
    assert "status" in res
    assert "active_workers_count" in res
    assert "workers" in res
    assert "worker_nodes" in res
    assert "queue_depth" in res
    assert "registered_tasks" in res
    assert "active_tasks" in res
    assert "total_tasks_completed" in res


def test_check_websocket_status():
    """Verify Delta WebSocket status retrieval."""
    ws = get_delta_websocket_client()
    status = ws.get_status()
    assert "is_configured" in status
    assert "is_running" in status
    assert "is_connected" in status
    assert "ws_url" in status
    assert "subscribed_channels" in status
    assert "messages_received" in status
    assert "reconnect_count" in status

    # Verify health_monitor wrapper
    hw = _check_websocket()
    assert "status" in hw
    assert "is_connected" in hw


def test_check_zeromq_status():
    """Verify ZeroMQ EventBus status and publishing metrics."""
    bus = get_event_bus()
    bus.publish("test_topic", {"key": "val"})

    status = bus.get_status()
    assert "status" in status
    assert "port" in status
    assert status["port"] == 5557
    assert "mode" in status
    assert "packets_published" in status
    assert status["packets_published"] >= 1
    assert "topics" in status
    assert "test_topic" in status["topics"]

    # Verify health_monitor wrapper
    hz = _check_zeromq()
    assert "port" in hz
    assert hz["port"] == 5557


def test_api_system_celery_endpoint():
    """Verify GET /api/system/celery endpoint."""
    res = client.get("/api/system/celery")
    assert res.status_code == 200
    data = res.json()
    assert "status" in data
    assert "active_workers_count" in data
    assert "queue_depth" in data
    assert "scheduled_beat_tasks" in data
    assert isinstance(data["scheduled_beat_tasks"], list)


def test_api_system_telemetry_endpoint():
    """Verify GET /api/system/telemetry endpoint."""
    res = client.get("/api/system/telemetry")
    assert res.status_code == 200
    data = res.json()
    assert "celery" in data
    assert "websocket" in data
    assert "zeromq" in data
    assert data["zeromq"]["port"] == 5557
