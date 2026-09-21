"""Unit tests for the Services layer (app/services/)."""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from app.services.analytics_service import AnalyticsService, get_analytics_service
from app.services.broker_service import BrokerService, get_broker_service
from app.services.log_service import LogService
from app.services.strategy_service import StrategyService, get_strategy_service
from app.services.system_service import (
    SystemService,
    get_system_service,
)


def test_system_service_singleton_and_methods():
    """Verify SystemService methods return properly structured dictionaries."""
    service = get_system_service()
    assert isinstance(service, SystemService)

    # get_metrics
    metrics = service.get_metrics()
    assert metrics["status"] == "success"
    assert "resources" in metrics
    assert "sqlite" in metrics
    assert "redis" in metrics
    assert "celery" in metrics

    # get_config
    config = service.get_config()
    assert "symbols" in config
    assert "strategies" in config
    assert "execution_mode" in config

    # get_batch_schedule
    sched = service.get_batch_schedule()
    assert "interval_seconds" in sched

    # reset_system with invalid phrase
    with pytest.raises(ValueError, match="Confirmation phrase"):
        service.reset_system("WRONG PHRASE")


def test_broker_service_status_and_modes():
    """Verify BrokerService status, mode switching, and arming validation."""
    service = get_broker_service()
    assert isinstance(service, BrokerService)

    status = service.get_status()
    assert "execution_mode" in status
    assert "is_armed" in status

    # Toggle to PAPER
    res_paper = service.set_execution_mode("PAPER")
    assert res_paper["execution_mode"] == "PAPER"

    # Toggle to invalid mode
    with pytest.raises(ValueError, match="Execution mode must be"):
        service.set_execution_mode("INVALID_MODE")

    # Toggle to LIVE without arming first
    with pytest.raises(ValueError, match="Live trading must be armed first"):
        service.set_execution_mode("LIVE")

    # Arming with wrong phrase
    with pytest.raises(ValueError, match="Confirmation phrase must exactly match"):
        service.arm_live_trading("wrong")

    # Arming with valid phrase using mock
    mock_client = MagicMock()
    type(mock_client).is_configured = PropertyMock(return_value=True)
    mock_service = BrokerService(delta_client=mock_client)
    with patch.object(type(mock_service.mgr.delta_client), "is_configured", new_callable=PropertyMock, return_value=True):
        arm_res = mock_service.arm_live_trading("ARM LIVE TRADING")
        assert arm_res["armed"] is True
        assert mock_service.mgr.is_armed() is True

    # Disarm
    disarm_res = mock_service.disarm_live_trading()
    assert disarm_res["armed"] is False
    assert mock_service.mgr.is_armed() is False


def test_broker_profile_and_authority_verification():
    """Verify broker profile retrieval, saving, authority testing, and live trading toggle."""
    service = get_broker_service()

    # Get profile
    profile = service.get_broker_profile()
    assert "base_url" in profile
    assert "api_key_masked" in profile
    assert "is_authorized" in profile
    assert "is_live_enabled" in profile

    # Save profile
    save_res = service.save_broker_profile(
        base_url="https://api.india.delta.exchange",
        api_key="test_api_key_12345",
        api_secret="test_api_secret_67890",
        client_id=12345,
    )
    assert save_res["success"] is True
    assert service.delta_client.api_key == "test_api_key_12345"

    # Toggle live trading without arming/confirmation
    with pytest.raises(ValueError, match="Confirmation phrase"):
        service.toggle_live_trading(enabled=True, confirmation="WRONG")

    # Toggle live trading with valid confirmation (mocking DeltaClient.is_configured)
    with patch.object(type(service.delta_client), "is_configured", new_callable=PropertyMock, return_value=True):
        toggle_res = service.toggle_live_trading(enabled=True, confirmation="ARM LIVE TRADING")
        assert toggle_res["enabled"] is True
        assert toggle_res["execution_mode"] == "LIVE"

        # Toggle live trading disabled
        dis_res = service.toggle_live_trading(enabled=False)
        assert dis_res["enabled"] is False
        assert dis_res["execution_mode"] == "PAPER"


def test_strategy_service_methods():
    """Verify StrategyService returns stats and calendar."""
    service = get_strategy_service()
    assert isinstance(service, StrategyService)

    stats = service.get_global_stats()
    assert "total_capital" in stats
    assert "total_trades" in stats
    assert "active_strategies" in stats

    strat_list = service.get_strategies_stats()
    assert isinstance(strat_list, list)

    cal = service.get_calendar()
    assert "is_trading_day" in cal


def test_analytics_service_methods():
    """Verify AnalyticsService calculates equity curves and performance metrics."""
    service = get_analytics_service()
    assert isinstance(service, AnalyticsService)

    curve = service.get_portfolio_equity_curve()
    assert isinstance(curve, list)

    trades = service.get_recent_trades(limit=10)
    assert isinstance(trades, list)

    analytics = service.get_strategy_analytics()
    assert isinstance(analytics, list)


def test_log_service_methods(tmp_path):
    """Verify LogService log retrieval, sanitization, and errors."""
    service = LogService(logs_dir=tmp_path)
    assert isinstance(service, LogService)

    # Invalid log type
    with pytest.raises(ValueError, match="Invalid log type"):
        service.get_logs("malicious_path/../../etc/passwd")

    with pytest.raises(ValueError, match="Invalid log type"):
        service.get_log_file_path("invalid")

    # Non-existent valid file returns placeholder message
    lines = service.get_logs("success")
    assert len(lines) == 1
    assert "does not exist yet" in lines[0]

    # Writing to a log file and reading back
    success_file = tmp_path / "success.log"
    success_file.write_text("line 1\nline 2\nline 3\n")

    read_lines = service.get_logs("success", lines_count=2)
    assert read_lines == ["line 2", "line 3"]

    path = service.get_log_file_path("success")
    assert path.exists()
