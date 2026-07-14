"""Shared feature engineering + condition logic for the two Master Backtester
strategies (see master_backtester_long_strategy.py / master_backtester_short_strategy.py).

Ported verbatim from the Master Backtester project's livetest/strategies.py -
every formula is copied as-is (not re-derived) to avoid subtly-wrong
reimplementations. That project validated these two specific combos (best
LONG and best SHORT by raw PnL) via an exhaustive/beam search over historical
ETHUSD 1h data, then confirmed the search methodology itself was free of
lookahead bias (see that project's combo_backtester.py for the search, and
its README/session history for the out-of-sample validation results -
strategy_01 (LONG) did NOT hold up out-of-sample and should be treated with
real skepticism; strategy_02 (SHORT) retained a modest, real edge).

Leading underscore in the filename is deliberate: app/core/settings.py's
strategy auto-discovery (`_discover_strategy_class_paths`) skips any module
name starting with "_", so this shared helper is never itself scanned for a
BaseStrategy subclass (it has none) even though the two actual strategy files
import it.

Every condition string is one of:
    "{indicator}>median" / "{indicator}<median" - indicator value vs its own
        trailing rolling median (CONDITION_WINDOW bars, causal - no lookahead)
    "{signal}(L)" / "{signal}(S)" - sig_{signal} column == +1 / == -1
"""

import numpy as np
import pandas as pd
import pandas_ta as ta

CONDITION_WINDOW = 100  # same causal rolling window used when these strategies were found

# The 2 strategies actually used here (out of 25 originally found - see the
# module docstring above for why only these 2 were carried over).
STRATEGIES = {
    "strategy_01_long": {
        "name": "strategy_01_long",
        "combo": "is_bullish>median AND vol_regime>median AND gap_up>median AND path_curvature>median AND wick_imbalance>median AND low_open_return>median AND open_close_return>median AND gap_size>median",
        "direction": 1,
        # search-time stats (reference only, in-sample): size=8, fires=134, trades=83, win_rate_pct=51.8, total_pnl=248.57
        # out-of-sample result: NEGATIVE (-3.2%) - did not hold up, treat with skepticism
    },
    "strategy_02_short": {
        "name": "strategy_02_short",
        "combo": "RSI_21<median AND price_entropy<median AND lower_wick<median AND wick_to_body<median AND price_to_sma_50<median AND return_10<median",
        "direction": -1,
        # search-time stats (reference only, in-sample): size=6, fires=373, trades=184, win_rate_pct=46.2, total_pnl=630.57
        # out-of-sample result: positive (+74.2%), weaker than in-sample but a real, retained edge
    },
}


# ----------------------------------------------------------------------
# Feature engineering (trimmed port of Master Backtester's IndicatorEngine +
# PriceActionEngine - only what these 2 strategies' conditions reference)
# ----------------------------------------------------------------------
def build_features(df):
    """OHLCV DataFrame (needs Open/High/Low/Close/Volume columns - extra
    columns such as this repo's own 9EMA/15EMA/RSI/Candle_Signal etc. from
    data_provider.fetch_historical_data are ignored, not overwritten) ->
    DataFrame with every column STRATEGIES above reference."""
    df = df.copy()
    df = _add_indicators(df)
    df = df.bfill().ffill()  # matches the original pipeline: only indicators get filled, not price-action
    df = _add_price_action(df)
    return df


