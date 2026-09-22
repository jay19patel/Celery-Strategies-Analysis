"""Delta Exchange India broker module (ported from Trade-Buddy-Broker).

Provides REST client, WebSocket streaming, safe trade calculation, and event listeners.
"""

from app.broker.delta.calculator import TradeCalculator
from app.broker.delta.client import (
    BalanceError,
    DeltaAPIError,
    DeltaClient,
    OrderPlacementError,
    PositionError,
)
from app.broker.delta.price_feed import (
    DeltaLivePriceFeed,
    get_all_live_prices,
    get_all_live_tickers,
    get_delta_price_feed,
    get_live_price,
    get_live_ticker,
    get_price_history,
)
from app.broker.delta.websocket import DeltaWebSocketClient

__all__ = [
    "BalanceError",
    "DeltaAPIError",
    "DeltaClient",
    "DeltaLivePriceFeed",
    "DeltaWebSocketClient",
    "OrderPlacementError",
    "PositionError",
    "TradeCalculator",
    "get_all_live_prices",
    "get_all_live_tickers",
    "get_delta_price_feed",
    "get_live_price",
    "get_live_ticker",
    "get_price_history",
]

