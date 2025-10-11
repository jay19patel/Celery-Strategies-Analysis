"""
MongoDB connection and data persistence layer.
Handles storage of strategy results and batch execution data.
"""
from typing import Any, Dict, Optional
from pymongo import MongoClient, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database
from datetime import datetime, timezone
from core.settings import settings


class MongoDBConnection:
    """Singleton MongoDB connection manager."""
    _instance: Optional['MongoDBConnection'] = None
    _client: Optional[MongoClient] = None
    _db: Optional[Database] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._client is None:
            self._connect()

    def _connect(self):
        """Establish connection to MongoDB."""
        self._client = MongoClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
        )
        self._db = self._client[settings.mongodb_database]
        # Create indexes for better query performance
        self._create_indexes()

    def _create_indexes(self):
        """Create indexes on collections for optimal query performance."""
        # Strategy results indexes
        strategy_collection = self._db.strategy_results
        strategy_collection.create_index([("symbol", 1), ("timestamp", DESCENDING)])
        strategy_collection.create_index([("strategy_name", 1), ("timestamp", DESCENDING)])
        strategy_collection.create_index([("timestamp", DESCENDING)])

        # Batch results indexes
        batch_collection = self._db.batch_results
        batch_collection.create_index([("created_at", DESCENDING)])

    @property
    def db(self) -> Database:
        """Get database instance."""
        if self._db is None:
            self._connect()
        return self._db

    def get_collection(self, collection_name: str) -> Collection:
        """Get a specific collection."""
        return self.db[collection_name]

    def close(self):
        """Close MongoDB connection."""
        if self._client:
            self._client.close()
            self._client = None
            self._db = None


# Global connection instance
_mongo = MongoDBConnection()


def get_db() -> Database:
    """Get MongoDB database instance."""
    return _mongo.db


def save_strategy_result(result_data: Dict[str, Any]) -> Any:
    """
    Save individual strategy result to MongoDB.

    Args:
        result_data: Dictionary containing strategy execution result

    Returns:
        ObjectId of the inserted document
    """
    collection = get_db().strategy_results

    # Ensure timestamp is present
    if "timestamp" not in result_data:
        result_data["timestamp"] = datetime.now(timezone.utc).isoformat()

    # Add created_at for tracking
    result_data["created_at"] = datetime.now(timezone.utc)

    result = collection.insert_one(result_data)
    return result.inserted_id


def save_batch_results(batch_data: Dict[str, Any]) -> Any:
    """
    Save batch execution results to MongoDB.

    Args:
        batch_data: Dictionary containing batch execution summary and results

    Returns:
        ObjectId of the inserted document
    """
    collection = get_db().batch_results

    # Add metadata
    batch_data["created_at"] = datetime.now(timezone.utc)
    batch_data["total_results"] = len(batch_data.get("results", []))

    result = collection.insert_one(batch_data)
    return result.inserted_id


def get_latest_strategy_results(symbol: Optional[str] = None, limit: int = 100) -> list:
    """
    Retrieve latest strategy results from MongoDB.

    Args:
        symbol: Optional symbol to filter results
        limit: Maximum number of results to return

    Returns:
        List of strategy result documents
    """
    collection = get_db().strategy_results

    query = {}
    if symbol:
        query["symbol"] = symbol

    results = collection.find(query).sort("timestamp", DESCENDING).limit(limit)
    return list(results)


def get_latest_batch_results(limit: int = 10) -> list:
    """
    Retrieve latest batch execution results.

    Args:
        limit: Maximum number of batch results to return

    Returns:
        List of batch result documents
    """
    collection = get_db().batch_results
    results = collection.find().sort("created_at", DESCENDING).limit(limit)
    return list(results)


def get_strategy_results_by_date_range(start_date: datetime, end_date: datetime, symbol: Optional[str] = None) -> list:
    """
    Get strategy results within a date range.

    Args:
        start_date: Start datetime
        end_date: End datetime
        symbol: Optional symbol filter

    Returns:
        List of strategy result documents
    """
    collection = get_db().strategy_results

    query = {
        "created_at": {
            "$gte": start_date,
            "$lte": end_date
        }
    }

    if symbol:
        query["symbol"] = symbol

    results = collection.find(query).sort("created_at", DESCENDING)
    return list(results)