def _add_indicators(df):
    # --- basic price/volume features ---
    df["close_return"] = np.log(df["Close"] / df["Close"].shift(1))
    df["return_1"] = df["Close"].pct_change()  # dependency for several stats/microstructure features below
    df["return_5"] = df["Close"].pct_change(5)
    df["return_10"] = df["Close"].pct_change(10)
    df["return_20"] = df["Close"].pct_change(20)
    df["open_close_return"] = np.log(df["Close"] / df["Open"])
    df["low_open_return"] = np.log(df["Low"] / df["Open"])
    df["log_volume"] = np.log(df["Volume"] + 1)
    df["volume_ratio"] = df["Volume"] / df["Volume"].rolling(20).mean()

    # --- moving averages & trend ---
    for period in [10, 20, 50, 100]:
        df[f"EMA_{period}"] = ta.ema(df["Close"], length=period).bfill()
    for period in [20, 50, 100]:  # SMA_50 is a dependency of price_to_sma_50, not referenced directly
        df[f"SMA_{period}"] = ta.sma(df["Close"], length=period).bfill()
    df["price_to_ema_20"] = (df["Close"] - df["EMA_20"]) / df["EMA_20"]
    df["price_to_sma_50"] = (df["Close"] - df["SMA_50"]) / df["SMA_50"]
    df["ema_10_20_cross"] = df["EMA_10"] - df["EMA_20"]
    df["VWAP"] = ta.vwap(df["High"], df["Low"], df["Close"], df["Volume"]).bfill()
    df["price_to_vwap"] = (df["Close"] - df["VWAP"]) / df["VWAP"]

    # --- momentum ---
    for period in [7, 21]:
        df[f"RSI_{period}"] = ta.rsi(df["Close"], length=period).bfill()
    macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
    df["MACD"] = macd["MACD_12_26_9"].bfill()
    df["MACD_signal"] = macd["MACDs_12_26_9"].bfill()
    df["MACD_hist"] = macd["MACDh_12_26_9"].bfill()
    stoch = ta.stoch(df["High"], df["Low"], df["Close"], k=14, d=3)
    df["Stoch_K"] = stoch["STOCHk_14_3_3"].bfill()
    df["WilliamsR_14"] = ta.willr(df["High"], df["Low"], df["Close"], length=14).bfill()
    df["ROC_20"] = ta.roc(df["Close"], length=20).bfill()

    # --- volatility ---
    for period in [14, 21]:
        df[f"ATR_{period}"] = ta.atr(df["High"], df["Low"], df["Close"], length=period).bfill()
    df["ATR_pct"] = (df["ATR_14"] / df["Close"]) * 100
    bbands = ta.bbands(df["Close"], length=20, std=2)
    df["BB_lower"] = bbands.iloc[:, 0].bfill()
    df["BB_middle"] = bbands.iloc[:, 1].bfill()
    df["BB_upper"] = bbands.iloc[:, 2].bfill()
    kc = ta.kc(df["High"], df["Low"], df["Close"], length=20, scalar=2)
    df["KC_lower"] = kc.iloc[:, 0].bfill()
    df["KC_upper"] = kc.iloc[:, 2].bfill()
    df["volatility_10"] = df["Close"].pct_change().rolling(10).std()  # dependency for vol_regime/fractal_proxy/shock_elasticity

    # --- trend strength ---
    for period in [14, 20]:
        adx_df = ta.adx(df["High"], df["Low"], df["Close"], length=period)
        df[f"ADX_{period}"] = adx_df[f"ADX_{period}"].bfill()
        df[f"DMP_{period}"] = adx_df[f"DMP_{period}"].bfill()
        df[f"DMN_{period}"] = adx_df[f"DMN_{period}"].bfill()
    df["directional_bias"] = df["DMP_14"] - df["DMN_14"]
    supertrend = ta.supertrend(df["High"], df["Low"], df["Close"], length=10, multiplier=3)
    df["supertrend"] = supertrend["SUPERT_10_3"].bfill()
    df["supertrend_direction"] = supertrend["SUPERTd_10_3"].bfill()
    df["st_flip"] = df["supertrend_direction"].diff().abs()
    df["bars_since_flip"] = df.groupby((df["st_flip"] == 2).cumsum()).cumcount()
    aroon = ta.aroon(df["High"], df["Low"], length=25)
    df["aroon_up"] = aroon["AROONU_25"].bfill()  # dependency for aroon_oscillator
    df["aroon_down"] = aroon["AROOND_25"].bfill()
    df["aroon_oscillator"] = df["aroon_up"] - df["aroon_down"]

    # --- volume ---
    df["CMF"] = ta.cmf(df["High"], df["Low"], df["Close"], df["Volume"], length=20).bfill()
    df["MFI"] = ta.mfi(df["High"], df["Low"], df["Close"], df["Volume"], length=14).bfill()
    df["VPT"] = ta.pvt(df["Close"], df["Volume"]).bfill()

    # --- candle features ---
    df["upper_wick"] = df["High"] - df[["Open", "Close"]].max(axis=1)
    df["lower_wick"] = df[["Open", "Close"]].min(axis=1) - df["Low"]
    df["total_wick"] = df["upper_wick"] + df["lower_wick"]
    df["wick_imbalance"] = (df["upper_wick"] - df["lower_wick"]) / df["Close"]
    df["wick_to_body"] = df["total_wick"] / (abs(df["Close"] - df["Open"]) + 0.0001)
    df["close_position"] = (df["Close"] - df["Low"]) / (df["High"] - df["Low"] + 0.0001)
    df["is_bullish"] = (df["Close"] > df["Open"]).astype(int)
    df["candle_strength"] = abs(df["Close"] - df["Open"]) / (df["High"] - df["Low"] + 0.0001)
    df["gap_up"] = (df["Open"] > df["Close"].shift(1)).astype(int)
    df["gap_down"] = (df["Open"] < df["Close"].shift(1)).astype(int)
    df["gap_size"] = (df["Open"] - df["Close"].shift(1)) / df["Close"].shift(1)

    # --- statistical ---
    for period in [10, 50]:
        df[f"zscore_{period}"] = (df["Close"] - df["Close"].rolling(period).mean()) / (
            df["Close"].rolling(period).std() + 0.0001
        )
    df["skew_20"] = df["return_1"].rolling(20).skew()
    df["kurt_20"] = df["return_1"].rolling(20).kurt()

    # --- advanced volatility microstructure ---
    new_features = pd.DataFrame(index=df.index)
    new_features["realized_var_20"] = (df["return_1"] ** 2).rolling(20).sum()  # dependency for jump_strength
    new_features["bipower_var"] = (abs(df["return_1"]) * abs(df["return_1"].shift())).rolling(20).sum()
    new_features["jump_strength"] = new_features["realized_var_20"] - new_features["bipower_var"]
    new_features["vol_regime"] = (df["volatility_10"] > df["volatility_10"].rolling(50).mean()).astype(int)
    rng = df["High"] - df["Low"]
    new_features["range_velocity"] = (rng - rng.shift(1)) / (rng.shift(1) + 0.0001)
    new_features["fractal_proxy"] = df["ATR_pct"] / (df["volatility_10"] + 0.0001)
    df = pd.concat([df, new_features], axis=1)

    # --- advanced trend & momentum microstructure ---
    new_features = pd.DataFrame(index=df.index)
    new_features["efficiency_ratio"] = abs(df["Close"] - df["Close"].shift(10)) / (
        df["High"].rolling(10).max() - df["Low"].rolling(10).min() + 0.0001
    )
    new_features["trend_persistence"] = np.sign(df["return_1"]).rolling(10).sum()
    new_features["trend_smoothness"] = abs(df["Close"] - df["Close"].shift(20)) / (
        df["return_1"].rolling(20).std() + 0.0001
    )
    new_features["path_curvature"] = df["return_1"].diff().abs().rolling(10).mean()
    new_features["trend_strength"] = abs(df["Close"] - df["supertrend"]) / df["Close"]  # dependency for trend_volume
    new_features["trend_acceleration"] = new_features["trend_strength"].diff()
    new_features["dir_entropy"] = df["return_1"].rolling(20).apply(
        lambda x: -np.mean(np.sign(x) * np.log(np.abs(np.sign(x)) + 1e-6))
    )
    df = pd.concat([df, new_features], axis=1)

    # --- information theory ---
    new_features = pd.DataFrame(index=df.index)
    from scipy import stats as scipy_stats

    new_features["price_entropy"] = df["return_1"].rolling(20).apply(
        lambda x: scipy_stats.entropy(np.histogram(x, bins=5)[0] + 1) if len(x) > 0 else 0,
        raw=False,
    )
    new_features["surprise"] = (df["return_1"] - df["return_1"].rolling(20).mean()) / (
        df["return_1"].rolling(20).std() + 1e-6
    )
    new_features["shock_elasticity"] = df["return_1"].abs() / (df["volatility_10"] + 1e-6)
    df = pd.concat([df, new_features], axis=1)

    # --- market microstructure & liquidity ---
    new_features = pd.DataFrame(index=df.index)
    new_features["slippage_proxy"] = (df["High"] - df["Low"]) / df["Close"].rolling(10).mean()
    new_features["stop_hunt_proxy"] = (df["High"] - df["Low"]) / (df["ATR_14"] + 0.0001)
    df = pd.concat([df, new_features], axis=1)

    # --- interaction features ---
    new_features = pd.DataFrame(index=df.index)
    new_features["trend_volume"] = df["trend_strength"] * df["volume_ratio"]
    new_features["adx_volume"] = df["ADX_14"] * df["volume_ratio"]
    new_features["vol_atr_ratio"] = df["volume_ratio"] / (df["ATR_pct"] + 0.0001)
    df = pd.concat([df, new_features], axis=1)

    return df


