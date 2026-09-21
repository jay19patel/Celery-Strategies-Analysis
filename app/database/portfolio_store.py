"""SQLite persistence for the Portfolio paper-trading engine.

Stores state in the `portfolio_state` table (single row with id='portfolio')
and completed trades in `portfolio_trades`. Replaces MongoDB with fast,
embedded SQLite storage.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from app.database.sqlite_db import get_sqlite_db

logger = logging.getLogger(__name__)

STATE_DOC_ID = "portfolio"


def _to_json_safe(value: Any) -> Any:
    """Recursively converts numpy scalars and non-standard types to JSON-safe primitives."""
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _serialize_position(p: dict[str, Any]) -> dict[str, Any]:
    """Ensures entry_time is ISO-formatted string."""
    item = dict(p)
    if "entry_time" in item:
        item["entry_time"] = item["entry_time"].isoformat() if hasattr(item["entry_time"], "isoformat") else str(item["entry_time"])
    return _to_json_safe(item)


def _deserialize_position(p: dict[str, Any]) -> dict[str, Any]:
    """Converts entry_time back to pandas Timestamp."""
    item = dict(p)
    if item.get("entry_time"):
        item["entry_time"] = pd.Timestamp(item["entry_time"])
    return item


def deserialize_open_positions(raw_positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deserializes position records so entry_time becomes pd.Timestamp."""
    return [_deserialize_position(p) for p in raw_positions]


def load_state() -> dict[str, Any]:
    """Loads the portfolio state from SQLite. Returns empty dict if no state is saved."""
    db = get_sqlite_db()
    row = db.execute_one(
        "SELECT balance, peak_equity, is_throttled, open_positions, pending_entries, last_candle_time, raw_state "
        "FROM portfolio_state WHERE id = ?;",
        (STATE_DOC_ID,),
    )
    if not row:
        return {}

    state: dict[str, Any] = {
        "balance": row["balance"],
        "peak_equity": row["peak_equity"],
        "is_throttled": bool(row["is_throttled"]),
        "open_positions": json.loads(row["open_positions"]) if row["open_positions"] else [],
        "pending_entries": json.loads(row["pending_entries"]) if row["pending_entries"] else [],
        "last_candle_time": row["last_candle_time"],
    }

    if row["raw_state"]:
        try:
            extra = json.loads(row["raw_state"])
            if isinstance(extra, dict):
                state.update(extra)
        except Exception:
            pass

    return state


def save_state(state: dict[str, Any]) -> None:
    """Saves the current portfolio state into SQLite."""
    db = get_sqlite_db()
    safe_state = _to_json_safe(dict(state))

    open_pos_json = json.dumps([_serialize_position(p) for p in state.get("open_positions", [])])
    pending_json = json.dumps(safe_state.get("pending_entries", []))
    balance = float(safe_state.get("balance", 100.0))
    peak_equity = float(safe_state.get("peak_equity", balance))
    is_throttled = 1 if safe_state.get("is_throttled", False) else 0
    last_candle = safe_state.get("last_candle_time")
    now_utc = datetime.now(UTC).isoformat()
    raw_state_json = json.dumps(safe_state)

    sql = """
        INSERT INTO portfolio_state (
            id, balance, peak_equity, is_throttled, open_positions,
            pending_entries, last_candle_time, raw_state, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            balance = excluded.balance,
            peak_equity = excluded.peak_equity,
            is_throttled = excluded.is_throttled,
            open_positions = excluded.open_positions,
            pending_entries = excluded.pending_entries,
            last_candle_time = excluded.last_candle_time,
            raw_state = excluded.raw_state,
            updated_at = excluded.updated_at;
    """
    db.execute_modify(
        sql,
        (
            STATE_DOC_ID,
            balance,
            peak_equity,
            is_throttled,
            open_pos_json,
            pending_json,
            last_candle,
            raw_state_json,
            now_utc,
        ),
    )
    logger.info(f"💾 Portfolio state saved to SQLite | balance={balance:.2f}")


def append_trades(trades: list[dict[str, Any]]) -> None:
    """Appends closed portfolio simulation trades to SQLite."""
    if not trades:
        return

    db = get_sqlite_db()
    now_utc = datetime.now(UTC).isoformat()

    sql = """
        INSERT INTO portfolio_trades (
            entry_time, exit_time, entry_price, exit_price, stop_price,
            target_price, size, leverage, direction, pnl, return_pct,
            exit_reason, raw_data, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    with db.transaction() as cursor:
        for t in trades:
            safe_t = _to_json_safe(dict(t))
            entry_time = str(safe_t.get("entry_time", now_utc))
            exit_time = str(safe_t.get("exit_time", now_utc))
            try:
                entry_price = float(safe_t.get("entry_price", 0.0) or 0.0)
            except (ValueError, TypeError):
                entry_price = 0.0

            try:
                exit_price = float(safe_t.get("exit_price", 0.0) or 0.0)
            except (ValueError, TypeError):
                exit_price = 0.0

            try:
                stop_price = float(safe_t.get("stop_price", 0.0) or 0.0)
            except (ValueError, TypeError):
                stop_price = 0.0

            try:
                target_price = float(safe_t.get("target_price", 0.0) or 0.0)
            except (ValueError, TypeError):
                target_price = 0.0

            try:
                size = float(safe_t.get("size", 0.0) or 0.0)
            except (ValueError, TypeError):
                size = 0.0

            try:
                leverage = float(safe_t.get("leverage", 1.0) or 1.0)
            except (ValueError, TypeError):
                leverage = 1.0

            raw_dir = safe_t.get("direction", 1)
            if isinstance(raw_dir, str):
                raw_dir_upper = raw_dir.strip().upper()
                if raw_dir_upper in ("SHORT", "SELL", "-1"):
                    direction = -1
                else:
                    direction = 1
            else:
                try:
                    direction = int(raw_dir)
                except (ValueError, TypeError):
                    direction = 1

            try:
                pnl = float(safe_t.get("pnl", 0.0) or 0.0)
            except (ValueError, TypeError):
                pnl = 0.0

            try:
                return_pct = float(safe_t.get("return_pct", 0.0) or 0.0)
            except (ValueError, TypeError):
                return_pct = 0.0

            reason = str(safe_t.get("exit_reason", safe_t.get("reason", "Exit")))
            raw_json = json.dumps(safe_t)

            cursor.execute(
                sql,
                (
                    entry_time,
                    exit_time,
                    entry_price,
                    exit_price,
                    stop_price,
                    target_price,
                    size,
                    leverage,
                    direction,
                    pnl,
                    return_pct,
                    reason,
                    raw_json,
                    now_utc,
                ),
            )

    logger.info(f"💾 {len(trades)} new Portfolio trade(s) saved to SQLite")


def get_trades(limit: int = 200, skip: int = 0) -> list[dict[str, Any]]:
    """Fetches closed portfolio trades sorted by exit_time descending."""
    db = get_sqlite_db()
    sql = "SELECT * FROM portfolio_trades ORDER BY exit_time DESC LIMIT ? OFFSET ?;"
    rows = db.execute_query(sql, (limit, skip))
    result: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        if item.get("raw_data"):
            try:
                raw = json.loads(item["raw_data"])
                if isinstance(raw, dict):
                    item.update(raw)
            except Exception:
                pass
        result.append(item)
    return result


def count_trades() -> int:
    """Returns the total number of closed portfolio trades."""
    db = get_sqlite_db()
    row = db.execute_one("SELECT COUNT(*) as cnt FROM portfolio_trades;")
    return row["cnt"] if row else 0
