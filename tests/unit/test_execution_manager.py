"""Unit tests for ExecutionManager routing, arming, and mode switching."""

from datetime import UTC, datetime

import pytest

from app.broker.execution_manager import ARM_CONFIRMATION_PHRASE, ExecutionManager
from app.models.strategy_models import SignalType


def test_execution_manager_defaults():
    """Verify default mode is LIVE and disarmed."""
    mgr = ExecutionManager()
    assert mgr.get_mode() == "LIVE"


def test_execution_manager_mode_switch():
    """Verify mode setting."""
    mgr = ExecutionManager()
    mgr.set_mode("LIVE")
    assert mgr.get_mode() == "LIVE"


def test_execution_manager_rejects_live_arming():
    """Delta remains monitoring-only regardless of confirmation or credentials."""
    mgr = ExecutionManager()
    with pytest.raises(ValueError, match="disabled"):
        mgr.arm_live_trading("wrong phrase")
    with pytest.raises(ValueError, match="disabled"):
        mgr.arm_live_trading(ARM_CONFIRMATION_PHRASE)
    assert mgr.is_armed() is False


def test_execution_manager_disarm():
    """Verify disarming resets mode to LIVE."""
    mgr = ExecutionManager()
    res = mgr.disarm_live_trading()
    assert res["armed"] is False
    assert res["mode"] == "LIVE"
    assert mgr.is_armed() is False


def test_execution_manager_signal_routing():
    """Verify signal routing in LIVE mode when disarmed."""
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
