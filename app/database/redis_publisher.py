import redis
import json
import os
import threading
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from app.core.settings import settings
from app.core.logger import get_redis_logger

logger = get_redis_logger()


class RedisPublisher:
    """
    Fork-safe Redis publisher for pub/sub messaging.
    Ensures only one connection per process by tracking PID.
    """
    _lock = threading.Lock()
    _client: Optional[redis.Redis] = None
    _pid: Optional[int] = None

    @classmethod
    def get_client(cls) -> redis.Redis:
        """Get Redis client instance, reconnecting if we're in a forked process."""
        current_pid = os.getpid()
        if cls._client is None or cls._pid != current_pid:
            with cls._lock:
                if cls._client is None or cls._pid != current_pid:
                    cls._connect(current_pid)
        return cls._client

    @classmethod
    def _connect(cls, pid: int):
        """Establish Redis connection"""
        try:
            logger.info("🔌 Initializing Redis Pub/Sub connection...")
            
            # Close stale connection from parent process if any
            if cls._client is not None:
                try:
                    cls._client.close()
                except Exception:
                    pass

            cls._client = redis.from_url(
                settings.redis_pubsub_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30
            )

            # Test connection
            cls._client.ping()
            cls._pid = pid
            
            logger.info(f"✅ Redis Pub/Sub connected successfully | PID: {pid}")

        except redis.ConnectionError as e:
            logger.error(f"❌ Redis connection failed: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"❌ Redis initialization error: {str(e)}")
            raise

    @classmethod
    def publish(cls, channel: str, message: Dict[str, Any]) -> int:
        """
        Publish message to Redis channel
        """
        try:
            # Add metadata
            message_with_meta = {
                **message,
                "published_at": datetime.now(timezone.utc).isoformat(),
                "channel": channel
            }

            # Serialize to JSON
            json_message = json.dumps(message_with_meta, default=str)

            # Publish
            client = cls.get_client()
            subscriber_count = client.publish(channel, json_message)

            logger.info(
                f"📡 Published to '{channel}' | "
                f"Subscribers: {subscriber_count} | "
                f"Size: {len(json_message)} bytes"
            )

            return subscriber_count

        except Exception as e:
            logger.error(f"❌ Error publishing to '{channel}': {str(e)}", exc_info=True)
            raise

    @classmethod
    def close(cls):
        """Close Redis connection"""
        if cls._client:
            cls._client.close()
            cls._client = None
            cls._pid = None
            logger.info("🔌 Redis connection closed")


def get_redis_client() -> redis.Redis:
    """Get Redis client instance"""
    return RedisPublisher.get_client()


def publish_message(channel: str, message: Dict[str, Any]) -> int:
    """
    Publish message to Redis channel
    """
    return RedisPublisher.publish(channel, message)


def publish_batch_complete(batch_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Publish batch completion notification
    """
    try:
        channel = settings.pubsub_channel_batch
        
        subscriber_count = publish_message(channel, batch_data)
        
        return {
            "channel": channel,
            "subscriber_count": subscriber_count,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "status": "success"
        }
        
    except Exception as e:
        logger.error(f"❌ Error publishing batch complete: {str(e)}")
        return {
            "channel": settings.pubsub_channel_batch,
            "subscriber_count": 0,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "error": str(e)
        }