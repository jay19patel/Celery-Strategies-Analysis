import logging
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database.mongodb import MongoDBConnection
from app.core.settings import settings, get_symbols, get_strategies

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Stock Analysis Dashboard API",
    description="Backend API for real-time strategy monitoring and paper trading logs.",
    version="1.0.0"
)

# Define static directories
CURRENT_DIR = Path(__file__).parent.resolve()
STATIC_DIR = CURRENT_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _get_logs_path() -> Path:
    """Returns the absolute path to the logs directory."""
    return Path(__file__).parent.parent.parent / "logs"


def _iso_utc(dt: Any) -> Optional[str]:
    """Serializes a MongoDB-stored datetime as ISO-8601 with an explicit UTC offset.

    MongoDB returns naive datetimes (though they were written as UTC) - without an
    explicit offset, JS's `new Date()` would misinterpret them as local time.
    """
    if not hasattr(dt, "isoformat"):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


@app.get("/")
def get_dashboard() -> FileResponse:
    """Serves the main dashboard HTML interface."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Dashboard index.html not found.")
    return FileResponse(str(index_file))


@app.get("/api/stats")
def get_global_stats() -> Dict[str, Any]:
    """Calculates and returns system-wide performance indicators."""
    try:
        db = MongoDBConnection.get_database()
        accounts = list(db.broker_accounts.find())
        
        total_capital = sum(acc.get("capital", 100.0) for acc in accounts) if accounts else 0.0
        total_trades = sum(acc.get("total_trades", 0) for acc in accounts) if accounts else 0
        total_wins = sum(acc.get("winning_trades", 0) for acc in accounts) if accounts else 0
        global_win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0.0
        
        return {
            "total_capital": round(total_capital, 2),
            "total_profit_pct": round(((total_capital - (len(accounts) * 100.0)) / (len(accounts) * 100.0) * 100), 2) if accounts else 0.0,
            "active_strategies": len(accounts),
            "total_trades": total_trades,
            "global_win_rate": round(global_win_rate, 2)
        }
    except Exception as e:
        logger.error(f"Error compiling global stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
def get_system_config() -> Dict[str, Any]:
    """Returns the active runtime configuration (strategies, symbols, schedules, risk
    parameters) so the dashboard can show what the system is actually set up to do.
    Deliberately excludes connection strings/credentials - operational settings only.
    """
    return {
        "symbols": get_symbols(),
        "strategies": [path.split(".")[-1] for path in get_strategies()],
        "timezone": settings.timezone,
        "batch_schedule_seconds": settings.schedule_seconds,
        "paper_broker": {
            "stop_loss_pct": settings.broker_stop_loss_pct,
            "take_profit_pct": settings.broker_take_profit_pct,
        },
        "portfolio_engine": {
            "symbol": settings.portfolio_symbol,
            "interval": settings.portfolio_interval,
            "schedule_seconds": settings.portfolio_schedule_seconds,
            "initial_capital": settings.portfolio_initial_capital,
            "risk_per_trade_pct": settings.portfolio_risk_per_trade_pct,
            "stop_loss_pct": settings.portfolio_stop_loss_pct,
            "take_profit_pct": settings.portfolio_take_profit_pct,
            "max_hold_bars": settings.portfolio_max_hold_bars,
            "max_leverage": settings.portfolio_max_leverage,
            "max_concurrent_trades": settings.portfolio_max_concurrent_trades,
        },
    }


@app.get("/api/schedule")
def get_batch_schedule() -> Dict[str, Any]:
    """Returns when the strategy batch last ran and its configured interval, so the UI can show a next-run countdown."""
    try:
        db = MongoDBConnection.get_database()
        doc = db.system_status.find_one({"_id": "batch_schedule"})

        last_triggered_at = doc.get("last_triggered_at") if doc else None
        interval_seconds = (doc.get("interval_seconds") if doc else None) or settings.schedule_seconds

        return {
            "interval_seconds": interval_seconds,
            "last_triggered_at": _iso_utc(last_triggered_at),
        }
    except Exception as e:
        logger.error(f"Error fetching batch schedule status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/strategies")
def get_strategies_stats() -> List[Dict[str, Any]]:
    """Retrieves performance data for each trading strategy, ordered by capital desc."""
    try:
        db = MongoDBConnection.get_database()
        accounts = list(db.broker_accounts.find().sort("capital", -1))
        
        results = []
        for acc in accounts:
            ret_pct = ((acc.get("capital", 100.0) - 100.0) / 100.0) * 100.0
            
            # Format open position for serializability
            open_pos = acc.get("open_position")
            if open_pos and "entry_time" in open_pos:
                open_pos["entry_time"] = _iso_utc(open_pos["entry_time"]) or open_pos["entry_time"]
            
            results.append({
                "strategy_name": acc.get("_id"),
                "capital": round(acc.get("capital", 100.0), 2),
                "return_pct": round(ret_pct, 2),
                "total_trades": acc.get("total_trades", 0),
                "win_rate": round(acc.get("win_rate", 0.0), 2),
                "open_position": open_pos
            })
        return results
    except Exception as e:
        logger.error(f"Error fetching strategy stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trades")
def get_recent_trades(limit: int = 100) -> List[Dict[str, Any]]:
    """Retrieves the history of completed (closed) trades, newest first."""
    try:
        db = MongoDBConnection.get_database()
        trades = list(db.broker_trades.find().sort("exit_time", -1).limit(limit))
        
        results = []
        for t in trades:
            entry_time = t.get("entry_time")
            exit_time = t.get("exit_time")

            holding_minutes = None
            if hasattr(entry_time, "timestamp") and hasattr(exit_time, "timestamp"):
                holding_minutes = round((exit_time - entry_time).total_seconds() / 60, 1)

            results.append({
                "id": str(t.get("_id")),
                "strategy_name": t.get("strategy_name"),
                "symbol": t.get("symbol"),
                "type": t.get("type"),
                "entry_price": t.get("entry_price"),
                "exit_price": t.get("exit_price"),
                "stop_price": t.get("stop_price"),
                "target_price": t.get("target_price"),
                "size": round(t.get("size", 0.0), 6),
                "pnl": round(t.get("pnl", 0.0), 2),
                "return_pct": t.get("return_pct"),
                "entry_time": _iso_utc(entry_time) or str(entry_time),
                "exit_time": _iso_utc(exit_time) or str(exit_time),
                "holding_minutes": holding_minutes,
                "reason": t.get("reason", "Signal Reverse")
            })
        return results
    except Exception as e:
        logger.error(f"Error fetching trade history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics")
def get_strategy_analytics() -> List[Dict[str, Any]]:
    """Groups closed-trade history by strategy and computes performance analytics
    (win rate, profit factor, avg win/loss, long/short split, etc.) so each
    strategy's behavior can be compared at a glance."""
    try:
        db = MongoDBConnection.get_database()
        trades = list(db.broker_trades.find())
        accounts = {acc["_id"]: acc for acc in db.broker_accounts.find()}

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for t in trades:
            grouped.setdefault(t.get("strategy_name"), []).append(t)

        # Include strategies that have an account but no closed trades yet
        for strategy_name in accounts:
            grouped.setdefault(strategy_name, [])

        results = []
        for strategy_name, strat_trades in grouped.items():
            pnls = [t.get("pnl", 0.0) for t in strat_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            total_trades = len(strat_trades)
            total_pnl = sum(pnls)
            gross_profit = sum(wins)
            gross_loss = abs(sum(losses))

            if gross_loss > 0:
                profit_factor = round(gross_profit / gross_loss, 2)
            elif gross_profit > 0:
                profit_factor = None  # no losing trades yet - undefined/infinite
            else:
                profit_factor = 0.0

            long_trades = [t for t in strat_trades if t.get("type") == "LONG"]
            short_trades = [t for t in strat_trades if t.get("type") == "SHORT"]

            durations = []
            for t in strat_trades:
                entry_time, exit_time = t.get("entry_time"), t.get("exit_time")
                if hasattr(entry_time, "timestamp") and hasattr(exit_time, "timestamp"):
                    durations.append((exit_time - entry_time).total_seconds())

            reason_breakdown: Dict[str, int] = {}
            for t in strat_trades:
                reason = t.get("reason", "Unknown")
                reason_breakdown[reason] = reason_breakdown.get(reason, 0) + 1

            symbols_traded = sorted({t.get("symbol") for t in strat_trades if t.get("symbol")})

            account = accounts.get(strategy_name, {})
            capital = account.get("capital", 100.0)

            results.append({
                "strategy_name": strategy_name,
                "current_capital": round(capital, 2),
                "return_pct": round(((capital - 100.0) / 100.0) * 100, 2),
                "total_trades": total_trades,
                "win_rate": round((len(wins) / total_trades * 100), 2) if total_trades else 0.0,
                "total_pnl": round(total_pnl, 2),
                "avg_pnl_per_trade": round(total_pnl / total_trades, 2) if total_trades else 0.0,
                "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
                "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
                "profit_factor": profit_factor,
                "best_trade": round(max(pnls), 2) if pnls else 0.0,
                "worst_trade": round(min(pnls), 2) if pnls else 0.0,
                "long_trades": len(long_trades),
                "short_trades": len(short_trades),
                "avg_hold_minutes": round((sum(durations) / len(durations)) / 60, 1) if durations else None,
                "symbols_traded": symbols_traded,
                "exit_reasons": reason_breakdown,
            })

        results.sort(key=lambda r: r["total_pnl"], reverse=True)
        return results
    except Exception as e:
        logger.error(f"Error compiling strategy analytics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/logs/{log_type}")
def get_file_logs(log_type: str, lines_count: int = 200) -> List[str]:
    """Reads and returns the last N lines of a specific system log file."""
    if log_type not in ["success", "errors", "signals", "performance"]:
        raise HTTPException(status_code=400, detail="Invalid log type requested.")
        
    try:
        log_file = _get_logs_path() / f"{log_type}.log"
        if not log_file.exists():
            return [f"Log file {log_type}.log does not exist yet. It will be generated when tasks run."]
            
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            
        # Return only the last lines_count lines
        return [line.strip() for line in lines[-lines_count:]]
    except Exception as e:
        logger.error(f"Error reading log file {log_type}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
