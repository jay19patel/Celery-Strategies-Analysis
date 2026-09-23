"""Unit tests for SQLiteDatabase manager."""

import tempfile
from pathlib import Path

import pytest

from app.database.sqlite_db import SQLiteDatabase


def test_sqlite_db_init_and_schema():
    """Verify that SQLiteDatabase initializes tables in a fresh file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_trading.db"
        db = SQLiteDatabase(db_path=db_path)

        assert db_path.exists()

        # Check broker_accounts table
        db.execute_modify(
            "INSERT INTO broker_accounts (id, strategy_name, symbol, capital, updated_at) VALUES (?, ?, ?, ?, ?);",
            ("test_strat::BTC-USD", "test_strat", "BTC-USD", 100.0, "2026-09-20T00:00:00Z"),
        )

        account = db.execute_one("SELECT * FROM broker_accounts WHERE id = ?;", ("test_strat::BTC-USD",))
        assert account is not None
        assert account["strategy_name"] == "test_strat"
        assert account["symbol"] == "BTC-USD"
        assert account["capital"] == 100.0

        stats = db.get_database_stats()
        assert stats["table_counts"]["broker_accounts"] == 1


def test_sqlite_db_trade_insert_and_query():
    """Verify broker_trades insertion and aggregate queries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_trades.db"
        db = SQLiteDatabase(db_path=db_path)

        db.execute_modify(
            """
            INSERT INTO broker_trades (
                strategy_name, symbol, type, entry_time, exit_time,
                entry_price, exit_price, size, capital_allocated,
                margin_used, leverage, notional_value, gross_pnl,
                entry_fee, exit_fee, total_fees, pnl, return_pct,
                reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                "EMA_Strat", "ETH-USD", "LONG", "2026-09-20T10:00:00Z", "2026-09-20T11:00:00Z",
                2500.0, 2550.0, 1.0, 100.0,
                50.0, 20.0, 2500.0, 50.0,
                1.25, 1.275, 2.525, 47.475, 94.95,
                "Target Hit", "2026-09-20T11:00:01Z"
            ),
        )

        trades = db.execute_query("SELECT * FROM broker_trades WHERE symbol = ?;", ("ETH-USD",))
        assert len(trades) == 1
        assert trades[0]["pnl"] == 47.475
        assert trades[0]["reason"] == "Target Hit"


def test_sqlite_adapter_compatibility():
    """Verify that collection-style get_collection works seamlessly over SQLite."""
    from app.database.sqlite_adapter import get_collection

    coll = get_collection("signals_log")
    coll.insert_one({
        "strategy_name": "TestStrategy",
        "symbol": "BTC-USD",
        "signal_type": "BUY",
        "price": 60000.0,
        "execution_time": 0.05,
        "subscribers_received": 1,
    })

    signals = list(coll.find({"symbol": "BTC-USD", "strategy_name": "TestStrategy"}))
    assert len(signals) >= 1
    assert signals[0]["strategy_name"] == "TestStrategy"
    assert signals[0]["price"] == 60000.0


def test_sqlite_adapter_filters_counts_deletes_and_rejects_unknown_fields(tmp_path):
    """Collection filters remain scoped and invalid fields never widen a query."""
    from app.database.sqlite_adapter import SQLiteCollectionAdapter

    adapter = SQLiteCollectionAdapter("signals_log")
    adapter.db = SQLiteDatabase(db_path=tmp_path / "adapter.db")
    for symbol in ("BTC-USD", "ETH-USD", "BTC-USD"):
        adapter.insert_one({
            "strategy_name": "TestStrategy",
            "symbol": symbol,
            "signal_type": "BUY",
            "price": 100.0,
            "execution_time": 0.01,
        })

    assert adapter.count_documents({"symbol": "BTC-USD"}) == 2
    assert adapter.delete_many({"symbol": "ETH-USD"}).deleted_count == 1
    assert adapter.count_documents() == 2
    with pytest.raises(ValueError, match="Unsupported field"):
        list(adapter.find({"unknown": "value"}))
    with pytest.raises(ValueError, match="Unsupported field"):
        list(adapter.find().sort("unknown"))


def test_sqlite_adapter_replace_and_upsert_semantics(tmp_path):
    """Replacement respects the filter and reports matches and upserts."""
    from app.database.sqlite_adapter import SQLiteCollectionAdapter

    adapter = SQLiteCollectionAdapter("broker_accounts")
    adapter.db = SQLiteDatabase(db_path=tmp_path / "replace.db")
    account = {
        "_id": "Strategy::BTC-USD",
        "strategy_name": "Strategy",
        "symbol": "BTC-USD",
        "capital": 100.0,
    }
    adapter.insert_one(account)

    result = adapter.replace_one(
        {"_id": account["_id"]},
        {**account, "capital": 125.0},
    )
    assert result.matched_count == 1
    assert adapter.find_one({"_id": account["_id"]})["capital"] == 125.0

    missing = adapter.replace_one(
        {"_id": "Strategy::ETH-USD"},
        {**account, "_id": "Strategy::ETH-USD", "symbol": "ETH-USD"},
    )
    assert missing.matched_count == 0
    assert adapter.find_one({"_id": "Strategy::ETH-USD"}) is None

    upsert = adapter.replace_one(
        {"_id": "Strategy::ETH-USD"},
        {**account, "_id": "Strategy::ETH-USD", "symbol": "ETH-USD"},
        upsert=True,
    )
    assert upsert.upserted_id == "Strategy::ETH-USD"


def test_get_symbol_results_filters_nested_batch_payload(monkeypatch, tmp_path):
    """Symbol history filters the nested results instead of a nonexistent SQL column."""
    from app.database import sqlite_adapter

    db = SQLiteDatabase(db_path=tmp_path / "batches.db")
    monkeypatch.setattr(sqlite_adapter, "get_sqlite_db", lambda: db)
    adapter = sqlite_adapter.SQLiteCollectionAdapter("batch_results")
    adapter.insert_one({"_id": "btc", "results": [{"symbol": "BTC-USD"}], "total_results": 1})
    adapter.insert_one({"_id": "eth", "results": [{"symbol": "ETH-USD"}], "total_results": 1})

    batches = sqlite_adapter.get_symbol_results("BTC-USD")
    assert [batch["_id"] for batch in batches] == ["btc"]
