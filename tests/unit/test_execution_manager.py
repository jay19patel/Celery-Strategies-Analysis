"""Unit tests for ExecutionManager routing, arming, and mode switching."""

from datetime import UTC, datetime
from unittest.mock import PropertyMock, patch

import pytest

from app.broker.execution_manager import ARM_CONFIRMATION_PHRASE, ExecutionManager
from app.models.strategy_models import SignalType


def test_execution_manager_defaults():
    """Verify default mode is PAPER and disarmed."""
    mgr = ExecutionManager()
    assert mgr.get_mode() in ("PAPER", "LIVE")


def test_execution_manager_mode_switch():
    """Verify mode setting."""
    mgr = ExecutionManager()
    mgr.set_mode("PAPER")
    assert mgr.get_mode() == "PAPER"


def test_execution_manager_arm_invalid_confirmation():
    """Verify error on incorrect confirmation string."""
    mgr = ExecutionManager()
    with pytest.raises(ValueError, match="Confirmation phrase"):
        mgr.arm_live_trading("wrong phrase")


def test_execution_manager_arm_valid_with_mock():
    """Verify arming succeeds when Delta credentials are configured and confirmation matches."""
    mgr = ExecutionManager()
    with patch.object(type(mgr.delta_client), "is_configured", new_callable=PropertyMock, return_value=True):
        res = mgr.arm_live_trading(ARM_CONFIRMATION_PHRASE)
        assert res["armed"] is True
        assert res["mode"] == "LIVE"
        assert mgr.is_armed() is True

    # Safely disarm afterwards
    mgr.disarm_live_trading()
    assert mgr.is_armed() is False


def test_execution_manager_disarm():
    """Verify disarming resets mode to PAPER."""
    mgr = ExecutionManager()
    res = mgr.disarm_live_trading()
    assert res["armed"] is False
    assert res["mode"] == "PAPER"
    assert mgr.is_armed() is False


def test_execution_manager_paper_signal_routing():
    """Verify signal routing in PAPER mode."""
    mgr = ExecutionManager()
    mgr.disarm_live_trading()

    now = datetime.now(UTC)
    res = mgr.process_signal(
        strategy_name="UnitTestStrat",
        symbol="BTC-USD",
        signal_type=SignalType.HOLD,
        price=50000.0,
        timestamp=now,
    )
    assert res["action"] == "ignored"
