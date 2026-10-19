"""SQLite: settings, toggles, the order book, the paper account, and a log of every event."""

from __future__ import annotations

import itertools
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def _number(value: Any) -> float | None:
    """A REAL column takes a number or nothing. market_context carries strings and labels too
    (implied_move_label), and SQLite would happily store one in a REAL column and hand it back as
    text to whatever later tries arithmetic on it."""
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None

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
    opened_at         REAL NOT NULL,
    -- How far the trade went the wrong way and the right way while it was open, as % of the entry.
    -- Without these, a losing trade cannot say whether the signal was wrong or the stop was too tight.
    mae_pct           REAL NOT NULL DEFAULT 0,
    mfe_pct           REAL NOT NULL DEFAULT 0
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
    closed_at       REAL NOT NULL,
    mae_pct         REAL NOT NULL DEFAULT 0,   -- worst excursion against the trade, % of entry
    mfe_pct         REAL NOT NULL DEFAULT 0    -- best excursion in its favour, % of entry
);
-- Open paper option structures (straddle, condor, ...). A close moves the structure to paper_trades.
CREATE TABLE IF NOT EXISTS paper_structures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL UNIQUE,
    symbol          TEXT NOT NULL,              -- the perpetual whose options these are (BTCUSD)
    underlying      TEXT NOT NULL,              -- BTC
    kind            TEXT NOT NULL,
    label           TEXT NOT NULL,
    expiry          REAL NOT NULL,
    qty             INTEGER NOT NULL,           -- contracts per leg
    contract_value  REAL NOT NULL,
    legs            TEXT NOT NULL,              -- JSON: action, kind, strike, symbol, fill price
    net             REAL NOT NULL,              -- per unit: + premium paid (debit), - received (credit)
    premium         REAL NOT NULL,              -- USD paid or received
    capital         REAL NOT NULL,              -- USD max loss, held as margin
    max_profit      REAL,                       -- USD; NULL = unlimited
    entry_fee       REAL NOT NULL,
    sl_pct          REAL NOT NULL,
    tp_pct          REAL NOT NULL,
    strategy        TEXT NOT NULL,
    opened_at       REAL NOT NULL
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
-- Every forecast the model made, and what the market then did over its horizon. One row per symbol
-- per bar: the analyst repeats a bar's forecast every cycle, and the first one is the one it made.
-- A claim no one checks is not a forecast, so the actual is written back when the horizon elapses
-- and the Market Data page draws the two against each other.
CREATE TABLE IF NOT EXISTS forecasts (
    symbol                 TEXT NOT NULL,
    bar_time               INTEGER NOT NULL,
    ts                     REAL NOT NULL,
    horizon_hours          REAL NOT NULL,
    price                  REAL NOT NULL,   -- spot when the claim was made
    up_probability         REAL,
    breakout_probability   REAL,
    breakout_pct           REAL,            -- the threshold that bar's volatility implied
    predicted_realized_vol REAL,
    expected_abs_move      REAL,
    expected_return        REAL,
    skill                  TEXT,            -- what the card claimed skill at, as JSON
    settled_at             REAL,            -- null until the horizon has passed
    actual_price           REAL,
    actual_return          REAL,
    actual_realized_vol    REAL,
    actual_excursion       REAL,            -- the furthest it went either way, as a fraction
    actual_breakout        INTEGER,
    PRIMARY KEY (symbol, bar_time)
);
CREATE INDEX IF NOT EXISTS forecasts_open ON forecasts(settled_at, bar_time);
CREATE INDEX IF NOT EXISTS forecasts_settled ON forecasts(symbol, settled_at, bar_time);
-- The market itself, kept. Everything analytical used to be recomputed from a fresh Delta REST
-- call and thrown away, so nothing could be backtested, no base rate could be counted, and an
-- outage erased that window for good. One row per symbol per closed bar, with the open interest
-- and funding of the same bar carried alongside. Small (~80 bytes), so it is kept for good.
CREATE TABLE IF NOT EXISTS bars (
    symbol   TEXT    NOT NULL,
    interval TEXT    NOT NULL,
    time     INTEGER NOT NULL,   -- bar open, epoch seconds
    open     REAL    NOT NULL,
    high     REAL    NOT NULL,
    low      REAL    NOT NULL,
    close    REAL    NOT NULL,
    volume   REAL    NOT NULL,
    oi       REAL,
    funding  REAL,
    PRIMARY KEY (symbol, interval, time)
);
-- The analyst's reading of one bar, as typed columns rather than the JSON blob the event log kept
-- for three days. Typed because the whole point is to count base rates over a year of it
-- ("how often did price rise from here when IV was this rich"), and JSON extraction cannot be
-- scanned. Rows on a 15-minute boundary are kept for good; the rest for SNAPSHOT_DAYS.
CREATE TABLE IF NOT EXISTS market_snapshots (
    symbol          TEXT    NOT NULL,
    bar_time        INTEGER NOT NULL,
    ts              REAL    NOT NULL,
    keep            INTEGER NOT NULL DEFAULT 0,
    price           REAL,
    change_1h_pct   REAL,
    change_4h_pct   REAL,
    change_24h_pct  REAL,
    rv_24h          REAL,
    rv_7d           REAL,
    atr_pct         REAL,
    range_pos_24h   REAL,
    ema20           REAL,
    ema50           REAL,
    ema200          REAL,
    volume_z_1h     REAL,
    oi_change_1h_pct   REAL,
    oi_change_24h_pct  REAL,
    funding_avg_24h_pct REAL,
    basis_pct       REAL,
    atm_iv          REAL,
    iv_rv_ratio     REAL,
    skew_25d        REAL,
    pcr_oi          REAL,
    pcr_volume      REAL,
    implied_move_pct REAL,
    max_pain_gap_pct REAL,
    call_wall_gap_pct REAL,
    put_wall_gap_pct REAL,
    term_slope      REAL,
    insight_keys    TEXT NOT NULL DEFAULT '',  -- 'iv_rich,funding_high,trend': short and greppable
    lean            REAL,                      -- bullish weight - bearish weight
    PRIMARY KEY (symbol, bar_time, ts)
);
CREATE INDEX IF NOT EXISTS market_snapshots_keep ON market_snapshots(keep, ts);
-- Process load over time. The monitor has always measured CPU, memory and loop lag every two
-- seconds and stored none of it, so "how did it look yesterday" had no answer. One row per
-- process every few minutes is 90 bytes and answers it.
CREATE TABLE IF NOT EXISTS process_health (
    role            TEXT NOT NULL,
    ts              REAL NOT NULL,
    cpu_pct         REAL,
    memory_mb       REAL,
    loop_lag_max_ms REAL,
    PRIMARY KEY (role, ts)
);
CREATE INDEX IF NOT EXISTS process_health_ts ON process_health(ts);
-- Every verdict any card on the Market page made, and what the market then did about it.
-- A verdict nobody marks is decoration: this is what turns "the term structure says an event is
-- priced" into "that card has been right 6 times out of 11, against 5 you would have got by
-- saying nothing". One row per card per bar; the first reading of a bar is the one it made.
CREATE TABLE IF NOT EXISTS predictions (
    symbol        TEXT    NOT NULL,
    source        TEXT    NOT NULL,   -- which card: term_structure, atm_iv, pcr, chain_oi, insights, outlook
    bar_time      INTEGER NOT NULL,
    ts            REAL    NOT NULL,
    horizon_hours REAL    NOT NULL,
    stance        TEXT    NOT NULL,   -- bullish | bearish | neutral | vol_up | vol_down | range | unclear
    measure       TEXT    NOT NULL,   -- direction | volatility | range | '' when it claims nothing
    label         TEXT    NOT NULL,
    confidence    REAL    NOT NULL,
    price         REAL    NOT NULL,
    numbers       TEXT    NOT NULL,   -- JSON: the inputs, so a claim can be traced
    settled_at      REAL,
    actual_return   REAL,
    actual_vol      REAL,             -- realised vol over the horizon
    prior_vol       REAL,             -- and over the same length before it, which is what vol_up/down is judged against
    actual_excursion REAL,
    regime          TEXT,             -- the market it was made in, so the record can be read per regime
    exit_reason     TEXT,             -- tp | sl | timeout: what a bracketed trade would have done
    r_multiple      REAL,             -- that trade's result in units of the risk it took
    correct         INTEGER,          -- did the claim hold
    baseline_correct INTEGER,         -- would saying nothing have held over the same bars
    PRIMARY KEY (symbol, source, bar_time)
);
CREATE INDEX IF NOT EXISTS predictions_open ON predictions(settled_at, bar_time);
CREATE INDEX IF NOT EXISTS predictions_scored ON predictions(symbol, source, settled_at);
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

