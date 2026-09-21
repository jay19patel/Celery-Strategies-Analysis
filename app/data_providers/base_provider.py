"""Abstract base class for all data providers.

Every exchange/broker data source must implement this interface. This ensures
strategies and the batch pipeline remain agnostic to where OHLCV data comes
from — you can swap Delta Exchange for Angel One, CCXT, or a local DuckDB
without changing a single strategy file.

Inspired by OpenAlgo's broker/*/api/data.py abstraction where every broker
provides the same data API surface.
"""

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class BaseDataProvider(ABC):
    """Abstract data provider — swap in any exchange/broker.

    Subclasses must implement all abstract methods. The returned DataFrame
    must have at minimum these columns:

        Open, High, Low, Close, Volume, DateTime (as index)

    Additional indicator columns (EMA, RSI, etc.) may be added by the
    provider or left for the strategy/features layer.
    """

    def __init__(self, name: str) -> None:
        """Initialize the provider with a human-readable name.

        Args:
            name: Provider identifier for logging and selection.
        """
        self.name = name

    @abstractmethod
    def fetch_historical_data(
        self,
        symbol: str,
        period: int = 30,
        interval: str = "15m",
        ttl: int | None = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV candle data for a symbol.

        Args:
            symbol: Instrument identifier (e.g. "ETHUSD", "NIFTY", "RELIANCE").
            period: Lookback window in days.
            interval: Candle resolution (e.g. "1m", "5m", "15m", "1h", "1d").
            ttl: Optional cache TTL override in seconds.

        Returns:
            DataFrame with DateTimeIndex and at least Open/High/Low/Close/Volume.

        Raises:
            DataFetchError: When the upstream API is unreachable after retries.
        """
        ...

    @abstractmethod
    def get_supported_symbols(self) -> list[str]:
        """Return the list of symbols this provider can serve data for.

        Returns:
            List of tradable symbol strings.
        """
        ...

    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """Fetch the latest traded price for a symbol.

        Args:
            symbol: Instrument identifier.

        Returns:
            Current price as a float.

        Raises:
            DataFetchError: When the price cannot be retrieved.
        """
        ...

    @abstractmethod
    def is_market_open(self, symbol: str) -> bool:
        """Check whether the market for a symbol is currently open.

        For 24/7 crypto markets, this should always return True.
        For equity markets, this should check trading hours and holidays.

        Args:
            symbol: Instrument identifier.

        Returns:
            True if the market is currently accepting orders.
        """
        ...

    def get_provider_info(self) -> dict[str, Any]:
        """Return metadata about this provider for health checks and dashboards.

        Returns:
            Dictionary with at least 'name', 'status', and 'supported_symbols_count'.
        """
        return {
            "name": self.name,
            "status": "active",
            "supported_symbols_count": len(self.get_supported_symbols()),
        }


class DataFetchError(Exception):
    """Raised when a data provider cannot retrieve the requested data."""

    def __init__(self, provider_name: str, symbol: str, message: str) -> None:
        self.provider_name = provider_name
        self.symbol = symbol
        super().__init__(
            f"[{provider_name}] Failed to fetch data for {symbol}: {message}"
        )
