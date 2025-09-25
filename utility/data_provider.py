import pandas as pd
import pandas_ta as ta
import yfinance as yf
import numpy as np


def fetch_historical_data(symbol: str, period: str = "5d", interval: str = "5m"):
    """
    Fetch historical data for crypto symbols with technical indicators
    """
    # Download data from yfinance
    df = yf.download(symbol, period=period, interval=interval)

    # Remove Ticker row (if present) by resetting columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)

    df["DateTime"] = df.index
    # Convert index to Asia/Kolkata timezone
    df.index = df.index.tz_convert('Asia/Kolkata')
    df['Date'] = df.index.date
    df['Time'] = df.index.strftime('%I:%M %p')  # AM/PM format

    # Add EMA indicators
    ema_list = [9, 15]
    for ema_length in ema_list:
        ema_name = f'{ema_length}EMA'
        df[ema_name] = ta.trend.EMAIndicator(close=df['Close'], window=ema_length, fillna=False).ema_indicator()

    # Add RSI indicator
    df['RSI'] = ta.momentum.RSIIndicator(close=df['Close'], window=14, fillna=False).rsi()

    # Candle color
    df['Candle'] = df.apply(lambda row: 'Green' if row['Close'] >= row['Open'] else 'Red', axis=1)

    # Calculate body and shadows
    Body = abs(df['Close'] - df['Open'])
    Upper_Shadow = df['High'] - df[['Close', 'Open']].max(axis=1)
    Lower_Shadow = df[['Close', 'Open']].min(axis=1) - df['Low']
    Total_Range = df['High'] - df['Low']

    # Calculate body percentage
    df['Body'] = (Body / Total_Range) * 100

    # Calculate shadow % temporarily
    df['Upper_Shadow'] = (Upper_Shadow / Total_Range) * 100
    df['Lower_Shadow'] = (Lower_Shadow / Total_Range) * 100

    # Rolling average of previous 5 candles
    SEMA = 5
    df['Avg_Upper_Shadow'] = df['Upper_Shadow'].rolling(window=SEMA, min_periods=1).mean()
    df['Avg_Lower_Shadow'] = df['Lower_Shadow'].rolling(window=SEMA, min_periods=1).mean()
    df["ALUS"] = df['Avg_Lower_Shadow'] / df['Avg_Upper_Shadow']

    # Candle signal conditions
    body_large_condition = df['Body'] >= 50

    shadow_conditions = [
        np.logical_and(~body_large_condition, df['Upper_Shadow'] <= 30, df['Lower_Shadow'] >= 70),
        np.logical_and(~body_large_condition, df['Upper_Shadow'] >= 70, df['Lower_Shadow'] <= 30)
    ]
    shadow_choices = ["Bullish", "Bearish"]

    df['Candle_Signal'] = np.select(
        shadow_conditions,
        shadow_choices,
        default="Neutral"
    )

    return df

    """
    Analyze a crypto symbol and return data with signals and confidence
    """
    df = fetch_historical_data(symbol, period, interval)

    # Add confidence scores
    df['Confidence'] = 0
    for i in range(len(df)):
        if df['Action'].iloc[i] in ['buy', 'sell']:
            df.loc[df.index[i], 'Confidence'] = get_signal_confidence(df, i)

    return df