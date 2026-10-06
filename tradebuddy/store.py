"""SQLite: settings, toggles, the order book, the paper account, and a log of every event."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS toggles (
    key     TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    ts              REAL NOT NULL,
    broker          TEXT NOT NULL DEFAULT 'delta',
    strategy        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    size            INTEGER NOT NULL,
    price           REAL NOT NULL,
    stop_loss       REAL,
    take_profit     REAL,
    status          TEXT NOT NULL,
    order_id        TEXT,
    error           TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   REAL NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_account (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    balance         REAL NOT NULL,
    starting_balance REAL NOT NULL,
    realized_pnl    REAL NOT NULL DEFAULT 0,
    fees_paid       REAL NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_positions (
    symbol            TEXT PRIMARY KEY,
    client_order_id   TEXT NOT NULL UNIQUE,
    strategy          TEXT NOT NULL,
    side              TEXT NOT NULL,
    size              REAL NOT NULL,
    contract_value    REAL NOT NULL,
    entry_price       REAL NOT NULL,
    leverage          REAL NOT NULL,
    margin            REAL NOT NULL,
    entry_fee         REAL NOT NULL,
    stop_loss         REAL,
    take_profit       REAL,
    liquidation_price REAL NOT NULL,
    opened_at         REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL UNIQUE,
    strategy        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    size            REAL NOT NULL,
    contract_value  REAL NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL NOT NULL,
    leverage        REAL NOT NULL,
    margin          REAL NOT NULL,
    gross_pnl       REAL NOT NULL,
    fees            REAL NOT NULL,
    pnl             REAL NOT NULL,
    reason          TEXT NOT NULL,
    opened_at       REAL NOT NULL,
    closed_at       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS position_controls (
    broker      TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    position    TEXT NOT NULL,              -- side:entry, so a new position on the symbol starts fresh
    trailing    INTEGER NOT NULL,
    max_steps   INTEGER NOT NULL,
    steps       INTEGER NOT NULL DEFAULT 0,
    base_target REAL NOT NULL DEFAULT 0,    -- entry -> first target distance; each trail extends by a share of it
    updated_at  REAL NOT NULL,
    PRIMARY KEY (broker, symbol)
);
CREATE TABLE IF NOT EXISTS daily_risk (
    broker        TEXT NOT NULL,
    day           TEXT NOT NULL,
    start_equity  REAL NOT NULL,
    halted_at     REAL,
    halt_reason   TEXT,
    PRIMARY KEY (broker, day)
);
CREATE INDEX IF NOT EXISTS events_type ON events(type, id);
CREATE INDEX IF NOT EXISTS events_type_ts ON events(type, ts);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
"""

# How long the event log keeps each type. The orders, paper_trades and daily_risk tables are the
# permanent record; the log is for looking back. Market and plumbing events go first.
RETENTION_DAYS = {
    "CandleClosed": 2,
    "FeedStatus": 14,
    "OrderRequested": 14,
    "OrderUpdate": 14,
    "PositionUpdate": 14,
}
DEFAULT_RETENTION_DAYS = 90  # signals, skips, errors, orders, closed positions, settings changes

# Orders that may still become a position. While one exists, the symbol is blocked.
ACTIVE_STATUSES = ("pending", "unknown", "open")


