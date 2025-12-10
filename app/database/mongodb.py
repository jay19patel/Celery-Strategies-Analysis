from pymongo import MongoClient
import threading
from pymongo.errors import ConnectionFailure
from typing import Dict, Any, Optional
from datetime import datetime
from app.core.settings import settings
from app.core.logger import get_mongodb_logger

logger = get_mongodb_logger()


class MongoDBConnection:
    """
    Singleton MongoDB connection manager
    Ensures only one connection is created per process
    """
    _instance: Optional['MongoDBConnection'] = None
    _client: Optional[MongoClient] = None
    _db = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize connection manager (Lazy Load)"""
        # Do not connect here to avoid fork safety issues with Celery
        pass

    def get_database(self):
        """Get database instance, connecting if necessary"""
        if self._db is None:
            with threading.Lock():
                 # Double-check locking pattern
                if self._db is None:
                    self._connect()
        return self._db

    def get_collection(self, collection_name: str):
         """Get collection instance"""
         return self.get_database()[collection_name]

    def _connect(self):
        """Establish MongoDB connection and setup indexes"""
        try:
            logger.info("🔌 Initializing MongoDB connection...")
            
            self._client = MongoClient(
                settings.mongodb_uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                maxPoolSize=50,
                minPoolSize=10
            )
            
            # Test connection
            self._client.admin.command('ping')
            
            # Get database
            db_name = settings.mongodb_uri.split('/')[-1].split('?')[0] or 'stockanalysis'
            self._db = self._client[db_name]
            
            # Create indexes only once
            self._setup_indexes()
            
            logger.info(f"✅ MongoDB connected successfully | Database: {db_name}")
            
        except ConnectionFailure as e:
            logger.error(f"❌ MongoDB connection failed: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"❌ MongoDB initialization error: {str(e)}")
            raise

    def _setup_indexes(self):
        """Create necessary indexes for optimal performance"""
        try:
            collection = self._db['batch_results']
            
            # Index on timestamp for time-based queries
            collection.create_index([('timestamp', -1)], background=True)
            
            # Index on batch execution metadata
            collection.create_index([('summary.total_symbols', 1)], background=True)
            
            # Compound index for symbol-based queries
            collection.create_index([
                ('results.symbol', 1),
                ('timestamp', -1)
            ], background=True)
            
            logger.info("✅ MongoDB indexes created successfully")
            
        except Exception as e:
            logger.error(f"⚠️  Error creating indexes: {str(e)}")
            # Don't fail on index creation errors



    def close(self):
        """Close MongoDB connection"""
        if self._client:
            self._client.close()
            logger.info("🔌 MongoDB connection closed")


# Global singleton instance
_mongo_connection = MongoDBConnection()


def get_database():
    """Get MongoDB database instance"""
    return _mongo_connection.get_database()


def get_collection(collection_name: str):
    """Get MongoDB collection instance"""
    return _mongo_connection.get_collection(collection_name)


def save_batch_results(batch_data: Dict[str, Any]):
    """
    Save batch execution results to MongoDB
    
    Args:
        batch_data: Dictionary containing batch results and summary
        
    Returns:
        ObjectId of inserted document
    """
    try:
        collection = get_collection('batch_results')
        
        # Add metadata
        document = {
            **batch_data,
            "timestamp": datetime.utcnow(),
            "stored_at": datetime.utcnow().isoformat()
        }
        
        # Insert document
        result = collection.insert_one(document)
        
        logger.info(
            f"💾 Batch saved to MongoDB | "
            f"ID: {result.inserted_id} | "
            f"Symbols: {batch_data.get('summary', {}).get('total_symbols')} | "
            f"Results: {batch_data.get('summary', {}).get('total_results')}"
        )
        
        return result.inserted_id
        
    except Exception as e:
        logger.error(f"❌ Error saving batch to MongoDB: {str(e)}", exc_info=True)
        raise


def get_latest_batch_results(limit: int = 10):
    """
    Retrieve latest batch results from MongoDB
    
    Args:
        limit: Maximum number of results to return
        
    Returns:
        List of batch result documents
    """
    try:
        collection = get_collection('batch_results')
        
        results = list(
            collection.find()
            .sort('timestamp', -1)
            .limit(limit)
        )
        
        logger.info(f"📥 Retrieved {len(results)} batch results from MongoDB")
        
        return results
        
    except Exception as e:
        logger.error(f"❌ Error retrieving batches from MongoDB: {str(e)}")
        raise


def get_symbol_results(symbol: str, limit: int = 10):
    """
    Retrieve results for a specific symbol
    
    Args:
        symbol: Stock/crypto symbol to query
        limit: Maximum number of results to return
        
    Returns:
        List of results for the symbol
    """
    try:
        collection = get_collection('batch_results')
        
        results = list(
            collection.find({'results.symbol': symbol})
            .sort('timestamp', -1)
            .limit(limit)
        )
        
        logger.info(f"📥 Retrieved {len(results)} results for symbol: {symbol}")
        
        return results
        
    except Exception as e:
        logger.error(f"❌ Error retrieving results for {symbol}: {str(e)}")
        raise