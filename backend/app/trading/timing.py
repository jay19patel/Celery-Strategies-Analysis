"""Event-window timing utilities for options expiry, funding rates, and settlement windows.

Provides reusable time calculations for scheduling trades that depend on fixed market deadlines:
- Zero-Hero: Convexity momentum window (typically 4h to 2h before Delta daily 12:00 UTC settlement)
- Pre-Settlement: Execution window right before options expire (e.g. 30m before 12:00 UTC)
- Funding Arbitrage: Execution window right before 8-hour perp funding timestamps (00:00, 08:00, 16:00 UTC)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import zoneinfo
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.settings import Settings

# Delta Exchange India BTC/ETH options settle daily at 12:00:00 UTC (17:30:00 IST)
EXPIRY_HOUR_UTC = 12
IST = zoneinfo.ZoneInfo("Asia/Kolkata")


def next_daily_settlement_utc(now_ts: float | None = None) -> float:
    """Return the UNIX timestamp of the next daily 12:00 UTC settlement.

    If current time is before 12:00 UTC today, returns today at 12:00 UTC.
    If current time is at or past 12:00 UTC today, returns tomorrow at 12:00 UTC.
    """
    now = datetime.now(UTC) if now_ts is None else datetime.fromtimestamp(now_ts, UTC)
    target = now.replace(hour=EXPIRY_HOUR_UTC, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target.timestamp()


def get_zerohero_window(
    now_ts: float,
    settings: Settings,
    listed_expiry: float | None = None,
) -> tuple[float, float, str]:
    """Calculate the active scanning window for Zero-Hero trades.

    Returns:
        (window_start_unix, window_end_unix, human_readable_description)
    """
    # Use listed live expiry if available and in the future; otherwise compute mathematical settlement
    expiry = listed_expiry if listed_expiry and listed_expiry > now_ts else next_daily_settlement_utc(now_ts)

    start_hours = settings.zh_window_start_hours
    end_hours = settings.zh_window_end_hours

    w_start = expiry - start_hours * 3600
    w_end = expiry - end_hours * 3600

    # If the window for this expiry has already closed, roll over to the next day's expiry
    if now_ts >= w_end:
        next_expiry = next_daily_settlement_utc(expiry + 3600)
        w_start = next_expiry - start_hours * 3600
        w_end = next_expiry - end_hours * 3600
        expiry = next_expiry

    dt_start_ist = datetime.fromtimestamp(w_start, tz=IST)
    dt_end_ist = datetime.fromtimestamp(w_end, tz=IST)
    dt_exp_ist = datetime.fromtimestamp(expiry, tz=IST)

    desc = (
        f"Expiry {dt_exp_ist.strftime('%d %b %H:%M IST')} "
        f"(Window: {dt_start_ist.strftime('%H:%M')}–{dt_end_ist.strftime('%H:%M IST')})"
    )
    return w_start, w_end, desc


def get_funding_window(
    now_ts: float,
    lead_minutes: float = 10.0,
    window_duration_minutes: float = 15.0,
) -> tuple[float, float, str]:
    """Calculate the next 8-hour funding rate window (00:00, 08:00, 16:00 UTC).

    Window starts `lead_minutes` before funding time and lasts `window_duration_minutes`.
    """
    now = datetime.fromtimestamp(now_ts, UTC)
    # Find next funding hour among [0, 8, 16]
    funding_hours = [0, 8, 16, 24]
    current_hour = now.hour + now.minute / 60.0 + now.second / 3600.0

    target_hour = next((h for h in funding_hours if h > current_hour), None)
    if target_hour is None or target_hour == 24:
        target_dt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        target_dt = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)

    target_ts = target_dt.timestamp()
    w_start = target_ts - lead_minutes * 60
    w_end = w_start + window_duration_minutes * 60

    dt_ist = datetime.fromtimestamp(target_ts, tz=IST)
    desc = f"Funding {dt_ist.strftime('%H:%M IST')}"
    return w_start, w_end, desc


def get_settlement_window(
    now_ts: float,
    lead_minutes: float = 30.0,
) -> tuple[float, float, str]:
    """Calculate the pre-settlement window right before daily 12:00 UTC expiry.

    Useful for position unwinding, rolling, or theta risk prevention.
    """
    expiry = next_daily_settlement_utc(now_ts)
    w_start = expiry - lead_minutes * 60
    w_end = expiry

    if now_ts >= w_end:
        next_expiry = next_daily_settlement_utc(expiry + 3600)
        w_start = next_expiry - lead_minutes * 60
        w_end = next_expiry

    dt_ist = datetime.fromtimestamp(w_start, tz=IST)
    desc = f"Pre-Settlement {dt_ist.strftime('%H:%M IST')}"
    return w_start, w_end, desc
