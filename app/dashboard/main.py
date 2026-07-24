import logging
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database.mongodb import MongoDBConnection
from app.core.settings import settings

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


@app.get("/api/schedule")
def get_batch_schedule() -> Dict[str, Any]:
    """Returns when the strategy batch last ran and its configured interval, so the UI can show a next-run countdown."""
    try:
        db = MongoDBConnection.get_database()
        doc = db.system_status.find_one({"_id": "batch_schedule"})

        last_triggered_at = doc.get("last_triggered_at") if doc else None
        interval_seconds = (doc.get("interval_seconds") if doc else None) or settings.schedule_seconds

        last_triggered_iso = None
        if hasattr(last_triggered_at, "isoformat"):
            # MongoDB returns naive datetimes; they were written as UTC, so mark them
            # explicitly or JS's `new Date()` will misinterpret them as local time.
            if last_triggered_at.tzinfo is None:
                last_triggered_at = last_triggered_at.replace(tzinfo=timezone.utc)
            last_triggered_iso = last_triggered_at.isoformat()

        return {
            "interval_seconds": interval_seconds,
            "last_triggered_at": last_triggered_iso,
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
                if hasattr(open_pos["entry_time"], "isoformat"):
                    open_pos["entry_time"] = open_pos["entry_time"].isoformat()
            
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
            results.append({
                "id": str(t.get("_id")),
                "strategy_name": t.get("strategy_name"),
                "symbol": t.get("symbol"),
                "type": t.get("type"),
                "entry_price": t.get("entry_price"),
                "exit_price": t.get("exit_price"),
                "size": round(t.get("size", 0.0), 6),
                "pnl": round(t.get("pnl", 0.0), 2),
                "return_pct": t.get("return_pct"),
                "entry_time": entry_time.isoformat() if hasattr(entry_time, "isoformat") else str(entry_time),
                "exit_time": exit_time.isoformat() if hasattr(exit_time, "isoformat") else str(exit_time),
                "reason": t.get("reason", "Signal Reverse")
            })
        return results
    except Exception as e:
        logger.error(f"Error fetching trade history: {e}", exc_info=True)
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
