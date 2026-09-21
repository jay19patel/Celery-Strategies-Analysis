"""System observability, health monitoring, and system maintenance service.

Provides host resource metrics (CPU, RAM, Disk, SQLite stats), Redis & Celery
health status, batch schedule information, and safe system reset operations.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.broker.execution_manager import get_execution_manager
from app.core.health_monitor import (
    _check_celery_workers,
    _check_redis,
    _check_sqlite,
    _check_system_resources,
    get_latest_health,
)
from app.core.settings import (
    get_pipeline_settings_override,
    get_schedule_seconds,
    get_strategies,
    get_strategies_raw,
    get_symbols,
    get_symbols_raw,
    settings,
)
from app.database.sqlite_db import get_sqlite_db

logger = logging.getLogger(__name__)

RESET_CONFIRMATION_PHRASE: str = "RESET SYSTEM"
LOGS_DIR: Path = Path(__file__).parent.parent.parent.resolve() / "logs"


class SystemService:
    """Service encapsulating system-level metrics, health status, and maintenance."""

    def __init__(self) -> None:
        """Initialize SQLite database access."""
        self.db = get_sqlite_db()
        self.logs_dir = LOGS_DIR
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def get_metrics(self) -> dict[str, Any]:
        """Collect real-time host resource metrics: CPU, RAM, Disk, SQLite, Redis, Celery."""
        resources = _check_system_resources()
        sqlite_stats = _check_sqlite()
        redis_stats = _check_redis()
        celery_stats = _check_celery_workers()

        return {
            "status": "success",
            "timestamp": datetime.now(UTC).isoformat(),
            "resources": resources,
            "sqlite": sqlite_stats,
            "redis": redis_stats,
            "celery": celery_stats,
        }

    def get_health(self) -> dict[str, Any]:
        """Fetch aggregated health check status across all subsystems."""
        return get_latest_health()

    def get_config(self) -> dict[str, Any]:
        """Fetch operational settings without exposing secrets or API keys."""
        mgr = get_execution_manager()
        strategies_clean = [s.split(".")[-1] for s in get_strategies()]

        return {
            "symbols": get_symbols(),
            "symbols_raw": get_symbols_raw(),
            "strategies": strategies_clean,
            "strategies_raw": get_strategies_raw(),
            "timezone": settings.timezone,
            "batch_schedule_seconds": get_schedule_seconds(),
            "schedule_seconds": get_schedule_seconds(),
            "execution_mode": mgr.get_mode(),
            "live_trading_armed": mgr.is_armed(),
            "delta_configured": mgr.delta_client.is_configured,
            "trade_capital_pct": settings.trade_capital_pct,
            "risk_ratio": settings.risk_ratio,
            "reward_ratio": settings.reward_ratio,
        }

    def get_batch_schedule(self) -> dict[str, Any]:
        """Fetch the most recent batch trigger time and configured interval."""
        row = self.db.execute_one("SELECT data FROM system_status WHERE id = ?;", ("batch_schedule",))
        curr_interval = get_schedule_seconds()
        if not row or not row.get("data"):
            return {
                "interval_seconds": curr_interval,
                "last_triggered_at": None,
            }

        try:
            doc = json.loads(row["data"])
            last_triggered = doc.get("last_triggered_at")
            return {
                "interval_seconds": curr_interval,
                "last_triggered_at": last_triggered,
            }
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(f"Could not parse batch_schedule record: {exc}")
            return {
                "interval_seconds": curr_interval,
                "last_triggered_at": None,
            }

    def update_pipeline_settings(
        self,
        symbols: str | None = None,
        strategies: str | None = None,
        schedule_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Update runtime pipeline settings (symbols, strategies, schedule_seconds) in SQLite.

        Args:
            symbols: Comma-separated symbols (e.g. 'BTC-USD,ETH-USD,SOL-USD').
            strategies: Comma-separated strategy names or '*' for all.
            schedule_seconds: Batch execution interval in seconds.

        Returns:
            Dictionary containing updated pipeline settings.
        """
        now_utc = datetime.now(UTC).isoformat()
        current = get_pipeline_settings_override()

        if symbols is not None:
            clean_symbols = ",".join([s.strip().upper() for s in symbols.split(",") if s.strip()])
            if clean_symbols:
                current["symbols"] = clean_symbols
                settings.symbols = clean_symbols

        if strategies is not None:
            clean_strat = ",".join([s.strip() for s in strategies.split(",") if s.strip()])
            if clean_strat:
                current["strategies"] = clean_strat
                settings.strategies = clean_strat

        if schedule_seconds is not None:
            clean_sec = max(10, int(schedule_seconds))
            current["schedule_seconds"] = clean_sec
            settings.schedule_seconds = clean_sec

        self.db.execute_modify(
            """
            INSERT INTO system_config (key, value, updated_at) VALUES ('pipeline_settings', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
            """,
            (json.dumps(current), now_utc),
        )

        logger.info("Updated pipeline settings in system_config: %s", current)
        return {
            "success": True,
            "message": "Pipeline settings updated successfully.",
            "settings": {
                "symbols": get_symbols(),
                "symbols_raw": get_symbols_raw(),
                "strategies": [s.split(".")[-1] for s in get_strategies()],
                "strategies_raw": get_strategies_raw(),
                "schedule_seconds": get_schedule_seconds(),
            },
        }

    def reset_system(self, confirmation: str) -> dict[str, Any]:
        """Safely reset all trading data tables and wipe log files upon valid confirmation."""
        if confirmation != RESET_CONFIRMATION_PHRASE:
            raise ValueError(f'Confirmation phrase must exactly match "{RESET_CONFIRMATION_PHRASE}"')

        tables: list[str] = [
            "broker_accounts",
            "broker_trades",
            "portfolio_state",
            "portfolio_trades",
            "signals_log",
            "system_status",
            "batch_results",
            "live_orders",
            "live_positions",
        ]
        cleared_counts: dict[str, int] = {}
        for tbl in tables:
            cnt_row = self.db.execute_one(f"SELECT COUNT(*) as c FROM {tbl};")  # Table name from fixed whitelist
            count = cnt_row["c"] if cnt_row else 0
            self.db.execute_modify(f"DELETE FROM {tbl};")
            cleared_counts[tbl] = count

        cleared_logs: list[str] = []
        for log_file in self.logs_dir.glob("*.log"):
            log_file.write_text("")
            cleared_logs.append(log_file.name)

        logger.warning("🔴 SYSTEM RESET performed | tables cleared: %s | logs: %s", cleared_counts, cleared_logs)
        return {"ok": True, "cleared_tables": cleared_counts, "cleared_logs": cleared_logs}

    def update_trading_config(
        self,
        trade_capital_pct: float | None = None,
        risk_ratio: float | None = None,
        reward_ratio: float | None = None,
    ) -> dict[str, Any]:
        """Update trading parameters in SQLite system_config and active settings."""
        now_utc = datetime.now(UTC).isoformat()
        row = self.db.execute_one("SELECT value FROM system_config WHERE key = 'trading_params';")
        current: dict[str, Any] = json.loads(row["value"]) if row and row.get("value") else {}

        if trade_capital_pct is not None:
            clean_cap = max(1.0, min(100.0, float(trade_capital_pct)))
            current["trade_capital_pct"] = clean_cap
            settings.trade_capital_pct = clean_cap

        if risk_ratio is not None:
            clean_risk = max(0.001, min(0.2, float(risk_ratio)))
            current["risk_ratio"] = clean_risk
            settings.risk_ratio = clean_risk

        if reward_ratio is not None:
            clean_reward = max(0.001, min(0.5, float(reward_ratio)))
            current["reward_ratio"] = clean_reward
            settings.reward_ratio = clean_reward

        self.db.execute_modify(
            """
            INSERT INTO system_config (key, value, updated_at) VALUES ('trading_params', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
            """,
            (json.dumps(current), now_utc),
        )
        logger.info("Updated trading parameters: %s", current)
        return {
            "success": True,
            "message": "Trading parameters updated successfully.",
            "params": current,
        }

    def reset_paper_balances(self, starting_capital: float = 100.0) -> dict[str, Any]:
        """Reset paper trading accounts back to starting capital ($100 default) and clear paper positions."""
        clean_capital = max(10.0, float(starting_capital))
        self.db.execute_modify(
            """
            UPDATE broker_accounts 
            SET capital = ?, open_position = NULL, total_trades = 0, winning_trades = 0, win_rate = 0.0;
            """,
            (clean_capital,),
        )
        cnt_row = self.db.execute_one("SELECT COUNT(*) as c FROM broker_trades;")
        trades_count = cnt_row["c"] if cnt_row else 0
        self.db.execute_modify("DELETE FROM broker_trades;")

        logger.info(
            "Paper accounts reset to $%.2f | Cleared %d closed trades",
            clean_capital,
            trades_count,
        )
        return {
            "success": True,
            "starting_capital": clean_capital,
            "cleared_trades": trades_count,
            "message": f"Paper trading accounts reset to ${clean_capital:.2f} with clean trade history.",
        }


_system_service: SystemService | None = None


def get_system_service() -> SystemService:
    """Singleton accessor for SystemService."""
    global _system_service
    if _system_service is None:
        _system_service = SystemService()
    return _system_service
