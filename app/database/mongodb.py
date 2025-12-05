"""
MongoDB connection and data persistence layer.
Handles storage of strategy results and batch execution data.
"""
from typing import Any, Dict, Optional
from pymongo import MongoClient, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from app.core.settings import settings
from app.core.logger import get_mongodb_logger

logger = get_mongodb_logger()


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
        try:
            logger.info(f"Connecting to MongoDB at {settings.mongodb_uri}")
            self._client = MongoClient(
                settings.mongodb_uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )
            # Extract database name from URL or use default
            db_name = settings.mongodb_url.split('/')[-1].split('?')[0] or "TBStockanAlysis"
            self._db = self._client[db_name]
            logger.info(f"Connected to MongoDB database: {db_name}")
            # Create indexes for better query performance
            self._create_indexes()
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {str(e)}", exc_info=True)
            raise

    def _create_indexes(self):
        """Create indexes on collections for optimal query performance."""
        try:
            logger.debug("Creating indexes on collections")
            # Batch results indexes
            batch_collection = self._db.batch_results
            batch_collection.create_index([("created_at", DESCENDING)])
            logger.debug("Indexes created successfully")
        except Exception as e:
            logger.warning(f"Error creating indexes: {str(e)}")

    @property
    def db(self) -> Database:
        """Get database instance."""
        if self._db is None:
            logger.debug("MongoDB database not initialized, reconnecting...")
            self._connect()
        return self._db

    def get_collection(self, collection_name: str) -> Collection:
        """Get a specific collection."""
        return self.db[collection_name]

    def close(self):
        """Close MongoDB connection."""
        if self._client:
            logger.info("Closing MongoDB connection")
            self._client.close()
            self._client = None
            self._db = None


# Global connection instance
_mongo = MongoDBConnection()


def get_db() -> Database:
    """Get MongoDB database instance."""
    return _mongo.db


def save_batch_results(batch_data: Dict[str, Any]) -> Any:
    """
    Save batch execution results to MongoDB.

    Args:
        batch_data: Dictionary containing batch execution summary and results

    Returns:
        ObjectId of the inserted document
    """
    try:
        logger.info("Saving batch results to MongoDB")
        collection = get_db().batch_results

        # Add metadata - Convert UTC to IST (Indian Standard Time)
        ist_timezone = ZoneInfo("Asia/Kolkata")
        batch_data["created_at"] = datetime.now(timezone.utc).astimezone(ist_timezone)
        batch_data["total_results"] = len(batch_data.get("results", []))
        
        logger.debug(f"Saving batch with {batch_data['total_results']} results")

        result = collection.insert_one(batch_data)
        logger.info(f"Successfully saved batch results to MongoDB with ID: {result.inserted_id}")
        return result.inserted_id
    except Exception as e:
        logger.error(f"Error saving batch results to MongoDB: {str(e)}", exc_info=True)
        raise


def get_latest_batch_results(limit: int = 10) -> list:
    """
    Retrieve latest batch execution results.

    Args:
        limit: Maximum number of batch results to return

    Returns:
        List of batch result documents
    """
    try:
        logger.debug(f"Retrieving latest {limit} batch results from MongoDB")
        collection = get_db().batch_results
        results = collection.find().sort("created_at", DESCENDING).limit(limit)
        results_list = list(results)
        logger.debug(f"Retrieved {len(results_list)} batch results from MongoDB")
        return results_list
    except Exception as e:
        logger.error(f"Error retrieving batch results from MongoDB: {str(e)}", exc_info=True)
        raise


