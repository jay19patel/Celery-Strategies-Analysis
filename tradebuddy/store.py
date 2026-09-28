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
CREATE INDEX IF NOT EXISTS events_type ON events(type, id);
"""

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

    def count_events_since(self, event_type: str, since: float) -> int:
        return self.db.execute("SELECT COUNT(*) FROM events WHERE type = ? AND ts >= ?", (event_type, since)).fetchone()[0]