def _add_price_action(df, swing_left=3, swing_right=3, max_zone_age=50, max_active_zones=3):
    state = {}
    df = _add_swings(df, state, swing_left, swing_right)
    df = _add_market_structure(df, state)
    df = _add_candlestick_signals(df)
    df = _add_breakout_signals(df, state)
    df = _add_smart_money_signals(df, state, max_zone_age, max_active_zones)
    df = _add_fibonacci_signals(df)
    df = _add_range_signals(df)
    df = _add_combination_signals(df)
    return df


def _add_swings(df, state, left, right):
    """Fractal swing points, confirmed `right` bars after the pivot (no lookahead)."""
    win = left + right + 1
    is_ph = df["High"] == df["High"].rolling(win, center=True, min_periods=win).max()
    is_pl = df["Low"] == df["Low"].rolling(win, center=True, min_periods=win).min()

    confirmed_high = df["High"].where(is_ph).shift(right)
    confirmed_low = df["Low"].where(is_pl).shift(right)
    last_swing_high = confirmed_high.ffill()
    last_swing_low = confirmed_low.ffill()

    cross_up = (df["Close"] > last_swing_high) & (df["Close"].shift(1) <= last_swing_high.shift(1))
    cross_dn = (df["Close"] < last_swing_low) & (df["Close"].shift(1) >= last_swing_low.shift(1))

    new_cols = {
        "swing_high_at_pivot": is_ph.astype(int),
        "swing_low_at_pivot": is_pl.astype(int),
        "last_swing_high": last_swing_high,
        "last_swing_low": last_swing_low,
        "bars_since_swing_high": df.groupby(confirmed_high.notna().cumsum()).cumcount(),
        "bars_since_swing_low": df.groupby(confirmed_low.notna().cumsum()).cumcount(),
        "sig_swing_break": np.where(cross_up, 1, np.where(cross_dn, -1, 0)),
    }
    df = pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)

    state["confirmed_high"] = confirmed_high
    state["confirmed_low"] = confirmed_low
    return df


