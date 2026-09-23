"""Lightweight ZeroMQ event bus inspired by OpenAlgo architecture.

Distributes market telemetry, health metrics, and order events across
processes without blocking API request threads or locking SQLite databases.
"""

import json
import logging
import threading
from typing import Any

try:
    import zmq
except ImportError:
    zmq = None

logger = logging.getLogger(__name__)

ZMQ_TELEMETRY_PORT = 5557


class ZeroMQEventBus:
    """Non-blocking ZeroMQ PUB-SUB event broadcaster."""

    def __init__(self, telemetry_port: int = ZMQ_TELEMETRY_PORT) -> None:
        self.telemetry_port = telemetry_port
        self._ctx: Any = None
        self._pub_socket: Any = None
        self._lock = threading.Lock()
        self._latest_snapshot: dict[str, Any] = {}
        self._is_initialized = False
        self._published_count: int = 0
        self._last_published_at: str | None = None

    def start_publisher(self) -> None:
        """Bind publisher socket to localhost."""
        if zmq is None:
            logger.info("ZeroMQ (pyzmq) not available. Running in lightweight in-memory event bus mode.")
            return

        with self._lock:
            if self._is_initialized:
                return
            try:
                self._ctx = zmq.Context.instance()
                self._pub_socket = self._ctx.socket(zmq.PUB)
                self._pub_socket.bind(f"tcp://127.0.0.1:{self.telemetry_port}")
                self._is_initialized = True
                logger.info("zeromq_publisher_bound port=%s", self.telemetry_port)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not bind ZeroMQ publisher on port %s: %s", self.telemetry_port, exc)
                self._pub_socket = None

    def publish(self, topic: str, data: dict[str, Any]) -> None:
        """Publish an event to all subscribers non-blockingly."""
        from datetime import UTC, datetime

        self._latest_snapshot[topic] = data
        self._published_count += 1
        self._last_published_at = datetime.now(UTC).isoformat()
        if not self._pub_socket or zmq is None:
            return

        try:
            payload = json.dumps(data)
            with self._lock:
                self._pub_socket.send_multipart([topic.encode("utf-8"), payload.encode("utf-8")], flags=zmq.NOBLOCK)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ZeroMQ publish non-critical error: %s", exc)

    def get_latest(self, topic: str) -> dict[str, Any] | None:
        """Get the latest cached snapshot for a topic instantly without querying OS/DB."""
        return self._latest_snapshot.get(topic)

    def get_status(self) -> dict[str, Any]:
        """Get live health and telemetry status of the ZeroMQ event bus."""
        return {
            "status": "pass" if self._is_initialized else ("fallback" if zmq is None else "idle"),
            "port": self.telemetry_port,
            "mode": "ZeroMQ PUB/SUB" if self._is_initialized else "In-Memory EventBus",
            "is_bound": self._is_initialized,
            "packets_published": self._published_count,
            "last_published_at": self._last_published_at,
            "topics": list(self._latest_snapshot.keys()),
            "cached_topics_count": len(self._latest_snapshot),
        }


_event_bus: ZeroMQEventBus | None = None


def get_event_bus() -> ZeroMQEventBus:
    """Singleton accessor for ZeroMQEventBus."""
    global _event_bus
    if _event_bus is None:
        _event_bus = ZeroMQEventBus()
        _event_bus.start_publisher()
    return _event_bus
