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
-- One row per underlying per minute: the options numbers worth charting and, later, training on.
-- Rows on a 15-minute boundary are kept for good (keep = 1); the rest for OPTIONS_MINUTE_DAYS.
CREATE TABLE IF NOT EXISTS options_history (
    underlying       TEXT NOT NULL,
    ts               REAL NOT NULL,
    keep             INTEGER NOT NULL DEFAULT 0,
    spot             REAL,
    atm_iv           REAL,
    near_atm_iv      REAL,
    skew_25d         REAL,
    pcr_oi           REAL,
    pcr_volume       REAL,
    call_oi          REAL,
    put_oi           REAL,
    oi_usd           REAL,
    turnover_usd     REAL,
    max_pain         REAL,
    implied_move_pct REAL,
    PRIMARY KEY (underlying, ts)
);
CREATE INDEX IF NOT EXISTS options_history_keep ON options_history(keep, ts);
-- Every TradeBuddy AI attempt with its full written report, so any day's analysis can be read again.
CREATE TABLE IF NOT EXISTS ai_reports (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL UNIQUE,
    ok       INTEGER NOT NULL,
    health   TEXT,
    model    TEXT,
    headline TEXT,
    status   INTEGER,
    error    TEXT,
    data     TEXT NOT NULL
);
"""

OPTIONS_COLUMNS = (
    "spot", "atm_iv", "near_atm_iv", "skew_25d", "pcr_oi", "pcr_volume", "call_oi", "put_oi", "oi_usd", "turnover_usd",
    "max_pain", "implied_move_pct",
)
OPTIONS_MINUTE_DAYS = 30
AI_REPORT_DAYS = 90  # written reports; about 1.5 MB a day at one every 5 minutes
AI_FAILED_DAYS = 14  # attempts that produced no report

# What each table holds and keeps, for the System page.
TABLE_INFO = {
    "events": ("Event log: signals, skips, orders, errors, analysis", "ts", "2-90 days by type (RETENTION_DAYS)"),
    "orders": ("Every order ever reserved, on every broker", "ts", "forever"),
    "paper_trades": ("Closed paper trades", "closed_at", "forever"),
    "paper_positions": ("Open paper positions", "opened_at", "while open"),
    "paper_account": ("Paper balance and totals", None, "one row"),
    "options_history": ("Options numbers per underlying per minute", "ts", f"{OPTIONS_MINUTE_DAYS} days; 15-minute rows forever"),
    "ai_reports": ("Every TradeBuddy AI report, in full", "ts", f"{AI_REPORT_DAYS} days; failed attempts {AI_FAILED_DAYS}"),
    "daily_risk": ("Start-of-day equity and halts, per broker", None, "forever (one row a day)"),
    "position_controls": ("Trailing switch and steps per open position", "updated_at", "while open"),
    "settings": ("Runtime settings (secrets included, never sent out)", None, "forever"),
    "toggles": ("Trading, strategy and symbol switches", None, "forever"),
}

# How long the event log keeps each type. The orders, paper_trades and daily_risk tables are the
# permanent record; the log is for looking back. Market and plumbing events go first.
RETENTION_DAYS = {
    "CandleClosed": 2,
    "MarketAnalysis": 3,
    "AIReport": 14,  # the TB-AI page shows the latest and a history  # one per symbol every 5 minutes; the latest is what the dashboard shows
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
        self._backfill_ai_reports()

    def _add_missing_columns(self) -> None:
        # Databases created before orders were tagged with a broker.
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(orders)")}
        if "broker" not in columns:
            self.db.execute("ALTER TABLE orders ADD COLUMN broker TEXT NOT NULL DEFAULT 'delta'")
        self.db.execute("CREATE INDEX IF NOT EXISTS orders_symbol_status ON orders(broker, symbol, status)")

    def _backfill_ai_reports(self) -> None:
        # Reports written before the ai_reports table existed live only in the event log: copy them once.
        if self.db.execute("SELECT 1 FROM ai_reports LIMIT 1").fetchone():
            return
        with self.db:
            for row in self.db.execute("SELECT data FROM events WHERE type = 'AIReport' ORDER BY id").fetchall():
                self.record_ai_report(json.loads(row["data"]))

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

    # -- options history ------------------------------------------------------------

    def record_options(self, row: dict[str, Any]) -> None:
        """One minute of options numbers. A row whose minute starts a 15-minute bar is kept for good."""
        minute = int(row["ts"] // 60 * 60)
        values = {c: row.get(c) for c in OPTIONS_COLUMNS}
        self.db.execute(
            f"INSERT OR REPLACE INTO options_history (underlying, ts, keep, {', '.join(OPTIONS_COLUMNS)})"  # noqa: S608 - fixed column names
            f" VALUES (?, ?, ?, {', '.join('?' * len(OPTIONS_COLUMNS))})",
            (row["underlying"], minute, int(minute % 900 == 0), *values.values()),
        )

    def options_history(self, underlying: str, since: float, step: int = 60) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM options_history WHERE underlying = ? AND ts >= ? AND CAST(ts AS INTEGER) % ? = 0 ORDER BY ts",
            (underlying, since, step),
        )
        return [dict(r) for r in rows]

    def prune_options(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        cur = self.db.execute("DELETE FROM options_history WHERE keep = 0 AND ts < ?", (now - OPTIONS_MINUTE_DAYS * 86_400,))
        return cur.rowcount


    # -- TradeBuddy AI reports ------------------------------------------------

    AI_SUMMARY = "id, ts, ok, health, model, headline, status, error"

    def record_ai_report(self, e: dict[str, Any]) -> None:
        report = e.get("report") or {}
        self.db.execute(
            "INSERT OR IGNORE INTO ai_reports (ts, ok, health, model, headline, status, error, data) VALUES (?,?,?,?,?,?,?,?)",
            (e["ts"], int(bool(e.get("ok"))), report.get("health"), e.get("model"), report.get("headline"), e.get("status"), e.get("error") or "", json.dumps(e)),
        )

    def ai_reports(self, start: float = 0.0, end: float = 0.0, ok_only: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        """Summaries (no report body), newest first, with start <= ts < end (end 0 = now)."""
        rows = self.db.execute(
            f"SELECT {self.AI_SUMMARY} FROM ai_reports WHERE ts >= ? AND ts < ? AND ok >= ? ORDER BY ts DESC LIMIT ?",  # noqa: S608 - fixed columns
            (start, end or time.time() + 60, int(ok_only), limit),
        )
        return [dict(r) | {"ok": bool(r["ok"])} for r in rows]

    def ai_report(self, report_id: int) -> dict[str, Any] | None:
        """One attempt in full, with the ids of the reports either side of it (successful ones only)."""
        row = self.db.execute("SELECT id, ts, data FROM ai_reports WHERE id = ?", (report_id,)).fetchone()
        if row is None:
            return None
        older = self.db.execute("SELECT id FROM ai_reports WHERE ts < ? AND ok = 1 ORDER BY ts DESC LIMIT 1", (row["ts"],)).fetchone()
        newer = self.db.execute("SELECT id FROM ai_reports WHERE ts > ? AND ok = 1 ORDER BY ts LIMIT 1", (row["ts"],)).fetchone()
        return json.loads(row["data"]) | {"id": row["id"], "older_id": older["id"] if older else None, "newer_id": newer["id"] if newer else None}

    def ai_reports_span(self) -> dict[str, Any]:
        row = self.db.execute("SELECT COUNT(*) AS n, MIN(ts) AS oldest, SUM(ok) AS good FROM ai_reports").fetchone()
        return {"count": row["n"], "good": row["good"] or 0, "oldest": row["oldest"], "keep_days": AI_REPORT_DAYS}

    def prune_ai_reports(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        cur = self.db.execute(
            "DELETE FROM ai_reports WHERE (ok = 1 AND ts < ?) OR (ok = 0 AND ts < ?)",
            (now - AI_REPORT_DAYS * 86_400, now - AI_FAILED_DAYS * 86_400),
        )
        return cur.rowcount

    def db_stats(self) -> dict[str, Any]:
        """Size of the database file and of every table and index in it."""
        path = self.db.execute("PRAGMA database_list").fetchone()["file"]
        files = {s: Path(path + s).stat().st_size if path and Path(path + s).exists() else 0 for s in ("", "-wal", "-shm")}
        page = self.db.execute("PRAGMA page_size").fetchone()[0]
        try:
            sizes = {r["name"]: r["bytes"] for r in self.db.execute("SELECT name, SUM(pgsize) AS bytes FROM dbstat GROUP BY name")}
        except sqlite3.OperationalError:  # SQLite built without the dbstat table
            sizes = {}
        objects = self.db.execute("SELECT name, type, tbl_name FROM sqlite_master WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'").fetchall()
        tables = []
        for o in [o for o in objects if o["type"] == "table"]:
            name = o["name"]
            what, ts_col, keep = TABLE_INFO.get(name, ("", None, ""))
            row = {"name": name, "what": what, "keep": keep, "rows": self.db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0], "bytes": sizes.get(name)}  # noqa: S608 - names from sqlite_master
            if ts_col:
                span = self.db.execute(f'SELECT MIN("{ts_col}"), MAX("{ts_col}") FROM "{name}"').fetchone()  # noqa: S608 - fixed column names
                row |= {"oldest": span[0], "newest": span[1]}
            row["indexes"] = [{"name": i["name"], "bytes": sizes.get(i["name"])} for i in objects if i["type"] == "index" and i["tbl_name"] == name]
            row["index_bytes"] = sum(i["bytes"] or 0 for i in row["indexes"])
            tables.append(row)
        tables.sort(key=lambda r: -((r["bytes"] or 0) + r["index_bytes"]))
        return {
            "path": path, "file_bytes": files[""], "wal_bytes": files["-wal"], "shm_bytes": files["-shm"],
            "page_size": page, "pages": self.db.execute("PRAGMA page_count").fetchone()[0],
            "free_pages": self.db.execute("PRAGMA freelist_count").fetchone()[0],
            "journal_mode": self.db.execute("PRAGMA journal_mode").fetchone()[0],
            "sqlite_version": sqlite3.sqlite_version, "tables": tables,
        }

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
