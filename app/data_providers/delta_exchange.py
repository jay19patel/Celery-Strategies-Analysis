"""Delta Exchange data provider — refactored from app/utility/data_provider.py.

This provider fetches OHLCV crypto data from Delta Exchange India's public API
and adds basic technical indicators. The original monolithic
``fetch_historical_data`` function has been wrapped inside a BaseDataProvider
subclass so the rest of the system can swap providers via configuration.

Caching behavior (Redis DB 3, msgpack serialization) is preserved exactly
as it was. Indicator computation is also kept inline because strategies
depend on columns like 9EMA, RSI, Candle_Signal being present in the
returned DataFrame.
"""

import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pandas_ta as ta
import redis
import requests
import msgpack

from app.core.logger import get_data_provider_logger
from app.core.settings import settings
from app.data_providers.base_provider import BaseDataProvider, DataFetchError

logger = get_data_provider_logger()

# --- Redis cache (DB 3, separate from Celery) ---
_CACHE_DURATION = 120  # 2 minutes default

try:
    _base_redis_url = settings.redis_broker_url.rsplit("/", 1)[0]
    _redis_client: redis.Redis | None = redis.Redis.from_url(
        f"{_base_redis_url}/3", decode_responses=False
    )
except Exception as exc:
    logger.error(f"❌ Failed to initialize Redis cache: {exc}")
    _redis_client = None


def _get_cache_key(symbol: str, period: int, interval: str) -> str:
    """Generate a namespaced Redis cache key."""
    return f"stock_data:{symbol}:{period}:{interval}"


def _read_cache(cache_key: str) -> pd.DataFrame | None:
    """Retrieve a cached DataFrame from Redis, or None on miss/error."""
    if not _redis_client:
        return None
    try:
        raw = _redis_client.get(cache_key)
        if raw:
            data_dict = msgpack.unpackb(raw)
            df = pd.DataFrame(
                data=data_dict.get("data"),
                index=pd.to_datetime(data_dict.get("index")),
                columns=data_dict.get("columns"),
            )
            df.index.name = data_dict.get("index_name", "DateTime")
            df["DateTime"] = df.index
            return df
    except Exception as exc:
        logger.error(f"⚠️  Redis read error: {exc}")
    return None


def _write_cache(cache_key: str, data: pd.DataFrame, ttl: int | None = None) -> None:
    """Serialize and write a DataFrame to Redis with TTL."""
    if not _redis_client:
        return
    try:
        data = data.drop(columns=["DateTime"], errors="ignore")
        data_dict = data.to_dict(orient="split")
        data_dict["index"] = [
            x.isoformat() if hasattr(x, "isoformat") else str(x)
            for x in data_dict["index"]
        ]
        data_dict["index_name"] = data.index.name

        serialized = msgpack.packb(data_dict)
        expiry = ttl if ttl is not None else _CACHE_DURATION
        if expiry <= 0:
            logger.warning(f"⚠️  Skipping cache write for {cache_key}: invalid ttl={expiry}")
            return
        _redis_client.setex(cache_key, int(expiry), serialized)
    except Exception as exc:
        logger.error(f"⚠️  Redis write error: {exc}")


