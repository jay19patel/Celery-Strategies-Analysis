"""Thread-safe and process-safe SQLite database manager for trading pipeline data.

Replaces MongoDB with a lightweight, embedded SQLite database running in WAL mode
(Write-Ahead Logging). Supports concurrent readers and single writer with a busy timeout.
"""

import logging
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from app.core.settings import settings

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Base exception for SQLite database errors."""


class DatabaseConnectionError(DatabaseError):
    """Raised when connection to SQLite fails."""


class SQLiteDatabase:
    """Process-safe, thread-local SQLite manager with WAL mode and auto-schema initialization."""

    _instance: Optional["SQLiteDatabase"] = None
    _lock = threading.RLock()
    _initialized_paths: set[Path] = set()

    def __init__(self, db_path: Path | str | None = None) -> None:
        """Initialize the SQLite manager.

        Args:
            db_path: Path to SQLite file. If None, reads from settings.sqlite_db_path.
        """
        raw_path = db_path if db_path is not None else settings.sqlite_db_path
        self.db_path = Path(raw_path).resolve()
        self._local = threading.local()
        self._ensure_directory()
        self.init_schema()

    @classmethod
    def get_instance(cls, db_path: Path | str | None = None) -> "SQLiteDatabase":
        """Singleton accessor for the SQLite manager.

        Args:
            db_path: Optional custom path to database file.

        Returns:
            SQLiteDatabase singleton instance.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path)
        return cls._instance

    def _ensure_directory(self) -> None:
        """Create parent directory for SQLite database if it does not exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        """Get or create a thread-local SQLite connection configured with WAL mode.

        Returns:
            sqlite3.Connection for current thread.
        """
        if not hasattr(self._local, "connection") or self._local.connection is None:
            try:
                # PERF: timeout=10.0 handles concurrent writes gracefully
                conn = sqlite3.connect(
                    str(self.db_path),
                    timeout=10.0,
                    check_same_thread=False,
                    isolation_level=None,  # Autocommit mode; use explicit transactions
                )
                conn.row_factory = sqlite3.Row
                # PERF: TRUNCATE journal mode is rock-solid on container volume mounts (no -shm lock contention)
                conn.execute("PRAGMA journal_mode = TRUNCATE;")
                conn.execute("PRAGMA synchronous = NORMAL;")
                conn.execute("PRAGMA busy_timeout = 15000;")
                conn.execute("PRAGMA foreign_keys = ON;")
                self._local.connection = conn
            except sqlite3.Error as exc:
                logger.error(f"Failed to connect to SQLite at {self.db_path}: {exc}", exc_info=True)
                raise DatabaseConnectionError(f"Cannot connect to SQLite: {exc}") from exc

        return self._local.connection

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor]:
        """Context manager for explicit transaction handling.

        Yields:
            sqlite3.Cursor in an active transaction.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE;")
        try:
            yield cursor
            cursor.execute("COMMIT;")
        except Exception as exc:
            cursor.execute("ROLLBACK;")
            logger.error(f"Transaction failed, rolled back: {exc}", exc_info=True)
            raise DatabaseError(f"Transaction rolled back: {exc}") from exc
        finally:
            cursor.close()

    def execute_query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Execute a SELECT query and return list of dictionaries.

        Args:
            sql: Parameterized SQL string.
            params: Query parameter tuple.

        Returns:
            List of row dicts.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            # SECURITY: Always enforce parameterized queries, never format SQL with f-strings
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            logger.error(f"SQL execute error on '{sql}': {exc}", exc_info=True)
            raise DatabaseError(f"Query execution failed: {exc}") from exc
        finally:
            cursor.close()

    def execute_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        """Execute a SELECT query expecting at most one row.

        Args:
            sql: Parameterized SQL string.
            params: Query parameter tuple.

        Returns:
            Row dictionary or None if not found.
        """
        results = self.execute_query(sql, params)
        if not results:
            return None
        return results[0]

    def execute_modify(self, sql: str, params: tuple = ()) -> int:
        """Execute INSERT/UPDATE/DELETE query.

        Args:
            sql: Parameterized SQL string.
            params: Query parameter tuple.

        Returns:
            Number of affected rows or lastrowid for INSERT.
        """
        with self.transaction() as cursor:
            # SECURITY: Parameterized query enforcement
            cursor.execute(sql, params)
            if sql.strip().upper().startswith("INSERT"):
                return cursor.lastrowid or 0
            return cursor.rowcount

    def init_schema(self) -> None:
        """Initialize all required SQLite database tables and indexes."""
        if self.db_path in SQLiteDatabase._initialized_paths:
            return
        with SQLiteDatabase._lock:
            if self.db_path in SQLiteDatabase._initialized_paths:
                return
            self._do_init_schema()
            SQLiteDatabase._initialized_paths.add(self.db_path)

    def _do_init_schema(self) -> None:
        """Internal execution of schema initialization."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            # 1. Broker accounts
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS broker_accounts (
                    id TEXT PRIMARY KEY,
                    strategy_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    capital REAL NOT NULL DEFAULT 100.0,
                    total_trades INTEGER NOT NULL DEFAULT 0,
                    winning_trades INTEGER NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0.0,
                    open_position TEXT,
                    updated_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_accounts_strategy ON broker_accounts(strategy_name);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_accounts_symbol ON broker_accounts(symbol);")

            # 2. Broker trades (closed trades)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS broker_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    type TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    size REAL NOT NULL,
                    capital_allocated REAL NOT NULL,
                    margin_used REAL NOT NULL,
                    leverage REAL NOT NULL,
                    notional_value REAL NOT NULL,
                    liquidation_price REAL,
                    gross_pnl REAL NOT NULL,
                    entry_fee REAL NOT NULL,
                    exit_fee REAL NOT NULL,
                    total_fees REAL NOT NULL,
                    pnl REAL NOT NULL,
                    return_pct REAL NOT NULL,
                    reason TEXT NOT NULL,
                    stop_price REAL,
                    target_price REAL,
                    created_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON broker_trades(exit_time);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy ON broker_trades(strategy_name);")

            # 3. Portfolio state
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_state (
                    id TEXT PRIMARY KEY,
                    balance REAL NOT NULL,
                    peak_equity REAL NOT NULL,
                    is_throttled INTEGER NOT NULL DEFAULT 0,
                    open_positions TEXT NOT NULL,
                    pending_entries TEXT NOT NULL,
                    last_candle_time TEXT,
                    raw_state TEXT,
                    updated_at TEXT NOT NULL
                );
            """)

            # 4. Portfolio trades
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    stop_price REAL,
                    target_price REAL,
                    size REAL NOT NULL,
                    leverage REAL NOT NULL,
                    direction INTEGER NOT NULL,
                    pnl REAL NOT NULL,
                    return_pct REAL NOT NULL,
                    exit_reason TEXT,
                    raw_data TEXT,
                    recorded_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_port_trades_exit ON portfolio_trades(exit_time);")

            # 5. Signals log
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    price REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    execution_time REAL NOT NULL,
                    subscribers_received INTEGER NOT NULL DEFAULT 0,
                    mode TEXT DEFAULT 'PAPER',
                    action TEXT DEFAULT 'paper_executed',
                    created_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_time ON signals_log(timestamp);")
            try:
                cursor.execute("ALTER TABLE signals_log ADD COLUMN mode TEXT DEFAULT 'PAPER';")
            except sqlite3.OperationalError:
                pass
            try:
                cursor.execute("ALTER TABLE signals_log ADD COLUMN action TEXT DEFAULT 'paper_executed';")
            except sqlite3.OperationalError:
                pass

            # 6. Batch results
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS batch_results (
                    id TEXT PRIMARY KEY,
                    batch_data TEXT NOT NULL,
                    total_symbols INTEGER NOT NULL,
                    total_strategies INTEGER NOT NULL,
                    total_results INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_batch_created ON batch_results(created_at);")

            # 7. System status
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_status (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # 8. Live orders (Delta Exchange orders audit)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS live_orders (
                    id TEXT PRIMARY KEY,
                    product_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    size REAL NOT NULL,
                    order_type TEXT NOT NULL,
                    limit_price REAL,
                    stop_price REAL,
                    status TEXT NOT NULL,
                    is_bracket INTEGER NOT NULL DEFAULT 0,
                    raw_data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_live_orders_symbol ON live_orders(symbol);")

            # 9. Live positions (Delta Exchange open positions)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS live_positions (
                    symbol TEXT PRIMARY KEY,
                    product_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    size REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    mark_price REAL NOT NULL,
                    liquidation_price REAL,
                    leverage INTEGER NOT NULL,
                    unrealized_pnl REAL NOT NULL DEFAULT 0.0,
                    realized_pnl REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # 10. System config
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # 11. Strategy execution configurations
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS strategy_configs (
                    strategy_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    symbols TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    is_paper_enabled INTEGER NOT NULL DEFAULT 1,
                    is_real_enabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            now_iso = datetime.now(UTC).isoformat()
            cursor.execute("""
                INSERT OR IGNORE INTO strategy_configs (strategy_id, name, symbols, timeframe, is_paper_enabled, is_real_enabled, created_at, updated_at)
                VALUES ('CombinedPortfolioStrategy', 'Combined Portfolio Strategy (Long & Short)', 'BTC-USD,ETH-USD,SOL-USD', '1h', 1, 0, ?, ?);
            """, (now_iso, now_iso))
            cursor.execute("""
                INSERT OR IGNORE INTO strategy_configs (strategy_id, name, symbols, timeframe, is_paper_enabled, is_real_enabled, created_at, updated_at)
                VALUES ('MotherCandleStrategy', 'Mother Candle Multi-Timeframe Strategy', 'BTC-USD,ETH-USD,SOL-USD', '15m', 1, 0, ?, ?);
            """, (now_iso, now_iso))
            logger.info("✅ SQLite schema and indexes initialized successfully.")
        except Exception as exc:
            logger.error(f"Failed to initialize SQLite schema: {exc}", exc_info=True)
            raise DatabaseError(f"Schema initialization failed: {exc}") from exc
        finally:
            cursor.close()

    def get_database_stats(self) -> dict[str, Any]:
        """Collect metrics on SQLite file size, WAL file size, and row counts.

        Returns:
            Dictionary with database statistics.
        """
        db_size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        wal_path = Path(f"{self.db_path}-wal")
        wal_size_bytes = wal_path.stat().st_size if wal_path.exists() else 0

        tables = [
            "broker_accounts",
            "broker_trades",
            "portfolio_trades",
            "signals_log",
            "batch_results",
            "live_orders",
            "live_positions",
            "strategy_configs",
        ]
        counts: dict[str, int] = {}
        for table in tables:
            try:
                row = self.execute_one(f"SELECT COUNT(*) as cnt FROM {table};")
                counts[table] = row["cnt"] if row else 0
            except Exception:
                counts[table] = 0

        return {
            "db_path": str(self.db_path),
            "size_mb": round(db_size_bytes / (1024 * 1024), 2),
            "wal_size_mb": round(wal_size_bytes / (1024 * 1024), 2),
            "table_counts": counts,
        }


# Global helper functions for convenient access
def get_sqlite_db() -> SQLiteDatabase:
    """Returns the singleton SQLiteDatabase instance."""
    return SQLiteDatabase.get_instance()