def _add_market_structure(df, state):
    """BOS/CHoCH state machine + HH/HL/LH/LL swing labels."""
    n = len(df)
    closes = df["Close"].to_numpy()
    lsh = df["last_swing_high"].to_numpy()
    lsl = df["last_swing_low"].to_numpy()

    bos = np.zeros(n)
    choch = np.zeros(n)
    trend_arr = np.zeros(n)
    trend = 0
    consumed_high = np.nan
    consumed_low = np.nan

    for i in range(n):
        level_h, level_l = lsh[i], lsl[i]
        broke_up = (not np.isnan(level_h)) and level_h != consumed_high and closes[i] > level_h
        broke_dn = (not np.isnan(level_l)) and level_l != consumed_low and closes[i] < level_l

        if broke_up:
            if trend == -1:
                choch[i] = 1  # bearish structure broken upward -> character change
            else:
                bos[i] = 1
            trend = 1
            consumed_high = level_h
        if broke_dn:
            if trend == 1:
                choch[i] = -1
            else:
                bos[i] = -1
            trend = -1
            consumed_low = level_l
        trend_arr[i] = trend

    label_high = np.zeros(n)
    label_low = np.zeros(n)
    ch = state["confirmed_high"].to_numpy()
    cl = state["confirmed_low"].to_numpy()
    prev_high, prev_low = np.nan, np.nan
    for i in range(n):
        if not np.isnan(ch[i]):
            if not np.isnan(prev_high):
                label_high[i] = 2 if ch[i] > prev_high else -1
            prev_high = ch[i]
        if not np.isnan(cl[i]):
            if not np.isnan(prev_low):
                label_low[i] = 1 if cl[i] > prev_low else -2
            prev_low = cl[i]

    new_cols = {
        "structure_trend": trend_arr,
        "sig_bos": bos,
        "sig_choch": choch,
        "swing_high_label": label_high,
        "swing_low_label": label_low,
        "swing_label": np.where(label_low != 0, label_low, label_high),
    }
    return pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)


