import pandas as pd
import pandas_ta as ta
import numpy as np
import requests
import time
from functools import lru_cache
from datetime import datetime, timedelta
from threading import Lock
from app.core.logger import get_data_provider_logger

logger = get_data_provider_logger()

import redis
import pickle
from app.core.settings import settings

# Initialize Redis client for caching (using DB 3 to separate from Celery)
# Broker is usually DB 0, Backend DB 1.
try:
    base_redis_url = settings.redis_broker_url.rsplit('/', 1)[0]
    _redis_client = redis.Redis.from_url(f"{base_redis_url}/3", decode_responses=False)
except Exception as e:
    logger.error(f"❌ Failed to initialize Redis cache: {str(e)}")
    _redis_client = None

CACHE_DURATION = 300  # 5 minutes in seconds


def _get_cache_key(symbol: str, period: int, interval: str) -> str:
    """Generate cache key from parameters"""
    return f"stock_data:{symbol}:{period}:{interval}"


def _get_from_cache(cache_key: str):
    """Retrieve data from Redis cache"""
    if not _redis_client:
        return None
        
    try:
        cached_data = _redis_client.get(cache_key)
        if cached_data:
            return pickle.loads(cached_data)
    except Exception as e:
        logger.error(f"⚠️  Redis read error: {str(e)}")
    
    return None


def _save_to_cache(cache_key: str, data: pd.DataFrame):
    """Save data to Redis cache with TTL"""
    if not _redis_client:
        return
        
    try:
        serialized = pickle.dumps(data)
        _redis_client.setex(cache_key, CACHE_DURATION, serialized)
    except Exception as e:
        logger.error(f"⚠️  Redis write error: {str(e)}")


