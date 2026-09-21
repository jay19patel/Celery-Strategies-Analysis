"""Backward compatibility module re-exporting TradeCalculator from app.broker.delta."""

from app.broker.delta.calculator import TradeCalculator

__all__ = ["TradeCalculator"]
