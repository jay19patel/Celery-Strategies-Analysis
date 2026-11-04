"""Event-window timing: the zero-hero scan window, typically 4h to 2h before the daily 12:00 UTC
settlement of Delta's options."""

from __future__ import annotations

import zoneinfo
from datetime import UTC, datetime, timedelta
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
