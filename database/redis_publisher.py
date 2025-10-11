"""
Redis pub/sub publisher for real-time strategy result broadcasting.
Publishes strategy results and batch completion events to Redis channels.
"""
import json
import redis
from typing import Any, Dict
from core.settings import settings


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
        self._redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_pubsub_db,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
        # Test connection
        self._redis_client.ping()

    @property
    def client(self) -> redis.Redis:
        """Get Redis client instance."""
        if self._redis_client is None:
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
            message_json = json.dumps(message, default=str)
            return self.client.publish(channel, message_json)
        except Exception as e:
            print(f"Error publishing to Redis channel {channel}: {e}")
            return 0

    def close(self):
        """Close Redis connection."""
        if self._redis_client:
            self._redis_client.close()
            self._redis_client = None


# Global publisher instance
_publisher = RedisPublisher()


def publish_strategy_result(result_data: Dict[str, Any]) -> int:
    """
    Publish individual strategy result to Redis pub/sub channel.

    Args:
        result_data: Strategy execution result

    Returns:
        Number of subscribers that received the message
    """
    channel = settings.pubsub_channel_strategy
    return _publisher.publish(channel, {
        "type": "strategy_result",
        "data": result_data
    })


def publish_batch_complete(batch_summary: Dict[str, Any]) -> int:
    """
    Publish batch completion notification to Redis pub/sub channel.

    Args:
        batch_summary: Batch execution summary

    Returns:
        Number of subscribers that received the message
    """
    channel = settings.pubsub_channel_batch
    return _publisher.publish(channel, {
        "type": "batch_complete",
        "data": batch_summary
    })


def get_publisher() -> RedisPublisher:
    """Get the global Redis publisher instance."""
    return _publisher
