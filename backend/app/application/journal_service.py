"""Journal service — daily and monthly trade summaries."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.application.base_service import BaseService
from app.application.paper_service import PaperService


class JournalService(BaseService):
    """Aggregates trade data into daily and monthly summaries."""

    def __init__(self, paper_service: PaperService, timezone: str = "Asia/Kolkata") -> None:
        super().__init__()
        self._paper = paper_service
        self._tz = ZoneInfo(timezone)

    def get_day_summary(self, day: str) -> dict[str, Any]:
        """Summary of trades for a day (YYYY-MM-DD)."""
        dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=self._tz)
        start_ts = dt.timestamp()
        end_ts = (dt + timedelta(days=1)).timestamp()

        trades = self._paper.get_trades_in_range(start_ts, end_ts)
        if not trades:
            return {"day": day, "trade_count": 0, "total_pnl": 0.0, "win_rate": 0.0, "trades": []}

        pnls = [t["pnl"] for t in trades]
        winning = [p for p in pnls if p > 0]

        return {
            "day": day,
            "trade_count": len(trades),
            "total_pnl": round(sum(pnls), 2),
            "winning": len(winning),
            "losing": len(pnls) - len(winning),
            "win_rate": round(len(winning) / len(pnls) * 100, 1),
            "best_trade": round(max(pnls), 2),
            "worst_trade": round(min(pnls), 2),
            "trades": trades,
        }

    def get_month_summary(self, month: str) -> dict[str, Any]:
        """Monthly calendar data (YYYY-MM) — PnL per day for heatmap."""
        dt = datetime.strptime(f"{month}-01", "%Y-%m-%d").replace(tzinfo=self._tz)
        _, days_in_month = calendar.monthrange(dt.year, dt.month)
        end_ts = (dt.replace(day=days_in_month) + timedelta(days=1)).timestamp()

        trades = self._paper.get_trades_in_range(dt.timestamp(), end_ts)

        daily: dict[str, list[dict[str, Any]]] = {}
        for t in trades:
            trade_day = datetime.fromtimestamp(t["closed_at"], tz=self._tz).strftime("%Y-%m-%d")
            daily.setdefault(trade_day, []).append(t)

        days = []
        total_pnl = 0.0
        total_trades = 0

        for offset in range(days_in_month):
            current = dt + timedelta(days=offset)
            day_str = current.strftime("%Y-%m-%d")
            day_trades = daily.get(day_str, [])
            day_pnl = sum(t["pnl"] for t in day_trades)
            days.append({"day": day_str, "weekday": current.strftime("%a"), "trade_count": len(day_trades), "pnl": round(day_pnl, 2)})
            total_pnl += day_pnl
            total_trades += len(day_trades)

        return {
            "month": month, "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
            "trading_days": sum(1 for d in days if d["trade_count"] > 0),
            "days": days,
        }
