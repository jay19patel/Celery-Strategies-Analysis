"""Analytics and quantitative performance service.

Computes portfolio equity curves, win/loss metrics, profit factors,
per-strategy breakdowns, and paginated closed trade histories.
"""

import json
import logging
from datetime import UTC
from typing import Any

from app.broker.delta.price_feed import get_live_price
from app.database.sqlite_db import get_sqlite_db

logger = logging.getLogger(__name__)


def _iso_utc(dt: Any) -> str | None:
    """Serialize a datetime or timestamp to ISO-8601 UTC format."""
    if not dt:
        return None
    if isinstance(dt, str):
        return dt
    if hasattr(dt, "isoformat"):
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()
    return str(dt)


class AnalyticsService:
    """Service encapsulating portfolio equity progression, trade logs, and performance metrics."""

    def __init__(self) -> None:
        """Initialize SQLite database access."""
        self.db = get_sqlite_db()

    def get_portfolio_equity_curve(self) -> list[dict[str, Any]]:
        """Build cumulative portfolio equity curve over time from closed trades."""
        accounts = self.db.execute_query("SELECT capital FROM broker_accounts;")
        trades = self.db.execute_query("SELECT exit_time, entry_time, pnl FROM broker_trades ORDER BY exit_time ASC;")

        realized_pnl = sum(float(trade.get("pnl", 0.0)) for trade in trades)
        current_realized_equity = sum(float(account.get("capital", 0.0)) for account in accounts)
        base_capital = current_realized_equity - realized_pnl if accounts else 100.0
        running_capital = base_capital
        curve: list[dict[str, Any]] = []

        if trades:
            first_entry = trades[0]["entry_time"]
            curve.append({"time": _iso_utc(first_entry), "capital": round(base_capital, 2)})

        for t in trades:
            running_capital += t.get("pnl", 0.0)
            curve.append({"time": _iso_utc(t["exit_time"]), "capital": round(running_capital, 2)})

        return curve

    def get_recent_trades(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch completed trade audit log, ordered with newest trades first."""
        trades = self.db.execute_query("SELECT * FROM broker_trades ORDER BY exit_time DESC LIMIT ?;", (limit,))
        results: list[dict[str, Any]] = []
        for t in trades:
            item = dict(t)
            item["id"] = str(item.get("id"))
            results.append(item)
        return results

    def get_strategy_analytics(self) -> list[dict[str, Any]]:
        """Group closed trades by (strategy, symbol) and compute comprehensive risk/return metrics."""
        trades = self.db.execute_query("SELECT * FROM broker_trades;")
        accounts = self.db.execute_query("SELECT strategy_name, symbol, capital FROM broker_accounts;")
        accounts_map = {(a["strategy_name"], a["symbol"]): a["capital"] for a in accounts}

        grouped: dict[tuple, list[dict[str, Any]]] = {}
        for t in trades:
            key = (t["strategy_name"], t["symbol"])
            grouped.setdefault(key, []).append(t)

        for key in accounts_map:
            grouped.setdefault(key, [])

        results: list[dict[str, Any]] = []
        for (strategy_name, symbol), strat_trades in grouped.items():
            pnls = [t["pnl"] for t in strat_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            total_trades = len(strat_trades)
            total_pnl = sum(pnls)
            total_fees = sum(t.get("total_fees", 0.0) for t in strat_trades)
            gross_profit = sum(wins)
            gross_loss = abs(sum(losses))

            if gross_loss > 0:
                profit_factor: float | None = round(gross_profit / gross_loss, 2)
            elif gross_profit > 0:
                profit_factor = None
            else:
                profit_factor = 0.0

            long_trades = [t for t in strat_trades if t.get("type") == "LONG"]
            short_trades = [t for t in strat_trades if t.get("type") == "SHORT"]
            capital = accounts_map.get((strategy_name, symbol), 100.0)
            starting_capital = capital - total_pnl

            results.append(
                {
                    "strategy_name": strategy_name,
                    "symbol": symbol,
                    "current_capital": round(capital, 2),
                    "return_pct": round((total_pnl / starting_capital) * 100, 2) if starting_capital else 0.0,
                    "total_trades": total_trades,
                    "win_rate": round((len(wins) / total_trades * 100), 2) if total_trades else 0.0,
                    "total_pnl": round(total_pnl, 2),
                    "total_fees": round(total_fees, 2),
                    "profit_factor": profit_factor,
                    "best_trade": round(max(pnls), 2) if pnls else 0.0,
                    "worst_trade": round(min(pnls), 2) if pnls else 0.0,
                    "long_trades": len(long_trades),
                    "short_trades": len(short_trades),
                }
            )

        results.sort(key=lambda r: r["total_pnl"], reverse=True)
        return results

    def get_paper_dashboard(self) -> dict[str, Any]:
        """Build complete realized and mark-to-market paper trading analytics."""
        accounts = self.db.execute_query("SELECT strategy_name, symbol, capital, open_position FROM broker_accounts;")
        trades = self.db.execute_query("SELECT * FROM broker_trades ORDER BY exit_time ASC;")
        realized_pnl = sum(float(t.get("pnl", 0.0)) for t in trades)
        realized_equity = sum(float(account.get("capital", 0.0)) for account in accounts)
        starting_capital = realized_equity - realized_pnl if accounts else 100.0
        total_fees = sum(float(t.get("total_fees", 0.0)) for t in trades)
        wins = [float(t["pnl"]) for t in trades if float(t.get("pnl", 0.0)) > 0]
        losses = [float(t["pnl"]) for t in trades if float(t.get("pnl", 0.0)) <= 0]

        unrealized_pnl = 0.0
        open_positions = 0
        for account in accounts:
            raw_position = account.get("open_position")
            if not raw_position:
                continue
            position = json.loads(raw_position) if isinstance(raw_position, str) else raw_position
            if not position:
                continue
            open_positions += 1
            mark_price = get_live_price(account["symbol"]) or float(position["entry_price"])
            direction = 1 if position["type"] == "LONG" else -1
            gross = (mark_price - float(position["entry_price"])) * float(position["size"]) * direction
            exit_fee = float(position["size"]) * mark_price * 0.0005
            unrealized_pnl += gross - float(position.get("entry_fee", 0.0)) - exit_fee

        equity = starting_capital + realized_pnl + unrealized_pnl
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else (None if gross_profit else 0.0)

        curve = self.get_portfolio_equity_curve()
        equity_curve = [
            {"time": point["time"], "equity": point["capital"], "pnl": point["capital"] - starting_capital}
            for point in curve
        ]
        if not equity_curve:
            equity_curve.append({"time": None, "equity": starting_capital, "pnl": 0.0})
        if open_positions:
            equity_curve.append(
                {
                    "time": "Live",
                    "equity": round(equity, 2),
                    "pnl": round(realized_pnl + unrealized_pnl, 2),
                }
            )

        peak = equity_curve[0]["equity"]
        max_drawdown = 0.0
        for point in equity_curve:
            peak = max(peak, point["equity"])
            if peak:
                max_drawdown = max(max_drawdown, ((peak - point["equity"]) / peak) * 100)

        total_pnl = realized_pnl + unrealized_pnl
        return {
            "summary": {
                "starting_capital": round(starting_capital, 2),
                "equity": round(equity, 2),
                "total_pnl": round(total_pnl, 2),
                "pnl_pct": round((total_pnl / starting_capital) * 100, 2),
                "realized_pnl": round(realized_pnl, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "total_trades": len(trades),
                "open_positions": open_positions,
                "winning_trades": len(wins),
                "losing_trades": len(losses),
                "win_rate": round((len(wins) / len(trades)) * 100, 2) if trades else 0.0,
                "profit_factor": profit_factor,
                "average_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
                "average_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
                "best_trade": round(max(wins), 2) if wins else 0.0,
                "worst_trade": round(min(losses), 2) if losses else 0.0,
                "total_fees": round(total_fees, 2),
                "max_drawdown_pct": round(max_drawdown, 2),
            },
            "equity_curve": equity_curve,
            "strategies": self.get_strategy_analytics(),
            "trades": [dict(t) for t in reversed(trades[-100:])],
        }


_analytics_service: AnalyticsService | None = None


def get_analytics_service() -> AnalyticsService:
    """Singleton accessor for AnalyticsService."""
    global _analytics_service
    if _analytics_service is None:
        _analytics_service = AnalyticsService()
    return _analytics_service
