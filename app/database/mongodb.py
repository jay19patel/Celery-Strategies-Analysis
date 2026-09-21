"""Legacy compatibility module.

Re-exports SQLite collection adapters from app.database.sqlite_adapter.
All new code should import directly from app.database.sqlite_adapter.
"""

from app.database.sqlite_adapter import (
    DatabaseConnection,
    MongoDBConnection,
    SQLiteCollectionAdapter,
    SQLiteCursor,
    get_collection,
    get_database,
    get_latest_batch_results,
    get_symbol_results,
    save_batch_results,
)

__all__ = [
    "DatabaseConnection",
    "MongoDBConnection",
    "SQLiteCollectionAdapter",
    "SQLiteCursor",
    "get_collection",
    "get_database",
    "get_latest_batch_results",
    "get_symbol_results",
    "save_batch_results",
]