class Store:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        # Databases created before orders were tagged with a broker.
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(orders)")}
        if "broker" not in columns:
            self.db.execute("ALTER TABLE orders ADD COLUMN broker TEXT NOT NULL DEFAULT 'delta'")
        self.db.execute("CREATE INDEX IF NOT EXISTS orders_symbol_status ON orders(broker, symbol, status)")

    # -- settings -----------------------------------------------------------

    def load_settings(self) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self.db.execute("SELECT key, value FROM settings")}

    def save_settings(self, values: dict[str, Any]) -> None:
        with self.db:
            for key, value in values.items():
                self.db.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )

    # -- toggles ------------------------------------------------------------

    def enabled(self, key: str, default: bool = True) -> bool:
        row = self.db.execute("SELECT enabled FROM toggles WHERE key = ?", (key,)).fetchone()
        return default if row is None else bool(row["enabled"])

    def set_enabled(self, key: str, enabled: bool) -> None:
        self.db.execute(
            "INSERT INTO toggles (key, enabled) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET enabled = excluded.enabled",
            (key, int(enabled)),
        )

    # -- orders -------------------------------------------------------------

    def reserve_order(self, **order: Any) -> bool:
        """Record an order as pending before it is sent. False if this client_order_id already exists."""
        try:
            self.db.execute(
                "INSERT INTO orders (client_order_id, ts, broker, strategy, symbol, side, size, price, stop_loss, take_profit, status)"
                " VALUES (:client_order_id, :ts, :broker, :strategy, :symbol, :side, :size, :price, :stop_loss, :take_profit, 'pending')",
                {"ts": time.time(), **order},
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def update_order(self, client_order_id: str, status: str, order_id: str | None = None, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE orders SET status = ?, order_id = COALESCE(?, order_id), error = COALESCE(?, error) WHERE client_order_id = ?",
            (status, order_id, error, client_order_id),
        )

    def order(self, client_order_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)).fetchone()
        return dict(row) if row else None

    def active_order_for(self, broker: str, symbol: str) -> dict[str, Any] | None:
        row = self.db.execute(
            f"SELECT * FROM orders WHERE broker = ? AND symbol = ? AND status IN ({','.join('?' * len(ACTIVE_STATUSES))}) LIMIT 1",  # noqa: S608 - placeholders only
            (broker, symbol, *ACTIVE_STATUSES),
        ).fetchone()
        return dict(row) if row else None

    def orders_in(self, statuses: tuple[str, ...], before: float) -> list[dict[str, Any]]:
        rows = self.db.execute(
            f"SELECT * FROM orders WHERE status IN ({','.join('?' * len(statuses))}) AND ts < ? ORDER BY ts",  # noqa: S608 - placeholders only
            (*statuses, before),
        )
        return [dict(r) for r in rows]

    def recent_orders(self, limit: int = 100, broker: str | None = None) -> list[dict[str, Any]]:
        if broker:
            rows = self.db.execute("SELECT * FROM orders WHERE broker = ? ORDER BY ts DESC LIMIT ?", (broker, limit))
        else:
            rows = self.db.execute("SELECT * FROM orders ORDER BY ts DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def order_counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.db.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")}

    # -- events -------------------------------------------------------------

    def record_event(self, event: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO events (ts, type, data) VALUES (?, ?, ?)",
            (event["ts"], event["type"], json.dumps(event, default=str)),
        )

    def recent_events(self, limit: int = 100, types: list[str] | None = None) -> list[dict[str, Any]]:
        if types:
            rows = self.db.execute(
                f"SELECT data FROM events WHERE type IN ({','.join('?' * len(types))}) ORDER BY id DESC LIMIT ?",  # noqa: S608 - placeholders only
                (*types, limit),
            )
        else:
            rows = self.db.execute("SELECT data FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [json.loads(r["data"]) for r in rows]

    def prune_events(self, now: float | None = None, batch: int = 5_000) -> int:
        """Delete events past their retention, a batch at a time so the engine never waits long."""
        now = time.time() if now is None else now
        rules = [("type = ?", (t, now - days * 86_400)) for t, days in RETENTION_DAYS.items()]
        others = ",".join("?" * len(RETENTION_DAYS))
        rules.append((f"type NOT IN ({others})", (*RETENTION_DAYS, now - DEFAULT_RETENTION_DAYS * 86_400)))
        deleted = 0
        for where, params in rules:
            while True:
                cur = self.db.execute(
                    f"DELETE FROM events WHERE id IN (SELECT id FROM events WHERE {where} AND ts < ? LIMIT ?)",  # noqa: S608 - fixed clauses, values bound
                    (*params, batch),
                )
                deleted += cur.rowcount
                if cur.rowcount < batch:
                    break
        return deleted

    def events_size(self) -> dict[str, Any]:
        rows = self.db.execute("SELECT COUNT(*) AS n, MIN(ts) AS oldest FROM events").fetchone()
        pages = self.db.execute("PRAGMA page_count").fetchone()[0] * self.db.execute("PRAGMA page_size").fetchone()[0]
        return {"rows": rows["n"], "oldest": rows["oldest"], "db_mb": round(pages / 1_048_576, 1)}

    def count_events_since(self, event_type: str, since: float) -> int:
        return self.db.execute("SELECT COUNT(*) FROM events WHERE type = ? AND ts >= ?", (event_type, since)).fetchone()[0]

    # -- position controls (trailing) ------------------------------------------

    def controls(self, broker: str) -> dict[str, dict[str, Any]]:
        return {r["symbol"]: dict(r) for r in self.db.execute("SELECT * FROM position_controls WHERE broker = ?", (broker,))}

    def start_control(self, broker: str, symbol: str, position: str, trailing: bool, max_steps: int, base_target: float) -> None:
        self.db.execute(
            "INSERT INTO position_controls (broker, symbol, position, trailing, max_steps, steps, base_target, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?, ?) ON CONFLICT(broker, symbol) DO UPDATE SET position = excluded.position,"
            " trailing = excluded.trailing, max_steps = excluded.max_steps, steps = 0, base_target = excluded.base_target,"
            " updated_at = excluded.updated_at",
            (broker, symbol, position, int(trailing), max_steps, base_target, time.time()),
        )

    def update_control(self, broker: str, symbol: str, **fields: Any) -> bool:
        allowed = {"trailing", "max_steps", "steps"}
        if not fields or set(fields) - allowed:
            raise ValueError(f"only {sorted(allowed)} can change")
        sets = ", ".join(f"{k} = ?" for k in fields)  # names checked against `allowed` above
        cur = self.db.execute(
            f"UPDATE position_controls SET {sets}, updated_at = ? WHERE broker = ? AND symbol = ?",  # noqa: S608
            (*[int(v) for v in fields.values()], time.time(), broker, symbol),
        )
        return cur.rowcount > 0

    def drop_controls(self, broker: str, keep: set[str]) -> None:
        for symbol in set(self.controls(broker)) - keep:
            self.db.execute("DELETE FROM position_controls WHERE broker = ? AND symbol = ?", (broker, symbol))

    # -- daily risk ---------------------------------------------------------------

    def day_risk(self, broker: str, day: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM daily_risk WHERE broker = ? AND day = ?", (broker, day)).fetchone()
        return dict(row) if row else None

    def start_day(self, broker: str, day: str, equity: float) -> dict[str, Any]:
        self.db.execute("INSERT OR IGNORE INTO daily_risk (broker, day, start_equity) VALUES (?, ?, ?)", (broker, day, equity))
        return self.day_risk(broker, day) or {}

    def halt_day(self, broker: str, day: str, reason: str) -> None:
        self.db.execute("UPDATE daily_risk SET halted_at = ?, halt_reason = ? WHERE broker = ? AND day = ?", (time.time(), reason, broker, day))

    def resume_day(self, broker: str, day: str, equity: float) -> None:
        """Lift today's halt. The day restarts from the current equity, so the limit applies afresh."""
        self.db.execute(
            "UPDATE daily_risk SET halted_at = NULL, halt_reason = NULL, start_equity = ? WHERE broker = ? AND day = ?", (equity, broker, day)
        )