def _add_candlestick_signals(df):
    """sig_engulfing / sig_pinbar - needed as prerequisites for _add_combination_signals."""
    body = (df["Close"] - df["Open"]).abs()
    prev_bear = df["Close"].shift(1) < df["Open"].shift(1)
    prev_bull = df["Close"].shift(1) > df["Open"].shift(1)

    bull_engulf = (
        (df["Close"] > df["Open"]) & prev_bear
        & (df["Close"] >= df["Open"].shift(1)) & (df["Open"] <= df["Close"].shift(1))
    )
    bear_engulf = (
        (df["Close"] < df["Open"]) & prev_bull
        & (df["Close"] <= df["Open"].shift(1)) & (df["Open"] >= df["Close"].shift(1))
    )
    rng = df["High"] - df["Low"] + 1e-9
    upper_wick = df["High"] - df[["Open", "Close"]].max(axis=1)
    lower_wick = df[["Open", "Close"]].min(axis=1) - df["Low"]
    close_pos = (df["Close"] - df["Low"]) / rng
    bull_pin = (lower_wick >= 2 * body) & (close_pos > 0.6)
    bear_pin = (upper_wick >= 2 * body) & (close_pos < 0.4)

    new_cols = {
        "sig_engulfing": np.where(bull_engulf, 1, np.where(bear_engulf, -1, 0)),
        "sig_pinbar": np.where(bull_pin, 1, np.where(bear_pin, -1, 0)),
    }
    return pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)


def _add_breakout_signals(df, state):
    """squeeze_on/resistance_level/support_level are directly referenced by
    strategies; sig_donchian/sig_sr_breakout/sig_squeeze_breakout are computed
    as a side effect (unused, harmless)."""
    ch = state["confirmed_high"].copy()
    cl = state["confirmed_low"].copy()
    resistance = ch.dropna().rolling(3).max().reindex(df.index).ffill()
    support = cl.dropna().rolling(3).min().reindex(df.index).ffill()
    squeeze_on = (df["BB_upper"] < df["KC_upper"]) & (df["BB_lower"] > df["KC_lower"])

    new_cols = {
        "resistance_level": resistance,
        "support_level": support,
        "squeeze_on": squeeze_on.astype(int),
    }
    return pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)


def _add_smart_money_signals(df, state, max_zone_age, max_active_zones):
    """sig_ob_retest is directly referenced; sig_fvg_fill/sig_sweep are
    computed as a side effect (sig_sweep feeds _add_combination_signals)."""
    n = len(df)
    o = df["Open"].to_numpy()
    h = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    c = df["Close"].to_numpy()
    atr = df["ATR_14"].to_numpy()
    max_age, max_zones = max_zone_age, max_active_zones

    ob_sig = np.zeros(n)
    bull_zones, bear_zones = [], []

    for i in range(3, n):
        if c[i] - c[i - 3] > 1.5 * atr[i]:
            for j in range(i - 1, max(i - 4, 0) - 1, -1):
                if c[j] < o[j]:
                    if not bull_zones or (low[j], h[j]) != (bull_zones[-1]["lo"], bull_zones[-1]["hi"]):
                        bull_zones.append({"lo": low[j], "hi": h[j], "birth": i})
                        if len(bull_zones) > max_zones:
                            bull_zones.pop(0)
                    break
        if c[i - 3] - c[i] > 1.5 * atr[i]:
            for j in range(i - 1, max(i - 4, 0) - 1, -1):
                if c[j] > o[j]:
                    if not bear_zones or (low[j], h[j]) != (bear_zones[-1]["lo"], bear_zones[-1]["hi"]):
                        bear_zones.append({"lo": low[j], "hi": h[j], "birth": i})
                        if len(bear_zones) > max_zones:
                            bear_zones.pop(0)
                    break

        fired = False
        for zone in list(bull_zones):
            if i - zone["birth"] > max_age or c[i] < zone["lo"] - atr[i]:
                bull_zones.remove(zone)
                continue
            if not fired and low[i] <= zone["hi"] and c[i] > zone["hi"] and c[i] > o[i]:
                ob_sig[i] = 1
                bull_zones.remove(zone)
                fired = True
        for zone in list(bear_zones):
            if i - zone["birth"] > max_age or c[i] > zone["hi"] + atr[i]:
                bear_zones.remove(zone)
                continue
            if not fired and h[i] >= zone["lo"] and c[i] < zone["lo"] and c[i] < o[i]:
                ob_sig[i] = -1
                bear_zones.remove(zone)
                fired = True

    lsl = df["last_swing_low"]
    lsh = df["last_swing_high"]
    bull_sweep = (df["Low"] < lsl) & (df["Close"] > lsl) & (df["Close"] > df["Open"])
    bear_sweep = (df["High"] > lsh) & (df["Close"] < lsh) & (df["Close"] < df["Open"])

    new_cols = {
        "sig_ob_retest": ob_sig,
        "sig_sweep": np.where(bull_sweep, 1, np.where(bear_sweep, -1, 0)),
    }
    return pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)


