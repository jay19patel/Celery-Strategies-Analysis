"""Strategy management and virtual accounts overview service.

Aggregates strategy performance, virtual account balances, open positions,
and trading calendar status from SQLite and the Strategy Registry.
"""

import json
import logging
from typing import Any

from app.database.sqlite_db import get_sqlite_db
from app.utility.trading_calendar import TradingCalendar

logger = logging.getLogger(__name__)


class StrategyService:
    """Service encapsulating strategy accounts, global metrics, and trading calendar."""

    def __init__(self) -> None:
        """Initialize SQLite database access and trading calendar."""
        self.db = get_sqlite_db()
        self.calendar = TradingCalendar()

    def get_global_stats(self) -> dict[str, Any]:
        """Compile system-wide performance metrics across all strategy accounts."""
        accounts = self.db.execute_query("SELECT capital, total_trades, winning_trades FROM broker_accounts;")

        if not accounts:
            return {
                "total_capital": 0.0,
                "total_profit_pct": 0.0,
                "active_strategies": 0,
                "total_trades": 0,
                "global_win_rate": 0.0,
            }

        total_capital = sum(float(acc["capital"]) for acc in accounts)
        total_trades = sum(int(acc["total_trades"]) for acc in accounts)
        total_wins = sum(int(acc["winning_trades"]) for acc in accounts)
        global_win_rate = (total_wins / total_trades * 100.0) if total_trades > 0 else 0.0
        num_accounts = len(accounts)
        base_capital = num_accounts * 100.0
        total_profit_pct = ((total_capital - base_capital) / base_capital * 100.0) if base_capital > 0 else 0.0

        return {
            "total_capital": round(total_capital, 2),
            "total_profit_pct": round(total_profit_pct, 2),
            "active_strategies": num_accounts,
            "total_trades": total_trades,
            "global_win_rate": round(global_win_rate, 2),
        }

    def get_strategies_stats(self) -> list[dict[str, Any]]:
        """Retrieve performance stats and active open positions for each strategy account."""
        accounts = self.db.execute_query("SELECT * FROM broker_accounts ORDER BY capital DESC;")
        results: list[dict[str, Any]] = []

        for acc in accounts:
            cap = float(acc["capital"])
            ret_pct = ((cap - 100.0) / 100.0) * 100.0

            open_pos = None
            raw_pos = acc.get("open_position")
            if raw_pos:
                try:
                    open_pos = json.loads(raw_pos) if isinstance(raw_pos, str) else raw_pos
                except (json.JSONDecodeError, TypeError):
                    open_pos = None

            results.append({
                "strategy_name": acc["strategy_name"],
                "symbol": acc["symbol"],
                "capital": round(cap, 2),
                "return_pct": round(ret_pct, 2),
                "total_trades": int(acc["total_trades"]),
                "win_rate": round(float(acc["win_rate"]), 2),
                "open_position": open_pos,
            })

        if not results:
            # Provide initial registered strategy matrix if no trades recorded yet
            default_symbols = ["BTC-USD", "ETH-USD", "SOL-USD"]
            default_strats = ["CombinedPortfolioStrategy", "MotherCandleStrategy"]
            for s_name in default_strats:
                for sym in default_symbols:
                    results.append({
                        "strategy_name": s_name,
                        "symbol": sym,
                        "capital": 100.0,
                        "return_pct": 0.0,
                        "total_trades": 0,
                        "win_rate": 0.0,
                        "open_position": None,
                    })

        return results

    def get_signals_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve recent algorithmic strategy signals with execution routing status."""
        rows = self.db.execute_query(
            "SELECT id, strategy_name, symbol, signal_type, price, timestamp, execution_time, "
            "COALESCE(mode, 'PAPER') AS mode, COALESCE(action, 'paper_executed') AS action, created_at "
            "FROM signals_log ORDER BY id DESC LIMIT ?;",
            (limit,),
        )
        return [
            {
                "id": r["id"],
                "strategy_name": r["strategy_name"],
                "symbol": r["symbol"],
                "signal_type": r["signal_type"],
                "price": float(r["price"]),
                "timestamp": r["timestamp"],
                "execution_time": float(r["execution_time"]),
                "mode": r["mode"],
                "action": r["action"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def trigger_signal(
        self,
        strategy_name: str,
        symbol: str,
        signal_type: str,
        price: float,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        """Trigger an algorithmic signal through the live or paper execution pipeline."""
        from datetime import UTC, datetime
        from app.broker.execution_manager import get_execution_manager
        from app.models.strategy_models import SignalType

        sig_enum = SignalType(signal_type.strip().upper())
        now = datetime.now(UTC)
        exec_mgr = get_execution_manager()

        exec_res = exec_mgr.process_signal(
            strategy_name=strategy_name,
            symbol=symbol,
            signal_type=sig_enum,
            price=price,
            timestamp=now,
            confidence=confidence,
        )

        mode = exec_res.get("mode", exec_mgr.get_mode())
        action = exec_res.get("action", "recorded")

        now_str = now.isoformat()
        sql = """
            INSERT INTO signals_log (strategy_name, symbol, signal_type, price, timestamp, execution_time, subscribers_received, mode, action, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        self.db.execute_modify(
            sql,
            (strategy_name, symbol, sig_enum.value, price, now_str, 0.02, 1, mode, action, now_str),
        )

        return {
            "status": "success",
            "signal": sig_enum.value,
            "strategy_name": strategy_name,
            "symbol": symbol,
            "price": price,
            "mode": mode,
            "action": action,
            "details": exec_res,
        }

    def get_strategies_detailed(self) -> list[dict[str, Any]]:
        """Retrieve detailed strategy metadata, timeframes, supported symbols, signal & order counts, and toggles."""
        configs = self.db.execute_query("SELECT * FROM strategy_configs ORDER BY strategy_id ASC;")
        if not configs:
            self.db.init_schema()
            configs = self.db.execute_query("SELECT * FROM strategy_configs ORDER BY strategy_id ASC;")

        results: list[dict[str, Any]] = []
        for cfg in configs:
            s_id = cfg["strategy_id"]
            name = cfg["name"]

            # Count total signals produced by this strategy
            sig_row = self.db.execute_one(
                "SELECT COUNT(*) as cnt FROM signals_log WHERE strategy_name = ? OR strategy_name = ?;",
                (s_id, name),
            )
            total_signals = sig_row["cnt"] if sig_row else 0

            # Count paper orders created (trades in broker_trades)
            paper_row = self.db.execute_one(
                "SELECT COUNT(*) as cnt FROM broker_trades WHERE strategy_name = ? OR strategy_name = ?;",
                (s_id, name),
            )
            paper_orders_count = paper_row["cnt"] if paper_row else 0

            # Count real orders created (if live_orders table has records)
            real_row = self.db.execute_one(
                "SELECT COUNT(*) as cnt FROM live_orders WHERE raw_data LIKE ?;",
                (f"%{s_id}%",),
            )
            real_orders_count = real_row["cnt"] if real_row else 0

            # Performance stats from broker_accounts
            acc_row = self.db.execute_one(
                "SELECT capital, total_trades, winning_trades, win_rate, open_position FROM broker_accounts WHERE strategy_name = ? OR strategy_name = ? LIMIT 1;",
                (s_id, name),
            )
            capital = float(acc_row["capital"]) if acc_row else 100.0
            win_rate = float(acc_row["win_rate"]) if acc_row else 0.0
            open_pos = None
            if acc_row and acc_row.get("open_position"):
                try:
                    open_pos = json.loads(acc_row["open_position"]) if isinstance(acc_row["open_position"], str) else acc_row["open_position"]
                except Exception:
                    open_pos = None

            # Symbols list
            raw_syms = cfg.get("symbols", "BTC-USD,ETH-USD,SOL-USD")
            symbols_list = [s.strip() for s in raw_syms.split(",") if s.strip()]

            results.append({
                "strategy_id": s_id,
                "name": name,
                "timeframe": cfg.get("timeframe", "1h"),
                "symbols": symbols_list,
                "is_paper_enabled": bool(cfg.get("is_paper_enabled", 1)),
                "is_real_enabled": bool(cfg.get("is_real_enabled", 0)),
                "total_signals": total_signals,
                "paper_orders_count": paper_orders_count,
                "real_orders_count": real_orders_count,
                "capital": round(capital, 2),
                "win_rate": round(win_rate, 2),
                "return_pct": round(((capital - 100.0) / 100.0) * 100.0, 2),
                "open_position": open_pos,
                "updated_at": cfg.get("updated_at", ""),
            })

        return results

    def toggle_strategy_execution(
        self,
        strategy_id: str,
        is_paper_enabled: bool | None = None,
        is_real_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Update paper/real execution toggles for a specific strategy."""
        from datetime import UTC, datetime

        cfg = self.db.execute_one(
            "SELECT * FROM strategy_configs WHERE strategy_id = ?;",
            (strategy_id,),
        )
        if not cfg:
            raise ValueError(f"Strategy '{strategy_id}' not found in configuration.")

        paper_val = int(is_paper_enabled) if is_paper_enabled is not None else cfg["is_paper_enabled"]
        real_val = int(is_real_enabled) if is_real_enabled is not None else cfg["is_real_enabled"]
        now_str = datetime.now(UTC).isoformat()

        sql = """
            UPDATE strategy_configs
            SET is_paper_enabled = ?, is_real_enabled = ?, updated_at = ?
            WHERE strategy_id = ?;
        """
        self.db.execute_modify(sql, (paper_val, real_val, now_str, strategy_id))

        return {
            "strategy_id": strategy_id,
            "is_paper_enabled": bool(paper_val),
            "is_real_enabled": bool(real_val),
            "status": "updated",
        }

    def get_calendar(self) -> dict[str, Any]:
        """Fetch market calendar information and current exchange status."""
        return self.calendar.describe()


_strategy_service: StrategyService | None = None


def get_strategy_service() -> StrategyService:
    """Singleton accessor for StrategyService."""
    global _strategy_service
    if _strategy_service is None:
        _strategy_service = StrategyService()
    return _strategy_service
