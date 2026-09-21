"""Backward compatibility module re-exporting DeltaWebSocketClient from app.broker.delta."""

from app.broker.delta.websocket import DeltaWebSocketClient

__all__ = ["DeltaWebSocketClient"]