class DeltaExchangeProvider(BaseDataProvider):
    """Crypto OHLCV data from Delta Exchange India (api.india.delta.exchange).

    Supports all Delta Exchange perpetual/futures symbols:
    ETHUSD, BTCUSD, SOLUSD, etc.

    Caching is handled at the provider level via Redis DB 3 with configurable
    TTL per call.
    """

    _API_BASE = "https://api.india.delta.exchange/v2"
    _MAX_RETRIES = 3

    # Known perpetual symbols available on Delta Exchange India
    _SUPPORTED_SYMBOLS = [
        "ETHUSD", "BTCUSD", "SOLUSD", "BNBUSD", "XRPUSD",
        "ADAUSD", "DOTUSD", "LINKUSD", "MATICUSD", "AVAXUSD",
        "ETH-USD", "BTC-USD", "SOL-USD", "BNB-USD", "XRP-USD",
    ]

    @classmethod
    def _normalize_symbol(cls, symbol: str) -> str:
        """Convert standard pair notation (e.g. 'ETH-USD', 'BTC/USD', 'btc_usd') to Delta Exchange format ('ETHUSD')."""
        return symbol.replace("-", "").replace("/", "").replace("_", "").upper()

    def __init__(self) -> None:
        super().__init__("Delta Exchange India")

    def fetch_historical_data(
        self,
        symbol: str,
        period: int = 30,
        interval: str = "15m",
        ttl: int | None = None,
    ) -> pd.DataFrame:
        """Fetch candles from Delta Exchange with Redis caching and indicators.

        Args:
            symbol: Delta Exchange symbol (e.g. "ETHUSD" or "ETH-USD").
            period: Number of days of history.
            interval: Candle resolution.
            ttl: Cache TTL override.

        Returns:
            DataFrame with OHLCV + technical indicators.

        Raises:
            DataFetchError: After all retries are exhausted.
        """
        api_symbol = self._normalize_symbol(symbol)

        # Check cache first
        cache_key = _get_cache_key(symbol, period, interval)
        cached = _read_cache(cache_key)
        if cached is not None:
            logger.info(f"♻️  Cache HIT: {symbol} | period={period}, interval={interval}")
            return cached

        logger.info(
            f"🌐 Cache MISS: Fetching fresh data for {symbol} ({api_symbol}) | period={period}, interval={interval}"
        )

        # Determine actual API resolution
        target_interval = interval
        api_interval = interval
        if interval in ("1M", "1w", "1W"):
            api_interval = "1d"

        end_time = int(time.time())
        start_time = end_time - (period * 86400)

        params = {
            "resolution": api_interval,
            "symbol": api_symbol,
            "start": str(start_time),
            "end": str(end_time),
        }
        headers = {"Accept": "application/json"}

        df: pd.DataFrame | None = None
        last_error: str | None = None

        for attempt in range(self._MAX_RETRIES):
            try:
                logger.debug(f"API attempt {attempt + 1}/{self._MAX_RETRIES} for {symbol}")
                response = requests.get(
                    f"{self._API_BASE}/history/candles",
                    params=params,
                    headers=headers,
                    timeout=10,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("success") and len(data.get("result", [])) > 0:
                        candles = data["result"]
                        rows = [
                            {
                                "time": c["time"],
                                "Open": float(c["open"]),
                                "High": float(c["high"]),
                                "Low": float(c["low"]),
                                "Close": float(c["close"]),
                                "Volume": float(c["volume"] or 0),
                            }
                            for c in candles
                        ]

                        df = pd.DataFrame(rows)
                        df["DateTime"] = pd.to_datetime(df["time"], unit="s", utc=True)
                        df = df.sort_values("DateTime")
                        df.set_index("DateTime", inplace=True)

                        # Resample if needed
                        if target_interval in ("1M", "1w", "1W"):
                            rule = "ME" if target_interval == "1M" else "W"
                            ohlc_dict = {
                                "Open": "first",
                                "High": "max",
                                "Low": "min",
                                "Close": "last",
                                "Volume": "sum",
                                "time": "first",
                            }
                            df = df.resample(rule).agg(ohlc_dict).dropna()
                            logger.info(
                                f"🔄 Resampled 1d data to {target_interval}: {len(df)} candles"
                            )

                        df["DateTime"] = df.index
                        df["Date"] = df.index.strftime("%d/%m/%Y")
                        df["Time"] = df.index.strftime("%I:%M %p")

                        logger.info(f"✅ API fetch successful: {symbol} | {len(df)} candles")
                        break
                    else:
                        last_error = "API returned success=false or empty result"
                        logger.warning(f"⚠️  {last_error} for {symbol}")
                else:
                    last_error = f"Bad status code: {response.status_code}"
                    logger.warning(f"⚠️  {last_error} for {symbol}")

            except requests.exceptions.Timeout:
                last_error = "Request timeout"
                logger.warning(f"⚠️  Timeout on attempt {attempt + 1} for {symbol}")
            except Exception as exc:
                last_error = str(exc)
                logger.warning(f"⚠️  Error on attempt {attempt + 1} for {symbol}: {last_error}")

            if attempt < self._MAX_RETRIES - 1:
                wait_time = 2**attempt
                logger.debug(f"Waiting {wait_time}s before retry...")
                time.sleep(wait_time)

        if df is None:
            raise DataFetchError(
                self.name, symbol, f"Failed after {self._MAX_RETRIES} attempts: {last_error}"
            )

        # --- Technical indicators ---
        self._add_indicators(df)

        logger.info(f"✅ Processing complete: {symbol} | {len(df)} rows | Indicators calculated")

        # Save to cache
        _write_cache(cache_key, df, ttl)
        return df

    def get_supported_symbols(self) -> list[str]:
        """Return the list of known Delta Exchange perpetual symbols."""
        return list(self._SUPPORTED_SYMBOLS)

    def get_current_price(self, symbol: str) -> float:
        """Fetch the last traded price from Delta Exchange ticker API.

        Args:
            symbol: Delta Exchange symbol.

        Returns:
            The mark price as a float.

        Raises:
            DataFetchError: If the ticker cannot be fetched.
        """
        api_symbol = self._normalize_symbol(symbol)
        try:
            response = requests.get(
                f"{self._API_BASE}/tickers/{api_symbol}",
                headers={"Accept": "application/json"},
                timeout=5,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return float(data["result"]["mark_price"])
            raise DataFetchError(
                self.name, symbol, f"Ticker API returned status {response.status_code}"
            )
        except DataFetchError:
            raise
        except Exception as exc:
            raise DataFetchError(self.name, symbol, str(exc)) from exc

    def is_market_open(self, symbol: str) -> bool:
        """Crypto markets are 24/7 — always returns True."""
        return True

    # ------------------------------------------------------------------
    # Private: indicator computation (preserved from original data_provider.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _add_indicators(df: pd.DataFrame) -> None:
        """Calculate technical indicators matching the original data_provider.py.

        This adds EMA-9/15/50, RSI-14, candle colour, body/shadow percentages,
        and a simple candle-pattern signal (Bullish/Bearish/Neutral).
        """
        # EMA
        for ema_length in (9, 15, 50):
            df[f"{ema_length}EMA"] = ta.ema(df["Close"], length=ema_length)

        # RSI
        df["RSI"] = ta.rsi(df["Close"], length=14)

        # Candle colour
        df["Candle"] = df.apply(
            lambda r: "Green" if r["Close"] >= r["Open"] else "Red", axis=1
        )

        # Body & Shadows analysis
        body = abs(df["Close"] - df["Open"])
        upper_shadow = df["High"] - df[["Close", "Open"]].max(axis=1)
        lower_shadow = df[["Close", "Open"]].min(axis=1) - df["Low"]
        total_range = (df["High"] - df["Low"]).replace(0, np.nan)

        df["Body"] = (body / total_range) * 100
        df["Upper_Shadow"] = (upper_shadow / total_range) * 100
        df["Lower_Shadow"] = (lower_shadow / total_range) * 100

        sema = 5
        df["Avg_Upper_Shadow"] = df["Upper_Shadow"].rolling(sema, min_periods=1).mean()
        df["Avg_Lower_Shadow"] = df["Lower_Shadow"].rolling(sema, min_periods=1).mean()
        df["ALUS"] = df["Avg_Lower_Shadow"] / df["Avg_Upper_Shadow"].replace(0, np.nan)

        body_large = df["Body"] >= 50
        bull_condition = (~body_large) & (df["Upper_Shadow"] <= 30) & (df["Lower_Shadow"] >= 70)
        bear_condition = (~body_large) & (df["Upper_Shadow"] >= 70) & (df["Lower_Shadow"] <= 30)
        df["Candle_Signal"] = np.select(
            [bull_condition, bear_condition],
            ["Bullish", "Bearish"],
            default="Neutral",
        )

        # Cleanup
        df.drop(columns=["time"], errors="ignore", inplace=True)


def get_cache_stats() -> dict:
    """Get cache statistics for monitoring.

    Returns:
        Dictionary with cache information.
    """
    if not _redis_client:
        return {"error": "Redis not initialized"}
    try:
        keys = _redis_client.keys("stock_data:*")
        return {
            "total_entries": len(keys),
            "cache_duration_seconds": _CACHE_DURATION,
            "backend": "redis",
        }
    except Exception as exc:
        return {"error": str(exc)}


def clear_cache() -> None:
    """Clear all cached stock data."""
    if not _redis_client:
        return
    try:
        keys = _redis_client.keys("stock_data:*")
        if keys:
            _redis_client.delete(*keys)
        logger.info("🗑️  Data cache cleared (Redis)")
    except Exception as exc:
        logger.error(f"❌ Failed to clear cache: {exc}")