# The analysis numbers kept per bar. Order is the column order in market_snapshots; a name here
# is read straight out of insights.market_context, so the two stay in step by construction.
SNAPSHOT_COLUMNS = (
    "price", "change_1h_pct", "change_4h_pct", "change_24h_pct", "rv_24h", "rv_7d", "atr_pct", "range_pos_24h",
    "ema20", "ema50", "ema200", "volume_z_1h", "oi_change_1h_pct", "oi_change_24h_pct", "funding_avg_24h_pct",
    "basis_pct", "atm_iv", "iv_rv_ratio", "skew_25d", "pcr_oi", "pcr_volume", "implied_move_pct",
    "max_pain_gap_pct", "call_wall_gap_pct", "put_wall_gap_pct", "term_slope",
)
SNAPSHOT_DAYS = 30  # off-boundary rows; the 15-minute ones are kept for good
HEALTH_DAYS = 30  # process load: 4 processes every 5 minutes is ~1,150 rows a day
PREDICTION_DAYS = 180  # card verdicts and how they turned out: ~6 a bar a symbol, kept long enough to judge
BAR_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14_400, "1d": 86_400}
AI_REPORT_DAYS = 90  # written reports; about 1.5 MB a day at one every 5 minutes
AI_FAILED_DAYS = 14  # attempts that produced no report
FORECAST_DAYS = 180  # forecasts and their outcomes: ~96 small rows a symbol a day, kept long enough to judge