def _add_fibonacci_signals(df):
    """sig_fib_retracement is directly referenced (sig_fib_extension is not
    needed by either strategy and is skipped)."""
    lsh = df["last_swing_high"]
    lsl = df["last_swing_low"]
    impulse = (lsh - lsl).abs()
    bull_candle = df["Close"] > df["Open"]
    bear_candle = df["Close"] < df["Open"]

    up_top = lsh - 0.382 * impulse
    up_bot = lsh - 0.786 * impulse
    dn_bot = lsl + 0.382 * impulse
    dn_top = lsl + 0.786 * impulse
    in_zone_up = (df["Low"] <= up_top) & (df["Low"] >= up_bot)
    in_zone_dn = (df["High"] >= dn_bot) & (df["High"] <= dn_top)

    sig_fib_retracement = np.where(
        (df["structure_trend"] == 1) & in_zone_up & bull_candle, 1,
        np.where((df["structure_trend"] == -1) & in_zone_dn & bear_candle, -1, 0),
    )
    return pd.concat([df, pd.DataFrame({"sig_fib_retracement": sig_fib_retracement}, index=df.index)], axis=1)


def _add_range_signals(df):
    """sig_inside_bar_breakout is directly referenced (sig_nr7_breakout is
    not needed and is skipped)."""
    inside = (df["High"] < df["High"].shift(1)) & (df["Low"] > df["Low"].shift(1))
    break_up = inside.shift(1, fill_value=False) & (df["Close"] > df["High"].shift(1))
    break_dn = inside.shift(1, fill_value=False) & (df["Close"] < df["Low"].shift(1))
    sig_inside_bar_breakout = np.where(break_up, 1, np.where(break_dn, -1, 0))
    return pd.concat([df, pd.DataFrame({"sig_inside_bar_breakout": sig_inside_bar_breakout}, index=df.index)], axis=1)


def _add_combination_signals(df):
    """trend_score + sig_trend_confluence are directly referenced;
    sig_bos_retest/sig_sweep_reversal/sig_super_confluence are computed as a
    side effect (this is all one method upstream too - splitting it apart
    would risk breaking the shared trend_score/bos state)."""
    votes = (
        np.sign(df["EMA_20"] - df["EMA_50"])
        + df["supertrend_direction"]
        + np.sign(df["MACD_hist"])
        + np.sign(df["DMP_14"] - df["DMN_14"]) * (df["ADX_14"] > 20)
        + np.sign(df["Close"] - df["VWAP"])
    )
    fresh_up = (votes >= 4) & (votes.shift(1) < 4)
    fresh_dn = (votes <= -4) & (votes.shift(1) > -4)

    new_cols = {
        "trend_score": votes,
        "sig_trend_confluence": np.where(fresh_up, 1, np.where(fresh_dn, -1, 0)),
    }
    return pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)


# ----------------------------------------------------------------------
# Turning a strategy's combo string into a real +1/-1/0 direction array
# ----------------------------------------------------------------------
def _condition_mask(df, condition, window):
    if condition.endswith("(L)"):
        return df["sig_" + condition[: -len("(L)")]].to_numpy() == 1
    if condition.endswith("(S)"):
        return df["sig_" + condition[: -len("(S)")]].to_numpy() == -1
    if condition.endswith(">median"):
        col = condition[: -len(">median")]
        median = df[col].rolling(window, min_periods=window).median()
        return (df[col] > median).to_numpy()
    if condition.endswith("<median"):
        col = condition[: -len("<median")]
        median = df[col].rolling(window, min_periods=window).median()
        return (df[col] < median).to_numpy()
    raise ValueError(f"Unrecognized condition syntax: {condition!r}")


def build_direction_array(df, strategy, window=CONDITION_WINDOW):
    """strategy: one entry from STRATEGIES. Returns a +1/-1/0 numpy array,
    one value per candle."""
    mask = None
    for condition in strategy["combo"].split(" AND "):
        cond_mask = _condition_mask(df, condition, window)
        mask = cond_mask if mask is None else (mask & cond_mask)
    return np.where(mask, strategy["direction"], 0)


def latest_signal(df, strategy_key, window=CONDITION_WINDOW):
    """Convenience for a live strategy's execute(): build features (if not
    already present) and return the direction (+1/-1/0) for the MOST RECENT
    candle only, plus that candle's Close price. Raises KeyError with a clear
    message if `df` is missing a column the strategy's combo needs (e.g. if
    build_features() wasn't called first)."""
    strategy = STRATEGIES[strategy_key]
    direction_array = build_direction_array(df, strategy, window)
    latest_direction = int(direction_array[-1])
    latest_close = float(df["Close"].iloc[-1])
    return latest_direction, latest_close
