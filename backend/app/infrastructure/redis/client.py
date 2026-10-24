"""Redis client — connection lifecycle and health check.

One async Redis connection is shared across the whole app.
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


async def init_redis(url: str) -> aioredis.Redis:
    """Create the shared Redis connection.

    Called once during FastAPI lifespan startup.
    """
    global _redis  # noqa: PLW0603
    _redis = aioredis.from_url(url, decode_responses=True)
    # Verify connectivity
    await _redis.ping()
    logger.info("redis_ready url=%s", url)
    return _redis


async def close_redis() -> None:
    """Gracefully close the Redis connection."""
    global _redis  # noqa: PLW0603
    if _redis:
        await _redis.aclose()
        _redis = None
        logger.info("redis_closed")


def get_redis() -> aioredis.Redis:
    """Return the shared Redis connection. Crashes if not initialised."""
    if _redis is None:
        raise RuntimeError("Redis not initialised — call init_redis() first")
    return _redis
