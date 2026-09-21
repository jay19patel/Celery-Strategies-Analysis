"""Data providers package — pluggable exchange/broker data sources.

This package provides an abstract interface (BaseDataProvider) for fetching
historical OHLCV data from any exchange. New data sources are added by
subclassing BaseDataProvider and registering them in the provider factory.

Available providers:
    - DeltaExchangeProvider: Crypto data from Delta Exchange India API
"""

from app.data_providers.base_provider import BaseDataProvider
from app.data_providers.factory import get_data_provider

__all__ = [
    "BaseDataProvider",
    "get_data_provider",
]
