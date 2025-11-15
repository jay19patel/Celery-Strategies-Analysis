import pandas as pd
import pandas_ta as ta
import numpy as np
import requests
import time
from app.core.logger import get_data_provider_logger

logger = get_data_provider_logger()


def fetch_historical_data(symbol: str, period: int = 30, interval: str = "15m"):
    """
    Fetch historical data for crypto symbols using Delta Exchange API
    and calculate technical indicators.

    Args:
        symbol: Crypto symbol (e.g., BTCUSD)
        period: Time period (default: 5d)
        interval: Candle interval (default: 5m)

    Returns:
        DataFrame with historical data + indicators
    """

    try:
        logger.info(f"Fetching data for {symbol} from Delta Exchange | period={period}, interval={interval}")

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

        for attempt in range(3):
            try:
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

                        df['DateTime'] = pd.to_datetime(df['time'], unit='s', utc=True).dt.tz_convert('Asia/Kolkata')
                        df = df.sort_values('DateTime')
                        df.set_index('DateTime', inplace=True)
                        df['DateTime'] = df.index

                        df['Date'] = df.index.strftime('%d/%m/%Y')
                        df['Time'] = df.index.strftime('%I:%M %p')

                        break

                    else:
                        last_error = "API success=false or empty result"

                else:
                    last_error = f"Bad status code: {response.status_code}"

            except Exception as e:
                last_error = str(e)

            if attempt < 2:
                time.sleep(1)

        if df is None:
            raise Exception(f"Delta Exchange fetch failed: {last_error}")

        # --------- INDICATORS ---------

        logger.info("Processing indicators...")

        # EMA
        for ema_length in [9, 15, 50]:
            df[f"{ema_length}EMA"] = ta.ema(df['Close'], length=ema_length)

        # RSI
        df['RSI'] = ta.rsi(df['Close'], length=14)

        # Candle color
        df['Candle'] = df.apply(lambda r: 'Green' if r['Close'] >= r['Open'] else 'Red', axis=1)

        # Body & Shadows
        Body = abs(df['Close'] - df['Open'])
        Upper_Shadow = df['High'] - df[['Close', 'Open']].max(axis=1)
        Lower_Shadow = df[['Close', 'Open']].min(axis=1) - df['Low']
        Total_Range = df['High'] - df['Low']

        df['Body'] = (Body / Total_Range) * 100
        df['Upper_Shadow'] = (Upper_Shadow / Total_Range) * 100
        df['Lower_Shadow'] = (Lower_Shadow / Total_Range) * 100

        SEMA = 5
        df['Avg_Upper_Shadow'] = df['Upper_Shadow'].rolling(SEMA, min_periods=1).mean()
        df['Avg_Lower_Shadow'] = df['Lower_Shadow'].rolling(SEMA, min_periods=1).mean()
        df['ALUS'] = df['Avg_Lower_Shadow'] / df['Avg_Upper_Shadow']

        body_large = df['Body'] >= 50

        bull_condition = (~body_large) & (df['Upper_Shadow'] <= 30) & (df['Lower_Shadow'] >= 70)
        bear_condition = (~body_large) & (df['Upper_Shadow'] >= 70) & (df['Lower_Shadow'] <= 30)

        df['Candle_Signal'] = np.select(
            [bull_condition, bear_condition],
            ["Bullish", "Bearish"],
            default="Neutral"
        )

        df.drop(columns=['time'], errors='ignore', inplace=True)

        logger.info(f"Finished processing {symbol} | {len(df)} rows")
        return df

    except Exception as e:
        logger.error(f"Error fetching Delta data: {str(e)}", exc_info=True)
        raise
