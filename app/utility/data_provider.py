"""Provider-neutral market data facade used by strategies and tasks.

To switch from Delta Exchange to another provider, set the ``DATA_PROVIDER``
environment variable (e.g. ``DATA_PROVIDER=angel``). No code changes required.
"""

import pandas as pd

from app.data_providers.factory import get_data_provider


def fetch_historical_data(
    symbol: str,
    period: int = 30,
    interval: str = "15m",
    ttl: int | None = None,
) -> pd.DataFrame:
    """Fetch historical OHLCV data via the configured data provider.

    This function delegates to whichever provider is selected by the
    ``DATA_PROVIDER`` environment variable (defaults to ``"delta"``).

    Args:
        symbol: Instrument identifier (e.g. "ETHUSD", "BTCUSD").
        period: Lookback window in days.
        interval: Candle resolution (e.g. "15m", "1h", "1d").
        ttl: Optional cache TTL override in seconds.

    Returns:
        DataFrame with OHLCV data and technical indicators.
    """
    provider = get_data_provider()
    return provider.fetch_historical_data(
        symbol=symbol, period=period, interval=interval, ttl=ttl
    )
