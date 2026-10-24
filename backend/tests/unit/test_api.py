"""Unit tests for FastAPI endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import require_auth
from app.main import app


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient fixture with mocked auth."""
    # Override require_auth dependency to allow requests in test
    app.dependency_overrides[require_auth] = lambda: True
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_check(client: TestClient):
    """Verify /api/system/health returns 200 with operational stats."""
    response = client.get("/api/system/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "cpu_percent" in data
    assert "memory_usage_mb" in data


def test_auth_verify(client: TestClient):
    """Verify /api/auth/verify endpoint."""
    response = client.get("/api/auth/verify")
    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


def test_paper_account(client: TestClient):
    """Verify /api/paper/account returns balance structure."""
    response = client.get("/api/paper/account")
    assert response.status_code == 200
    data = response.json()
    assert "balance" in data
    assert "starting_balance" in data


def test_settings_endpoints(client: TestClient):
    """Verify GET /api/settings and PUT /api/settings."""
    response = client.get("/api/settings")
    assert response.status_code == 200

    # Updating setting with valid payload
    put_response = client.put("/api/settings", json={"settings": {"stop_loss_pct": "2.0"}, "confirm": ""})
    assert put_response.status_code == 200
    assert put_response.json()["status"] == "updated"


def test_trading_order_validation(client: TestClient):
    """Verify POST /api/trading/orders validates invalid payload."""
    response = client.post(
        "/api/trading/orders",
        json={
            "symbol": "BTCUSD",
            "side": "invalid_side",
            "size": 1,
            "price": 50000.0,
        },
    )
    assert response.status_code == 422
