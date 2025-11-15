"""
Redis pub/sub publisher for real-time strategy result broadcasting.
Publishes strategy results and batch completion events to Redis channels.
"""
import json
import redis
from typing import Any, Dict
from app.core.settings import settings
from app.core.logger import get_redis_logger

logger = get_redis_logger()


class RedisPublisher:
    """Singleton Redis publisher for pub/sub functionality."""
    _instance: 'RedisPublisher' = None
    _redis_client: redis.Redis = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._redis_client is None:
            self._connect()

    def _connect(self):
        """Establish connection to Redis for pub/sub."""
        try:
            logger.info(f"Connecting to Redis at {settings.redis_pubsub_url}")
            self._redis_client = redis.from_url(
                settings.redis_pubsub_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
            )
            # Test connection
            self._redis_client.ping()
            logger.info("Successfully connected to Redis")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {str(e)}", exc_info=True)
            raise

    @property
    def client(self) -> redis.Redis:
        """Get Redis client instance."""
        if self._redis_client is None:
            logger.debug("Redis client not initialized, reconnecting...")
            self._connect()
        return self._redis_client

    def publish(self, channel: str, message: Dict[str, Any]) -> int:
        """
        Publish message to a Redis channel.

        Args:
            channel: Redis channel name
            message: Message data to publish (will be JSON serialized)

        Returns:
            Number of subscribers that received the message
        """
        try:
            logger.debug(f"Publishing message to Redis channel: {channel}")
            message_json = json.dumps(message, default=str)
            subscribers = self.client.publish(channel, message_json)
            logger.debug(f"Message published to {channel}, received by {subscribers} subscriber(s)")
            return subscribers
        except Exception as e:
            logger.error(f"Error publishing to Redis channel {channel}: {str(e)}", exc_info=True)
            return 0

    def close(self):
        """Close Redis connection."""
        if self._redis_client:
            logger.info("Closing Redis connection")
            self._redis_client.close()
            self._redis_client = None


# Global publisher instance
_publisher = RedisPublisher()


def publish_batch_complete(batch_summary: Dict[str, Any]) -> int:
    """
    Publish batch completion notification to Redis pub/sub channel.

    Args:
        batch_summary: Batch execution summary

    Returns:
        Number of subscribers that received the message
    """
    channel = settings.pubsub_channel_batch
    batch_id = batch_summary.get('batch_id', 'unknown')
    total_results = batch_summary.get('total_results', 0)
    logger.info(f"Publishing batch completion for batch_id: {batch_id} with {total_results} total results")
    return _publisher.publish(channel, {
        "type": "batch_complete",
        "data": batch_summary
    })


def get_publisher() -> RedisPublisher:
    """Get the global Redis publisher instance."""
    return _publisher
