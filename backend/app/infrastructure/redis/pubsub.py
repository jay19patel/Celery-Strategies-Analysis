"""Redis PubSub — broadcast events to all connected WebSocket clients.

The engine, feed, and workers publish events to a Redis channel.
The WebSocket handler subscribes and forwards to browsers. This
decouples producers from consumers and works across processes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# The single channel all real-time events flow through
EVENTS_CHANNEL = "tb:events"


class EventPublisher:
    """Publishes domain events to Redis for WebSocket broadcast."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def publish(self, event_type: str, data: dict[str, Any]) -> int:
        """Publish an event. Returns the number of subscribers that received it."""
        message = json.dumps({"type": event_type, "data": data}, default=str)
        count = await self._redis.publish(EVENTS_CHANNEL, message)
        return count

    async def publish_event(self, event: Any) -> int:
        """Publish a DomainEvent object directly."""
        if hasattr(event, "to_dict"):
            d = event.to_dict()
            return await self.publish(d.get("type", "unknown"), d)
        return 0


class EventSubscriber:
    """Subscribes to the Redis events channel.

    Used by the WebSocket connection manager to relay events
    to all connected browser clients.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis
        self._pubsub: aioredis.client.PubSub | None = None

    async def subscribe(self) -> None:
        """Start listening to the events channel."""
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(EVENTS_CHANNEL)
        logger.info("pubsub_subscribed channel=%s", EVENTS_CHANNEL)

    async def unsubscribe(self) -> None:
        """Stop listening and clean up."""
        if self._pubsub:
            await self._pubsub.unsubscribe(EVENTS_CHANNEL)
            await self._pubsub.aclose()
            self._pubsub = None

    async def listen(self):
        """Async generator yielding parsed event messages.

        Yields dicts of shape: {"type": "...", "data": {...}}
        Skips subscription confirmation messages and parse errors.
        """
        if not self._pubsub:
            raise RuntimeError("Not subscribed — call subscribe() first")

        async for message in self._pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                yield json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                logger.warning("pubsub_bad_message data=%s", message.get("data"))
                continue