# What each table holds and keeps, for the System page.
TABLE_INFO = {
    "events": ("Event log: signals, skips, orders, errors, analysis", "ts", "2-90 days by type (RETENTION_DAYS)"),
    "orders": ("Every order ever reserved, on every broker", "ts", "forever"),
    "paper_trades": ("Closed paper trades", "closed_at", "forever"),
    "paper_positions": ("Open paper positions", "opened_at", "while open"),
    "paper_account": ("Paper balance and totals", None, "one row"),
    "options_history": ("Options numbers per underlying per minute", "ts", f"{OPTIONS_MINUTE_DAYS} days; 15-minute rows forever"),
    "bars": ("Closed candles with their open interest and funding", "time", "forever"),
    "market_snapshots": ("The analyst's numbers per bar, for base rates", "ts", f"{SNAPSHOT_DAYS} days; 15-minute rows forever"),
    "process_health": ("CPU, memory and loop lag per process", "ts", f"{HEALTH_DAYS} days"),
    "ai_reports": ("Every TradeBuddy AI report, in full", "ts", f"{AI_REPORT_DAYS} days; failed attempts {AI_FAILED_DAYS}"),
    "forecasts": ("Every forecast and what the market then did", "ts", f"{FORECAST_DAYS} days"),
    "predictions": ("Every card verdict and whether it held", "ts", f"{PREDICTION_DAYS} days"),
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
        # Predictions settled before the outcome became a bracketed trade: the new columns stay
        # NULL on them, and the scorecard says so rather than assuming.
        existing = {r["name"] for r in self.db.execute("PRAGMA table_info(predictions)")}
        for column, kind in (("regime", "TEXT"), ("exit_reason", "TEXT"), ("r_multiple", "REAL")):
            if column not in existing:
                self.db.execute(f"ALTER TABLE predictions ADD COLUMN {column} {kind}")
        # Databases created before trades recorded how far they went either way. Rows already closed
        # keep 0: the excursions were never measured and cannot be reconstructed.
        for table in ("paper_positions", "paper_trades"):
            existing = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for column in ("mae_pct", "mfe_pct"):
                if column not in existing:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} REAL NOT NULL DEFAULT 0")

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


    # -- bars -----------------------------------------------------------------------

    def record_bars(
        self, symbol: str, interval: str, candles: list[Any],
        oi: dict[int, float] | None = None, funding: dict[int, float] | None = None,
    ) -> int:
        """Closed candles, with the open interest and funding of the same bar where they are known.

        OR IGNORE, not REPLACE: a bar that is already stored was stored when it closed, and a later
        REST read of the same bar can only be the same numbers or a revision of them — and a
        silently revised past is worse for a base rate than a missing one. Returns rows written."""
        oi, funding = oi or {}, funding or {}
        rows = [
            (symbol, interval, int(c.time), c.open, c.high, c.low, c.close, c.volume,
             oi.get(int(c.time)), funding.get(int(c.time)))
            for c in candles
        ]
        if not rows:
            return 0
        before = self.db.total_changes
        self.db.executemany(
            "INSERT OR IGNORE INTO bars (symbol, interval, time, open, high, low, close, volume, oi, funding)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        return self.db.total_changes - before

    def last_bar_time(self, symbol: str, interval: str) -> int:
        """The newest bar stored, or 0. What a collector asks before deciding how far back to fetch."""
        row = self.db.execute("SELECT MAX(time) AS t FROM bars WHERE symbol = ? AND interval = ?", (symbol, interval)).fetchone()
        return int(row["t"] or 0)

    def bars(self, symbol: str, interval: str, since: float = 0.0, limit: int = 5000) -> list[dict[str, Any]]:
        """Stored bars, oldest first. `limit` applies to the newest, so a chart reads left to right."""
        rows = self.db.execute(
            "SELECT * FROM bars WHERE symbol = ? AND interval = ? AND time >= ? ORDER BY time DESC LIMIT ?",
            (symbol, interval, since, limit),
        )
        return [dict(r) for r in rows][::-1]

    def bar_gaps(self, symbol: str, interval: str, since: float = 0.0) -> list[tuple[int, int]]:
        """(after, before) pairs where consecutive stored bars are more than one bar apart.

        A gap is not a bug to hide: it is a window the feed or the exchange did not cover, and
        anything measured across it is measured across missing market."""
        step = BAR_SECONDS.get(interval, 900)
        times = [int(r["time"]) for r in self.db.execute(
            "SELECT time FROM bars WHERE symbol = ? AND interval = ? AND time >= ? ORDER BY time", (symbol, interval, since),
        )]
        return [(a, b) for a, b in itertools.pairwise(times) if b - a > step]

    # -- the analysis, kept ---------------------------------------------------------

    def record_snapshot(self, symbol: str, bar_time: int, ts: float, ctx: dict[str, Any], insight_keys: str, lean: float | None) -> None:
        """One analysis cycle's numbers. OR IGNORE on (symbol, bar_time, ts): the analyst may run
        twice within a second on a restart, and the first reading is the one it made."""
        self.db.execute(
            f"INSERT OR IGNORE INTO market_snapshots (symbol, bar_time, ts, keep, insight_keys, lean, {', '.join(SNAPSHOT_COLUMNS)}) "  # noqa: S608 - fixed column names
            f"VALUES (?,?,?,?,?,?,{','.join('?' * len(SNAPSHOT_COLUMNS))})",
            (symbol, int(bar_time), ts, int(int(bar_time) % 900 == 0), insight_keys, lean,
             *(_number(ctx.get(c)) for c in SNAPSHOT_COLUMNS)),
        )

    def snapshots(self, symbol: str, since: float = 0.0, limit: int = 5000) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM market_snapshots WHERE symbol = ? AND bar_time >= ? ORDER BY bar_time DESC, ts DESC LIMIT ?",
            (symbol, since, limit),
        )
        return [dict(r) for r in rows][::-1]

    def prune_snapshots(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return self.db.execute(
            "DELETE FROM market_snapshots WHERE keep = 0 AND ts < ?", (now - SNAPSHOT_DAYS * 86_400,),
        ).rowcount

    # -- process load ---------------------------------------------------------------

    def record_health(self, role: str, ts: float, cpu_pct: float | None, memory_mb: float | None, loop_lag_max_ms: float | None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO process_health (role, ts, cpu_pct, memory_mb, loop_lag_max_ms) VALUES (?,?,?,?,?)",
            (role, ts, _number(cpu_pct), _number(memory_mb), _number(loop_lag_max_ms)),
        )

    def health(self, since: float = 0.0, limit: int = 20_000) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM process_health WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (since, limit),
        )
        return [dict(r) for r in rows][::-1]

    def prune_health(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return self.db.execute("DELETE FROM process_health WHERE ts < ?", (now - HEALTH_DAYS * 86_400,)).rowcount

    # -- forecasts and what the market did ------------------------------------

    FORECAST_FIELDS = (
        "horizon_hours", "up_probability", "breakout_probability", "breakout_pct",
        "predicted_realized_vol", "expected_abs_move", "expected_return",
    )

    def record_forecast(self, symbol: str, bar_time: int, ts: float, price: float, forecast: dict[str, Any]) -> None:
        """The claim, as it was made. OR IGNORE: the analyst repeats a bar's forecast every cycle and
        only the first is the one it made with what it knew then."""
        values = [forecast.get(k) for k in self.FORECAST_FIELDS]
        self.db.execute(
            f"INSERT OR IGNORE INTO forecasts (symbol, bar_time, ts, price, skill, {', '.join(self.FORECAST_FIELDS)}) "  # noqa: S608 - fixed column names
            f"VALUES (?,?,?,?,?,{','.join('?' * len(self.FORECAST_FIELDS))})",
            (symbol, int(bar_time), ts, price, json.dumps(forecast.get("skill") or {}), *values),
        )

    def unsettled_forecasts(self, now: float | None = None, limit: int = 500) -> list[dict[str, Any]]:
        """Forecasts whose horizon has passed and which nothing has scored yet, oldest first."""
        now = time.time() if now is None else now
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM forecasts WHERE settled_at IS NULL AND bar_time + horizon_hours * 3600 <= ? ORDER BY bar_time LIMIT ?",
            (now, limit),
        )]

    def settle_forecast(
        self, symbol: str, bar_time: int, now: float, price: float, ret: float, realized_vol: float,
        excursion: float, breakout: bool,
    ) -> None:
        self.db.execute(
            "UPDATE forecasts SET settled_at = ?, actual_price = ?, actual_return = ?, actual_realized_vol = ?, "
            "actual_excursion = ?, actual_breakout = ? WHERE symbol = ? AND bar_time = ?",
            (now, price, ret, realized_vol, excursion, int(breakout), symbol, int(bar_time)),
        )

    def forecast_history(self, symbol: str, since: float = 0.0, limit: int = 2000) -> list[dict[str, Any]]:
        """Settled forecasts, oldest first, so the chart reads left to right."""
        rows = self.db.execute(
            "SELECT * FROM forecasts WHERE symbol = ? AND settled_at IS NOT NULL AND bar_time >= ? "
            "ORDER BY bar_time DESC LIMIT ?",
            (symbol, since, limit),
        )
        return [dict(r) for r in rows][::-1]

    def prune_forecasts(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return self.db.execute("DELETE FROM forecasts WHERE ts < ?", (now - FORECAST_DAYS * 86_400,)).rowcount

    # -- verdicts, and whether they held --------------------------------------

    def record_verdict(self, symbol: str, bar_time: int, ts: float, price: float, v: dict[str, Any]) -> None:
        """A card's claim, as it was made. OR IGNORE: the analyst re-reads the same bar every cycle
        and the first reading is the one it made with what it knew then."""
        self.db.execute(
            "INSERT OR IGNORE INTO predictions (symbol, source, bar_time, ts, horizon_hours, stance, measure, label,"
            " confidence, price, numbers) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, v["key"], int(bar_time), ts, float(v["horizon_hours"]), v["stance"], v.get("measure") or "",
             v["label"], float(v["confidence"]), price, json.dumps(v.get("numbers") or {})),
        )

    def unsettled_verdicts(self, now: float | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        """Verdicts whose horizon has passed and which nothing has marked yet. Claims that measure
        nothing ("not enough to say") are settled on sight — they were never a claim."""
        now = time.time() if now is None else now
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM predictions WHERE settled_at IS NULL AND measure != '' AND bar_time + horizon_hours * 3600 <= ?"
            " ORDER BY bar_time LIMIT ?",
            (now, limit),
        )]

    def settle_verdict(
        self, symbol: str, source: str, bar_time: int, now: float, ret: float, vol: float, prior_vol: float,
        excursion: float, correct: bool, baseline_correct: bool,
        regime: str = "", exit_reason: str = "", r_multiple: float | None = None,
    ) -> None:
        self.db.execute(
            "UPDATE predictions SET settled_at = ?, actual_return = ?, actual_vol = ?, prior_vol = ?,"
            " actual_excursion = ?, regime = ?, exit_reason = ?, r_multiple = ?, correct = ?, baseline_correct = ?"
            " WHERE symbol = ? AND source = ? AND bar_time = ?",
            (now, ret, vol, prior_vol, excursion, regime, exit_reason, r_multiple,
             int(correct), int(baseline_correct), symbol, source, int(bar_time)),
        )

    def verdict_record(self, symbol: str = "", since: float = 0.0, limit: int = 5000) -> list[dict[str, Any]]:
        """Settled verdicts, oldest first."""
        where = "settled_at IS NOT NULL AND bar_time >= ?"
        params: list[Any] = [since]
        if symbol:
            where += " AND symbol = ?"
            params.append(symbol)
        rows = self.db.execute(
            f"SELECT * FROM predictions WHERE {where} ORDER BY bar_time DESC LIMIT ?",  # noqa: S608 - fixed clauses, values bound
            (*params, limit),
        )
        return [dict(r) for r in rows][::-1]

    def verdicts_between(self, symbol: str, since: float, source: str = "", limit: int = 4000) -> list[dict[str, Any]]:
        """Every verdict made over a window, settled or not, oldest first.

        `verdict_record` keeps only the marked ones, which is right for a scoreboard and wrong for
        a chart: the claims still waiting on their horizon are the ones at the right-hand edge,
        and leaving them out would make the most recent stretch look like nothing was said."""
        where = "symbol = ? AND bar_time >= ?"
        params: list[Any] = [symbol, since]
        if source:
            where += " AND source = ?"
            params.append(source)
        rows = self.db.execute(
            f"SELECT symbol, source, bar_time, ts, horizon_hours, stance, measure, label, confidence, price,"  # noqa: S608 - fixed clauses, values bound
            f" settled_at, actual_return, regime, exit_reason, r_multiple, correct, baseline_correct"
            f" FROM predictions WHERE {where}"
            f" ORDER BY bar_time DESC LIMIT ?",
            (*params, limit),
        )
        return [dict(r) for r in rows][::-1]

    def prune_predictions(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return self.db.execute("DELETE FROM predictions WHERE ts < ?", (now - PREDICTION_DAYS * 86_400,)).rowcount

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

    def realized_since(self, broker: str, since: float) -> dict[str, float]:
        """Net realized P&L per strategy on one broker since `since`, from the PositionClosed log.

        The log is the only per-strategy record every broker writes (paper_trades is the paper
        broker's own table and Delta has none), and closed positions are kept for 90 days, far
        longer than the day this reads."""
        rows = self.db.execute(
            "SELECT json_extract(data, '$.strategy') AS strategy, SUM(json_extract(data, '$.pnl')) AS pnl "
            "FROM events WHERE type = 'PositionClosed' AND ts >= ? AND json_extract(data, '$.broker') = ? "
            "GROUP BY strategy",
            (since, broker),
        )
        return {r["strategy"]: float(r["pnl"] or 0.0) for r in rows if r["strategy"]}

    def resume_day(self, broker: str, day: str, equity: float) -> None:
        """Lift today's halt. The day restarts from the current equity, so the limit applies afresh."""
        self.db.execute(
            "UPDATE daily_risk SET halted_at = NULL, halt_reason = NULL, start_equity = ? WHERE broker = ? AND day = ?", (equity, broker, day)
        )
