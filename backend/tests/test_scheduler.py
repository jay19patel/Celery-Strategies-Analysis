"""Tests for the EventWindowTrigger and reusable market timing schedules."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from app.core.scheduler import EventWindowTrigger, Scheduler
from app.trading.timing import (
    get_funding_window,
    get_settlement_window,
    get_zerohero_window,
    next_daily_settlement_utc,
)


def test_next_daily_settlement_utc_returns_future_12utc() -> None:
    """Verifies that next settlement is strictly in future at 12:00 UTC."""
    # 2026-10-31 10:00:00 UTC -> next settlement is 2026-10-31 12:00:00 UTC
    dt_morning = datetime(2026, 10, 31, 10, 0, 0, tzinfo=UTC).timestamp()
    target_today = next_daily_settlement_utc(dt_morning)
    expected_today = datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC).timestamp()
    assert target_today == expected_today

    # 2026-10-31 13:00:00 UTC (after 12:00 UTC) -> next settlement is 2026-11-01 12:00:00 UTC
    dt_afternoon = datetime(2026, 10, 31, 13, 0, 0, tzinfo=UTC).timestamp()
    target_tomorrow = next_daily_settlement_utc(dt_afternoon)
    expected_tomorrow = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC).timestamp()
    assert target_tomorrow == expected_tomorrow


def test_zerohero_window_bounds_match_start_and_end_hours() -> None:
    """Zero-hero window should span exactly window_start_hours to window_end_hours before expiry."""
    from types import SimpleNamespace

    dt_now = datetime(2026, 10, 31, 6, 0, 0, tzinfo=UTC).timestamp()
    fake_settings = SimpleNamespace(zh_window_start_hours=4.0, zh_window_end_hours=2.0)
    w_start, w_end, desc = get_zerohero_window(
        now_ts=dt_now,
        settings=fake_settings,  # type: ignore[arg-type]
    )
    settlement = datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC).timestamp()
    assert w_start == settlement - 4.0 * 3600.0
    assert w_end == settlement - 2.0 * 3600.0
    assert "13:30–15:30 IST" in desc


def test_funding_window_triggers_before_funding_events() -> None:
    """Funding window aligns with 8-hour intervals (00:00, 08:00, 16:00 UTC)."""
    # 07:40 UTC: next funding is 08:00 UTC. 15m lead = window from 07:45 to 08:00
    now_ts = datetime(2026, 10, 31, 7, 40, 0, tzinfo=UTC).timestamp()
    w_start, w_end, desc = get_funding_window(now_ts, lead_minutes=15.0, window_duration_minutes=15.0)
    expected_fund = datetime(2026, 10, 31, 8, 0, 0, tzinfo=UTC).timestamp()
    assert w_end == expected_fund
    assert w_start == expected_fund - 15 * 60
    assert "Funding" in desc


def test_event_window_trigger_schedules_at_window_start_when_outside() -> None:
    """When outside window, next fire time should be precisely the window start."""
    now_dt = datetime(2026, 10, 31, 6, 0, 0, tzinfo=UTC)
    now_ts = now_dt.timestamp()

    w_start_ts = now_ts + 7200.0  # window opens in 2 hours
    w_end_ts = w_start_ts + 3600.0

    def mock_window(t: float) -> tuple[float, float, str]:
        return w_start_ts, w_end_ts, "test window"

    trigger = EventWindowTrigger(
        get_window=mock_window,
        active_interval=60.0,
        idle_heartbeat=14400.0,
    )

    next_fire = trigger.get_next_fire_time(None, now_dt)
    assert next_fire is not None
    assert next_fire.timestamp() == pytest.approx(w_start_ts, abs=1.0)


def test_event_window_trigger_schedules_at_active_interval_when_inside() -> None:
    """When inside window, next fire time should be now + active_interval."""
    now_dt = datetime(2026, 10, 31, 8, 30, 0, tzinfo=UTC)
    now_ts = now_dt.timestamp()

    w_start_ts = now_ts - 1800.0  # opened 30m ago
    w_end_ts = now_ts + 1800.0    # closes in 30m

    def mock_window(t: float) -> tuple[float, float, str]:
        return w_start_ts, w_end_ts, "active window"

    trigger = EventWindowTrigger(
        get_window=mock_window,
        active_interval=45.0,
        idle_heartbeat=1800.0,
    )

    prev_fire = datetime(2026, 10, 31, 8, 29, 30, tzinfo=UTC)
    next_fire = trigger.get_next_fire_time(prev_fire, now_dt)
    assert next_fire is not None
    assert next_fire.timestamp() == pytest.approx(now_dt.timestamp() + 45.0, abs=1.0)