def fetch_historical_data(symbol: str, period: int = 30, interval: str = "15m"):
    """
    Fetch historical data for crypto symbols using Delta Exchange API
    and calculate technical indicators.

    Args:
        symbol: Crypto symbol (e.g., BTCUSD)
        period: Time period in days (default: 30)
        interval: Candle interval (default: 15m)

    Returns:
        DataFrame with historical data + indicators

    Note:
        Data is cached for 5 minutes to avoid redundant API calls.
        Cache is thread-safe for concurrent Celery workers.
    """

    # Check cache first (thread-safe)
    cache_key = _get_cache_key(symbol, period, interval)
    cached_data = _get_from_cache(cache_key)
    
    if cached_data is not None:
        logger.info(f"♻️  Cache HIT: {symbol} | period={period}, interval={interval}")
        return cached_data

    logger.info(f"🌐 Cache MISS: Fetching fresh data for {symbol} | period={period}, interval={interval}")

    try:
        # Calculate time range
        end_time = int(time.time())
        start_time = end_time - (period * 86400)

        params = {
            'resolution': interval,
            'symbol': symbol,
            'start': str(start_time),
            'end': str(end_time)
        }

        headers = {'Accept': 'application/json'}

        df = None
        last_error = None

        # Retry logic with exponential backoff
        for attempt in range(3):
            try:
                logger.debug(f"API attempt {attempt + 1}/3 for {symbol}")
                
                response = requests.get(
                    'https://api.india.delta.exchange/v2/history/candles',
                    params=params,
                    headers=headers,
                    timeout=10
                )

                if response.status_code == 200:
                    data = response.json()

                    if data.get('success') and len(data.get('result', [])) > 0:
                        candles = data['result']

                        rows = []
                        for c in candles:
                            rows.append({
                                'time': c['time'],
                                'Open': float(c['open']),
                                'High': float(c['high']),
                                'Low': float(c['low']),
                                'Close': float(c['close']),
                                'Volume': float(c['volume'] or 0)
                            })

                        df = pd.DataFrame(rows)

                        # Process datetime
                        df['DateTime'] = pd.to_datetime(df['time'], unit='s', utc=True).dt.tz_convert('Asia/Kolkata')
                        df = df.sort_values('DateTime')
                        df.set_index('DateTime', inplace=True)
                        df['DateTime'] = df.index

                        df['Date'] = df.index.strftime('%d/%m/%Y')
                        df['Time'] = df.index.strftime('%I:%M %p')

                        logger.info(f"✅ API fetch successful: {symbol} | {len(df)} candles retrieved")
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
            except Exception as e:
                last_error = str(e)
                logger.warning(f"⚠️  Error on attempt {attempt + 1} for {symbol}: {last_error}")

            # Exponential backoff before retry
            if attempt < 2:
                wait_time = 2 ** attempt  # 1s, 2s
                logger.debug(f"Waiting {wait_time}s before retry...")
                time.sleep(wait_time)

        if df is None:
            error_msg = f"Delta Exchange fetch failed after 3 attempts: {last_error}"
            logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)

        # --------- CALCULATE TECHNICAL INDICATORS ---------
        logger.debug(f"📊 Calculating indicators for {symbol}...")

        # EMA (Exponential Moving Average)
        for ema_length in [9, 15, 50]:
            df[f"{ema_length}EMA"] = ta.ema(df['Close'], length=ema_length)

        # RSI (Relative Strength Index)
        df['RSI'] = ta.rsi(df['Close'], length=14)

        # Candle color
        df['Candle'] = df.apply(lambda r: 'Green' if r['Close'] >= r['Open'] else 'Red', axis=1)

        # Body & Shadows analysis
        Body = abs(df['Close'] - df['Open'])
        Upper_Shadow = df['High'] - df[['Close', 'Open']].max(axis=1)
        Lower_Shadow = df[['Close', 'Open']].min(axis=1) - df['Low']
        Total_Range = df['High'] - df['Low']

        # Avoid division by zero
        Total_Range = Total_Range.replace(0, np.nan)

        df['Body'] = (Body / Total_Range) * 100
        df['Upper_Shadow'] = (Upper_Shadow / Total_Range) * 100
        df['Lower_Shadow'] = (Lower_Shadow / Total_Range) * 100

        # Average shadows
        SEMA = 5
        df['Avg_Upper_Shadow'] = df['Upper_Shadow'].rolling(SEMA, min_periods=1).mean()
        df['Avg_Lower_Shadow'] = df['Lower_Shadow'].rolling(SEMA, min_periods=1).mean()
        
        # Avoid division by zero in ALUS calculation
        df['ALUS'] = df['Avg_Lower_Shadow'] / df['Avg_Upper_Shadow'].replace(0, np.nan)

        # Candle pattern signals
        body_large = df['Body'] >= 50

        bull_condition = (~body_large) & (df['Upper_Shadow'] <= 30) & (df['Lower_Shadow'] >= 70)
        bear_condition = (~body_large) & (df['Upper_Shadow'] >= 70) & (df['Lower_Shadow'] <= 30)

        df['Candle_Signal'] = np.select(
            [bull_condition, bear_condition],
            ["Bullish", "Bearish"],
            default="Neutral"
        )

        # Clean up
        df.drop(columns=['time'], errors='ignore', inplace=True)

        logger.info(f"✅ Processing complete: {symbol} | {len(df)} rows | Indicators calculated")

        # Store in cache (thread-safe)
        _save_to_cache(cache_key, df)

        return df

    except Exception as e:
        logger.error(f"❌ Fatal error fetching data for {symbol}: {str(e)}", exc_info=True)
        raise


def get_cache_stats():
    """
    Get cache statistics for monitoring
    
    Returns:
        Dictionary with cache information
    """
    if not _redis_client:
        return {"error": "Redis not initialized"}

    try:
        keys = _redis_client.keys("stock_data:*")
        total_entries = len(keys)
        # Note: Redis handles expiration automatically, so we don't have expired entries count easily available
        # without inspecting TTLs which is expensive.
        
        return {
            "total_entries": total_entries,
            "cache_duration_seconds": CACHE_DURATION,
            "backend": "redis"
        }
    except Exception as e:
        return {"error": str(e)}


def clear_cache():
    """Clear all cached stock data"""
    if not _redis_client:
        return

    try:
        keys = _redis_client.keys("stock_data:*")
        if keys:
            _redis_client.delete(*keys)
        logger.info("🗑️  Data cache cleared (Redis)")
    except Exception as e:
        logger.error(f"❌ Failed to clear cache: {str(e)}")