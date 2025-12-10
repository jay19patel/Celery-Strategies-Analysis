import redis
import json
from typing import Dict, Any, Optional
from datetime import datetime
from app.core.settings import settings
from app.core.logger import get_redis_logger

logger = get_redis_logger()


class RedisPublisher:
    """
    Singleton Redis publisher for pub/sub messaging
    Ensures only one connection per process
    """
    _instance: Optional['RedisPublisher'] = None
    _client: Optional[redis.Redis] = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize Redis connection only once"""
        if not self._initialized:
            self._connect()
            RedisPublisher._initialized = True

    def _connect(self):
        """Establish Redis connection"""
        try:
            logger.info("🔌 Initializing Redis Pub/Sub connection...")
            
            self._client = redis.from_url(
                settings.redis_pubsub_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30
            )
            
            # Test connection
            self._client.ping()
            
            logger.info("✅ Redis Pub/Sub connected successfully")
            
        except redis.ConnectionError as e:
            logger.error(f"❌ Redis connection failed: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"❌ Redis initialization error: {str(e)}")
            raise

    def publish(self, channel: str, message: Dict[str, Any]) -> int:
        """
        Publish message to Redis channel
        
        Args:
            channel: Channel name
            message: Message data (will be JSON serialized)
            
        Returns:
            Number of subscribers that received the message
        """
        try:
            # Add metadata
            message_with_meta = {
                **message,
                "published_at": datetime.utcnow().isoformat(),
                "channel": channel
            }
            
            # Serialize to JSON
            json_message = json.dumps(message_with_meta, default=str)
            
            # Publish
            subscriber_count = self._client.publish(channel, json_message)
            
            logger.info(
                f"📡 Published to '{channel}' | "
                f"Subscribers: {subscriber_count} | "
                f"Size: {len(json_message)} bytes"
            )
            
            return subscriber_count
            
        except Exception as e:
            logger.error(f"❌ Error publishing to '{channel}': {str(e)}", exc_info=True)
            raise

    def get_client(self) -> redis.Redis:
        """Get Redis client instance"""
        if self._client is None:
            raise RuntimeError("Redis not initialized")
        return self._client

    def close(self):
        """Close Redis connection"""
        if self._client:
            self._client.close()
            logger.info("🔌 Redis connection closed")


# Global singleton instance
_redis_publisher = RedisPublisher()


def get_redis_client() -> redis.Redis:
    """Get Redis client instance"""
    return _redis_publisher.get_client()


def publish_message(channel: str, message: Dict[str, Any]) -> int:
    """
    Publish message to Redis channel
    
    Args:
        channel: Channel name
        message: Message data
        
    Returns:
        Number of subscribers that received the message
    """
    return _redis_publisher.publish(channel, message)


def publish_batch_complete(batch_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Publish batch completion notification
    
    Args:
        batch_data: Batch execution data
        
    Returns:
        Dictionary with publish details
    """
    try:
        channel = settings.pubsub_channel_batch
        
        subscriber_count = publish_message(channel, batch_data)
        
        return {
            "channel": channel,
            "subscriber_count": subscriber_count,
            "published_at": datetime.utcnow().isoformat(),
            "status": "success"
        }
        
    except Exception as e:
        logger.error(f"❌ Error publishing batch complete: {str(e)}")
        return {
            "channel": settings.pubsub_channel_batch,
            "subscriber_count": 0,
            "published_at": datetime.utcnow().isoformat(),
            "status": "failed",
            "error": str(e)
        }