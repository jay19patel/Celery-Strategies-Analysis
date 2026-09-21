"""Unit tests for Dashboard FastAPI endpoints in frontend.main."""

from fastapi.testclient import TestClient

from frontend.main import app

client = TestClient(app)


def test_dashboard_index_endpoint():
    """Verify that root endpoint serves the dashboard HTML."""
    response = client.get("/")
    assert response.status_code == 200
    assert "TradeBuddy" in response.text or "html" in response.text.lower()


def test_system_metrics_endpoint():
    """Verify that /api/system/metrics returns CPU, RAM, Disk, and SQLite stats."""
    response = client.get("/api/system/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "resources" in data
    assert "sqlite" in data
    assert "celery" in data


def test_broker_status_endpoint():
    """Verify broker status endpoint returns execution mode and settings."""
    response = client.get("/api/broker/status")
    assert response.status_code == 200
    data = response.json()
    assert data["execution_mode"] in ("PAPER", "LIVE")
    assert "is_armed" in data
    assert "delta_configured" in data


def test_broker_balance_positions_orders():
    """Verify broker balance, positions, and orders query endpoints."""
    bal_res = client.get("/api/broker/balance")
    assert bal_res.status_code == 200
    assert "available_balance_usd" in bal_res.json()

    pos_res = client.get("/api/broker/positions")
    assert pos_res.status_code == 200
    assert isinstance(pos_res.json(), list)

    ord_res = client.get("/api/broker/orders")
    assert ord_res.status_code == 200
    assert isinstance(ord_res.json(), list)


def test_broker_profile_endpoints():
    """Verify broker profile retrieval, profile saving, and toggle endpoints."""
    prof_res = client.get("/api/broker/profile")
    assert prof_res.status_code == 200
    prof = prof_res.json()
    assert "base_url" in prof
    assert "api_key_masked" in prof
    assert "is_authorized" in prof
    assert "is_live_enabled" in prof

    # Save profile via API
    save_res = client.post(
        "/api/broker/profile",
        json={
            "base_url": "https://api.india.delta.exchange",
            "api_key": "test_api_key_999",
            "api_secret": "test_api_secret_888",
            "client_id": 999,
        },
    )
    assert save_res.status_code == 200
    assert save_res.json()["success"] is True

    # Toggle live trading disabled
    toggle_off = client.post("/api/broker/toggle-live", json={"enabled": False})
    assert toggle_off.status_code == 200
    assert toggle_off.json()["enabled"] is False


def test_stats_and_config_endpoints():
    """Verify global stats and config endpoints."""
    stats_res = client.get("/api/stats")
    assert stats_res.status_code == 200
    assert "total_capital" in stats_res.json()

    conf_res = client.get("/api/config")
    assert conf_res.status_code == 200
    assert "symbols" in conf_res.json()
    assert "batch_schedule_seconds" in conf_res.json()

    # Update trading config
    update_res = client.post("/api/system/config", json={"trade_capital_pct": 40.0, "risk_ratio": 0.02})
    assert update_res.status_code == 200
    assert update_res.json()["success"] is True
    assert update_res.json()["params"]["trade_capital_pct"] == 40.0

    # Reset paper balances
    reset_paper_res = client.post("/api/system/reset-paper", json={"starting_capital": 250.0})
    assert reset_paper_res.status_code == 200
    assert reset_paper_res.json()["success"] is True
    assert reset_paper_res.json()["starting_capital"] == 250.0



def test_analytics_and_trades_endpoints():
    """Verify analytics and closed trade endpoints."""
    curve_res = client.get("/api/portfolio/equity-curve")
    assert curve_res.status_code == 200
    assert isinstance(curve_res.json(), list)

    trades_res = client.get("/api/trades?limit=10")
    assert trades_res.status_code == 200
    assert isinstance(trades_res.json(), list)

    analytics_res = client.get("/api/analytics")
    assert analytics_res.status_code == 200
    assert isinstance(analytics_res.json(), list)


def test_trading_calendar_endpoint():
    """Verify trading calendar API endpoint."""
    response = client.get("/api/calendar")
    assert response.status_code == 200
    data = response.json()
    assert "date" in data
    assert "is_trading_day" in data


def test_logs_endpoint():
    """Verify system logs retrieval endpoint."""
    response = client.get("/api/logs/success?lines_count=10")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_strategy_signals_endpoint():
    """Verify retrieval of strategy signals log."""
    response = client.get("/api/strategy/signals?limit=10")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_trigger_signal_endpoint():
    """Verify manual signal trigger pipeline."""
    payload = {
        "strategy_name": "CombinedPortfolioStrategy",
        "symbol": "BTC-USD",
        "signal_type": "BUY",
        "price": 60000.0,
        "confidence": 0.95,
    }
    response = client.post("/api/strategy/trigger", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["signal"] == "BUY"
    assert "mode" in data
    assert "action" in data


def test_strategy_list_and_toggle_endpoints():
    """Verify detailed strategy list retrieval and toggle functionality."""
    res = client.get("/api/strategy/list")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 2
    strat = data[0]
    assert "strategy_id" in strat
    assert "timeframe" in strat
    assert "symbols" in strat
    assert "is_paper_enabled" in strat
    assert "is_real_enabled" in strat
    assert "total_signals" in strat
    assert "paper_orders_count" in strat

    # Test toggling paper execution
    toggle_payload = {
        "strategy_id": strat["strategy_id"],
        "is_paper_enabled": False,
        "is_real_enabled": False,
    }
    toggle_res = client.post("/api/strategy/toggle", json=toggle_payload)
    assert toggle_res.status_code == 200
    toggle_data = toggle_res.json()
    assert toggle_data["is_paper_enabled"] is False

    # Restore to True
    toggle_payload["is_paper_enabled"] = True
    restore_res = client.post("/api/strategy/toggle", json=toggle_payload)
    assert restore_res.status_code == 200
    assert restore_res.json()["is_paper_enabled"] is True


def test_parsed_error_logs_endpoint():
    """Verify parsed error logs endpoint returns list with structured fields."""
    res = client.get("/api/logs/errors/parsed?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    if data:
        entry = data[0]
        assert "timestamp" in entry
        assert "message" in entry
        assert "traceback" in entry


def test_system_pipeline_settings_endpoints():
    """Verify updating and retrieving dynamic pipeline settings."""
    payload = {
        "symbols": "BTC-USD,ETH-USD,SOL-USD",
        "strategies": "*",
        "schedule_seconds": 60,
    }
    res = client.post("/api/system/pipeline-settings", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["settings"]["symbols_raw"] == "BTC-USD,ETH-USD,SOL-USD"
    assert data["settings"]["strategies_raw"] == "*"
    assert data["settings"]["schedule_seconds"] == 60

    # Verify config endpoint includes active pipeline settings
    config_res = client.get("/api/system/config")
    assert config_res.status_code == 200
    cfg = config_res.json()
    assert "symbols" in cfg
    assert "strategies" in cfg
    assert "schedule_seconds" in cfg
    assert cfg["schedule_seconds"] == 60

