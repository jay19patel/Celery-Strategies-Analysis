"""SQLite database adapter providing collection-style interface.

Provides MongoDB-like collection interfaces (`find`, `find_one`, `insert_one`,
`replace_one`, `delete_many`, `count_documents`) backed directly by the embedded
SQLite database. Enables cleanly structured repository access across services.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.database.sqlite_db import get_sqlite_db

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(UTC).isoformat()


class SQLiteCollectionAdapter:
    """Provides a collection-style document API over SQLite tables."""

    def __init__(self, table_name: str) -> None:
        """Initialize collection adapter.

        Args:
            table_name: Name of underlying SQLite table.
        """
        self.table_name = table_name
        self.db = get_sqlite_db()

    def insert_one(self, doc: dict[str, Any]) -> Any:
        """Insert a single document into the collection.

        Args:
            doc: Dictionary of document values.

        Returns:
            InsertResult containing inserted_id.
        """
        item = dict(doc)
        now_str = _now_iso()

        if self.table_name == "signals_log":
            strategy_name = item.get("strategy_name", "")
            symbol = item.get("symbol", "")
            signal_type = str(item.get("signal_type", ""))
            price = float(item.get("price", 0.0))
            ts = item.get("timestamp", now_str)
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            exec_time = float(item.get("execution_time", 0.0))
            subs = int(item.get("subscribers_received", 0))
            mode = str(item.get("mode", "PAPER"))
            action = str(item.get("action", "paper_executed"))

            sql = """
                INSERT INTO signals_log (strategy_name, symbol, signal_type, price, timestamp, execution_time, subscribers_received, mode, action, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """
            row_id = self.db.execute_modify(sql, (strategy_name, symbol, signal_type, price, ts_str, exec_time, subs, mode, action, now_str))
            item["_id"] = row_id

        elif self.table_name == "broker_accounts":
            doc_id = item.get("_id", f"{item.get('strategy_name')}::{item.get('symbol')}")
            strat = item.get("strategy_name", "")
            symbol = item.get("symbol", "")
            capital = float(item.get("capital", 100.0))
            trades = int(item.get("total_trades", 0))
            wins = int(item.get("winning_trades", 0))
            win_rate = float(item.get("win_rate", 0.0))
            open_pos = json.dumps(item.get("open_position"), default=str) if item.get("open_position") else None

            sql = """
                INSERT OR REPLACE INTO broker_accounts (id, strategy_name, symbol, capital, total_trades, winning_trades, win_rate, open_position, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """
            self.db.execute_modify(sql, (doc_id, strat, symbol, capital, trades, wins, win_rate, open_pos, now_str))
            item["_id"] = doc_id

        elif self.table_name == "broker_trades":
            strat = item.get("strategy_name", "")
            symbol = item.get("symbol", "")
            trade_type = str(item.get("type", ""))
            entry_t = item.get("entry_time", now_str)
            entry_t_str = entry_t.isoformat() if hasattr(entry_t, "isoformat") else str(entry_t)
            exit_t = item.get("exit_time", now_str)
            exit_t_str = exit_t.isoformat() if hasattr(exit_t, "isoformat") else str(exit_t)

            sql = """
                INSERT INTO broker_trades (
                    strategy_name, symbol, type, entry_time, exit_time, entry_price, exit_price,
                    size, capital_allocated, margin_used, leverage, notional_value, liquidation_price,
                    gross_pnl, entry_fee, exit_fee, total_fees, pnl, return_pct, reason,
                    stop_price, target_price, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """
            row_id = self.db.execute_modify(
                sql,
                (
                    strat, symbol, trade_type, entry_t_str, exit_t_str,
                    float(item.get("entry_price", 0.0)), float(item.get("exit_price", 0.0)),
                    float(item.get("size", 0.0)), float(item.get("capital_allocated", 0.0)),
                    float(item.get("margin_used", 0.0)), float(item.get("leverage", 1.0)),
                    float(item.get("notional_value", 0.0)), float(item.get("liquidation_price", 0.0)) if item.get("liquidation_price") else None,
                    float(item.get("gross_pnl", 0.0)), float(item.get("entry_fee", 0.0)),
                    float(item.get("exit_fee", 0.0)), float(item.get("total_fees", 0.0)),
                    float(item.get("pnl", 0.0)), float(item.get("return_pct", 0.0)),
                    str(item.get("reason", "")),
                    float(item.get("stop_price", 0.0)) if item.get("stop_price") else None,
                    float(item.get("target_price", 0.0)) if item.get("target_price") else None,
                    now_str,
                ),
            )
            item["_id"] = row_id

        elif self.table_name == "system_status":
            doc_id = str(item.get("_id", "status"))
            data_json = json.dumps(item, default=str)
            sql = "INSERT OR REPLACE INTO system_status (id, data, updated_at) VALUES (?, ?, ?);"
            self.db.execute_modify(sql, (doc_id, data_json, now_str))
            item["_id"] = doc_id

        elif self.table_name == "batch_results":
            doc_id = str(item.get("_id", uuid.uuid4().hex))
            batch_json = json.dumps(item, default=str)
            symbols_cnt = int(item.get("summary", {}).get("total_symbols", 0))
            strats_cnt = int(item.get("summary", {}).get("total_strategies", 0))
            res_cnt = int(item.get("total_results", 0))

            sql = """
                INSERT OR REPLACE INTO batch_results (id, batch_data, total_symbols, total_strategies, total_results, created_at)
                VALUES (?, ?, ?, ?, ?, ?);
            """
            self.db.execute_modify(sql, (doc_id, batch_json, symbols_cnt, strats_cnt, res_cnt, now_str))
            item["_id"] = doc_id

        class InsertResult:
            def __init__(self, inserted_id: Any) -> None:
                self.inserted_id = inserted_id

        return InsertResult(item.get("_id"))

    def find_one(self, filter_dict: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Find a single document matching the filter."""
        results = self.find(filter_dict)
        return results[0] if results else None

    def find(self, filter_dict: dict[str, Any] | None = None) -> "SQLiteCursor":
        """Query documents matching filter."""
        return SQLiteCursor(self.table_name, filter_dict)

    def replace_one(self, filter_dict: dict[str, Any], replacement: dict[str, Any], upsert: bool = False) -> Any:
        """Replace a document matching the filter."""
        return self.insert_one(replacement)

    def update_one(self, filter_dict: dict[str, Any], update_dict: dict[str, Any], upsert: bool = False) -> Any:
        """Update a document matching the filter."""
        existing = self.find_one(filter_dict)
        if existing:
            set_vals = update_dict.get("$set", update_dict)
            existing.update(set_vals)
            self.insert_one(existing)
        elif upsert:
            set_vals = update_dict.get("$set", update_dict)
            merged = {**filter_dict, **set_vals}
            self.insert_one(merged)

    def delete_many(self, filter_dict: dict[str, Any]) -> Any:
        """Delete documents matching the filter."""
        if not filter_dict:
            self.db.execute_modify(f"DELETE FROM {self.table_name};")
            class DeleteResult:
                deleted_count = 1
            return DeleteResult()

        class DeleteResult:
            deleted_count = 0
        return DeleteResult()

    def count_documents(self, filter_dict: dict[str, Any] | None = None) -> int:
        """Count total documents in collection."""
        row = self.db.execute_one(f"SELECT COUNT(*) as cnt FROM {self.table_name};")
        return row["cnt"] if row else 0


class SQLiteCursor:
    """Cursor wrapper for collection-style queries over SQLite."""

    def __init__(self, table_name: str, filter_dict: dict[str, Any] | None = None) -> None:
        self.table_name = table_name
        self.filter_dict = filter_dict or {}
        self._limit: int | None = None
        self._skip: int = 0
        self._sort_field: str | None = None
        self._sort_dir: int = 1
        self.db = get_sqlite_db()

    def sort(self, key_or_list: Any, direction: int = 1) -> "SQLiteCursor":
        if isinstance(key_or_list, list):
            self._sort_field = key_or_list[0][0]
            self._sort_dir = key_or_list[0][1]
        else:
            self._sort_field = key_or_list
            self._sort_dir = direction
        return self

    def limit(self, count: int) -> "SQLiteCursor":
        self._limit = count
        return self

    def skip(self, count: int) -> "SQLiteCursor":
        self._skip = count
        return self

    def _execute(self) -> list[dict[str, Any]]:
        where_clauses: list[str] = []
        params: list[Any] = []

        for k, v in self.filter_dict.items():
            if k == "_id":
                where_clauses.append("id = ?")
                params.append(str(v))
            elif k in ("symbol", "strategy_name"):
                where_clauses.append(f"{k} = ?")
                params.append(str(v))

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        order_sql = ""
        if self._sort_field:
            col = "id" if self._sort_field == "_id" else self._sort_field
            dir_str = "ASC" if self._sort_dir == 1 else "DESC"
            order_sql = f"ORDER BY {col} {dir_str}"

        limit_sql = ""
        if self._limit is not None:
            limit_sql = f"LIMIT {self._limit} OFFSET {self._skip}"

        sql = f"SELECT * FROM {self.table_name} {where_sql} {order_sql} {limit_sql};".strip()
        rows = self.db.execute_query(sql, tuple(params))

        results: list[dict[str, Any]] = []
        for r in rows:
            doc = dict(r)
            if "id" in doc:
                doc["_id"] = doc["id"]
            if doc.get("open_position"):
                try:
                    doc["open_position"] = json.loads(doc["open_position"])
                except Exception:
                    pass
            if doc.get("data"):
                try:
                    raw_data = json.loads(doc["data"])
                    if isinstance(raw_data, dict):
                        doc.update(raw_data)
                except Exception:
                    pass
            if doc.get("batch_data"):
                try:
                    raw_batch = json.loads(doc["batch_data"])
                    if isinstance(raw_batch, dict):
                        doc.update(raw_batch)
                except Exception:
                    pass
            results.append(doc)
        return results

    def __iter__(self):
        return iter(self._execute())

    def __len__(self) -> int:
        return len(self._execute())

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self._execute()[index]


class DatabaseConnection:
    """Mock connection provider that delegates to SQLite."""

    @classmethod
    def get_database(cls):
        class DatabaseProxy:
            def __getattr__(self, name: str) -> SQLiteCollectionAdapter:
                return SQLiteCollectionAdapter(name)
            def __getitem__(self, name: str) -> SQLiteCollectionAdapter:
                return SQLiteCollectionAdapter(name)
        return DatabaseProxy()

    @classmethod
    def get_collection(cls, collection_name: str) -> SQLiteCollectionAdapter:
        return SQLiteCollectionAdapter(collection_name)

    @classmethod
    def close(cls) -> None:
        pass


# Backward compatibility alias
MongoDBConnection = DatabaseConnection


def get_database() -> Any:
    """Return database proxy delegating to SQLite."""
    return DatabaseConnection.get_database()


def get_collection(collection_name: str) -> SQLiteCollectionAdapter:
    """Return collection adapter for collection_name."""
    return SQLiteCollectionAdapter(collection_name)


def save_batch_results(batch_data: dict[str, Any]) -> str:
    """Save batch execution results to SQLite."""
    adapter = SQLiteCollectionAdapter("batch_results")
    res = adapter.insert_one(batch_data)
    return str(res.inserted_id)


def get_latest_batch_results(limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve latest batch results from SQLite."""
    adapter = SQLiteCollectionAdapter("batch_results")
    return list(adapter.find().sort("created_at", -1).limit(limit))


def get_symbol_results(symbol: str, limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve batch results for a specific symbol from SQLite."""
    adapter = SQLiteCollectionAdapter("batch_results")
    return list(adapter.find({"symbol": symbol}).sort("created_at", -1).limit(limit))
