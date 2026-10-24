"""Redis cache — price book, session tokens, and short-lived data.

Prices are cached with a TTL so stale quotes are never served.
Session tokens are stored with the same expiry as the cookie.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Key prefixes
PRICE_PREFIX = "tb:price:"
SESSION_PREFIX = "tb:session:"
CACHE_PREFIX = "tb:cache:"

# Default TTLs
PRICE_TTL_SECONDS = 30
SESSION_TTL_SECONDS = 86400  # 24 hours


class RedisCache:
    """Thin caching layer over Redis with typed helpers."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    # ── Prices ────────────────────────────────────────────────────────

    async def set_price(self, symbol: str, price: float) -> None:
        """Cache a live price with TTL."""
        await self._redis.set(
            f"{PRICE_PREFIX}{symbol}",
            str(price),
            ex=PRICE_TTL_SECONDS,
        )

    async def get_price(self, symbol: str) -> float | None:
        """Fetch a cached price. Returns None if expired or absent."""
        value = await self._redis.get(f"{PRICE_PREFIX}{symbol}")
        return float(value) if value is not None else None

    async def get_all_prices(self) -> dict[str, float]:
        """Scan all cached prices."""
        prices: dict[str, float] = {}
        async for key in self._redis.scan_iter(f"{PRICE_PREFIX}*"):
            symbol = key.replace(PRICE_PREFIX, "")
            value = await self._redis.get(key)
            if value is not None:
                prices[symbol] = float(value)
        return prices

    # ── Sessions ──────────────────────────────────────────────────────

    async def set_session(self, token: str, data: dict[str, Any]) -> None:
        """Store a session token with data and TTL."""
        await self._redis.set(
            f"{SESSION_PREFIX}{token}",
            json.dumps(data),
            ex=SESSION_TTL_SECONDS,
        )

    async def get_session(self, token: str) -> dict[str, Any] | None:
        """Retrieve session data. Returns None if expired."""
        value = await self._redis.get(f"{SESSION_PREFIX}{token}")
        if value is None:
            return None
        return json.loads(value)

    async def delete_session(self, token: str) -> None:
        """Invalidate a session."""
        await self._redis.delete(f"{SESSION_PREFIX}{token}")

    # ── Generic Cache ─────────────────────────────────────────────────

    async def set_cached(self, key: str, data: Any, ttl: int = 60) -> None:
        """Cache arbitrary JSON-serialisable data."""
        await self._redis.set(
            f"{CACHE_PREFIX}{key}",
            json.dumps(data, default=str),
            ex=ttl,
        )

    async def get_cached(self, key: str) -> Any | None:
        """Retrieve cached data. Returns None if absent/expired."""
        value = await self._redis.get(f"{CACHE_PREFIX}{key}")
        if value is None:
            return None
        return json.loads(value)
