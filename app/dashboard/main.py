import logging
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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


RESET_CONFIRMATION_PHRASE = "RESET SYSTEM"

# Every MongoDB collection this app writes trading data to (see app/database/mongodb.py,
# app/database/portfolio_store.py, app/core/tasks.py, app/dashboard/main.py's own
# system_status write) - kept as one explicit list so a reset can never silently miss one.
RESET_COLLECTIONS = [
    "broker_accounts",
    "broker_trades",
    "portfolio_state",
    "portfolio_trades",
    "signals_log",
    "system_status",
    "batch_results",
]


class ResetRequest(BaseModel):
    confirmation: str


@app.post("/api/system/reset")
def reset_system(payload: ResetRequest) -> Dict[str, Any]:
    """Wipes all trading data (every MongoDB collection above) and clears every log
    file, resetting the system to a fresh state. Destructive and irreversible - guarded
    by requiring the exact confirmation phrase, checked server-side (not just in the UI)
    so a stray/scripted request can't trigger it by accident."""
    if payload.confirmation != RESET_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation text must exactly match "{RESET_CONFIRMATION_PHRASE}"'
        )

    try:
        db = MongoDBConnection.get_database()
        cleared_collections = {}
        for name in RESET_COLLECTIONS:
            result = db[name].delete_many({})
            cleared_collections[name] = result.deleted_count

        cleared_logs = []
        for log_file in _get_logs_path().glob("*.log"):
            log_file.write_text("")
            cleared_logs.append(log_file.name)

        logger.warning(
            f"🔴 SYSTEM RESET performed | collections cleared: {cleared_collections} | "
            f"logs cleared: {cleared_logs}"
        )

        return {
            "ok": True,
            "cleared_collections": cleared_collections,
            "cleared_logs": cleared_logs,
        }
    except Exception as e:
        logger.error(f"Error performing system reset: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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


@app.get("/api/portfolio/equity-curve")
def get_portfolio_equity_curve() -> List[Dict[str, Any]]:
    """Builds the combined portfolio equity curve (every strategy's capital summed
    together) over real calendar time, by replaying every closed trade in
    chronological order on top of each strategy's $100 starting capital."""
    try:
        db = MongoDBConnection.get_database()
        accounts = list(db.broker_accounts.find())
        trades = list(db.broker_trades.find().sort("exit_time", 1))

        base_capital = len(accounts) * 100.0
        running_capital = base_capital
        curve = []

        if trades:
            first_entry_time = trades[0].get("entry_time")
            curve.append({"time": _iso_utc(first_entry_time) or str(first_entry_time), "capital": round(base_capital, 2)})

        for t in trades:
            running_capital += t.get("pnl", 0.0)
            exit_time = t.get("exit_time")
            curve.append({"time": _iso_utc(exit_time) or str(exit_time), "capital": round(running_capital, 2)})

        return curve
    except Exception as e:
        logger.error(f"Error computing portfolio equity curve: {e}", exc_info=True)
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
            "default_leverage": getattr(settings, "broker_leverage", 20.0),
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
            if open_pos:
                if "entry_time" in open_pos:
                    open_pos["entry_time"] = _iso_utc(open_pos["entry_time"]) or open_pos["entry_time"]
                if open_pos.get("last_price_time"):
                    open_pos["last_price_time"] = _iso_utc(open_pos["last_price_time"]) or open_pos["last_price_time"]

            results.append({
                "strategy_name": acc.get("strategy_name", acc.get("_id")),
                "symbol": acc.get("symbol"),
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
                "leverage": t.get("leverage", getattr(settings, "broker_leverage", 20.0)),
                "capital_allocated": t.get("capital_allocated"),
                "gross_pnl": round(t.get("gross_pnl", t.get("pnl", 0.0)), 2),
                "entry_fee": round(t.get("entry_fee", 0.0), 4),
                "exit_fee": round(t.get("exit_fee", 0.0), 4),
                "total_fees": round(t.get("total_fees", 0.0), 4),
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
    """Groups closed-trade history by (strategy, symbol) - matching how broker_accounts
    are keyed, since each strategy runs an independent account per symbol - and computes
    performance analytics (win rate, profit factor, avg win/loss, long/short split, etc.)
    so each strategy's behavior on each symbol can be compared at a glance."""
    try:
        db = MongoDBConnection.get_database()
        trades = list(db.broker_trades.find())
        accounts = {(acc.get("strategy_name"), acc.get("symbol")): acc for acc in db.broker_accounts.find()}

        grouped: Dict[Any, List[Dict[str, Any]]] = {}
        for t in trades:
            grouped.setdefault((t.get("strategy_name"), t.get("symbol")), []).append(t)

        # Include accounts that have no closed trades yet
        for account_key in accounts:
            grouped.setdefault(account_key, [])

        results = []
        for (strategy_name, symbol), strat_trades in grouped.items():
            pnls = [t.get("pnl", 0.0) for t in strat_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            total_trades = len(strat_trades)
            total_pnl = sum(pnls)
            total_fees = sum(t.get("total_fees", 0.0) for t in strat_trades)
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

            account = accounts.get((strategy_name, symbol), {})
            capital = account.get("capital", 100.0)

            results.append({
                "strategy_name": strategy_name,
                "symbol": symbol,
                "current_capital": round(capital, 2),
                "return_pct": round(((capital - 100.0) / 100.0) * 100, 2),
                "total_trades": total_trades,
                "win_rate": round((len(wins) / total_trades * 100), 2) if total_trades else 0.0,
                "total_pnl": round(total_pnl, 2),
                "total_fees": round(total_fees, 2),
                "avg_pnl_per_trade": round(total_pnl / total_trades, 2) if total_trades else 0.0,
                "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
                "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
                "profit_factor": profit_factor,
                "best_trade": round(max(pnls), 2) if pnls else 0.0,
                "worst_trade": round(min(pnls), 2) if pnls else 0.0,
                "long_trades": len(long_trades),
                "short_trades": len(short_trades),
                "avg_hold_minutes": round((sum(durations) / len(durations)) / 60, 1) if durations else None,
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


@app.get("/api/logs/{log_type}/download")
def download_file_logs(log_type: str) -> FileResponse:
    """Downloads the full raw log file (success, errors, signals, or performance)."""
    if log_type not in ["success", "errors", "signals", "performance"]:
        raise HTTPException(status_code=400, detail="Invalid log type requested.")

    log_file = _get_logs_path() / f"{log_type}.log"
    if not log_file.exists():
        raise HTTPException(status_code=404, detail=f"Log file {log_type}.log does not exist yet.")

    return FileResponse(
        str(log_file),
        media_type="text/plain",
        filename=f"{log_type}.log",
    )
