"""Indicators on plain lists, aligned to the END of the input, plus realised volatility and bars
folded to a higher timeframe. Shared by the regime, the analysis modules and the strategies."""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from typing import Any

YEAR_HOURS = 365 * 24


def ema(values: list[float], period: int) -> list[float]:
    """EMA seeded with the SMA of the first `period` values. len = len(values) - period + 1."""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values: list[float], period: int = 14) -> list[float]:
    """Wilder's RSI. len = len(values) - period."""
    if len(values) <= period:
        return []
    changes = [b - a for a, b in itertools.pairwise(values)]
    gain = sum(max(c, 0) for c in changes[:period]) / period
    loss = sum(max(-c, 0) for c in changes[:period]) / period

    def value() -> float:
        return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)

    out = [value()]
    for c in changes[period:]:
        gain = (gain * (period - 1) + max(c, 0)) / period
        loss = (loss * (period - 1) + max(-c, 0)) / period
        out.append(value())
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """Wilder's Average True Range. len = len(values) - period."""
    if len(closes) <= period:
        return []
    tr = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, len(closes))]
    out = [sum(tr[:period]) / period]
    for t in tr[period:]:
        out.append((out[-1] * (period - 1) + t) / period)
    return out


def supertrend(
    highs: list[float], lows: list[float], closes: list[float], period: int = 10, multiplier: float = 3.0,
) -> list[tuple[float, int]]:
    """(line, direction) per bar, direction +1 when the line is below price (long) and -1 above it.

    The line is an ATR band around the midpoint that only ever moves towards price while the trend
    holds, and flips to the other side when a close crosses it. As a stop it is what a trend-follower
    actually wants: it widens when the market is fast, tightens when it is quiet, and never loosens
    inside a trend. len = len(closes) - period.
    """
    bands = atr(highs, lows, closes, period)
    if not bands:
        return []
    offset = len(closes) - len(bands)  # the bar each ATR value belongs to
    out: list[tuple[float, int]] = []
    upper = lower = 0.0
    direction = 1
    for i, band in enumerate(bands):
        bar = offset + i
        mid = (highs[bar] + lows[bar]) / 2
        up, low = mid + multiplier * band, mid - multiplier * band
        if i == 0:
            upper, lower, direction = up, low, 1 if closes[bar] >= mid else -1
        else:
            prev = closes[bar - 1]
            # A band only tightens towards price; a close beyond it resets that side.
            upper = up if (up < upper or prev > upper) else upper
            lower = low if (low > lower or prev < lower) else lower
            if direction == 1 and closes[bar] < lower:
                direction = -1
            elif direction == -1 and closes[bar] > upper:
                direction = 1
        out.append((lower if direction == 1 else upper, direction))
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[list[float], list[float], list[float]]:
    """(macd, signal, histogram), aligned to the end. The histogram is what is read: it crosses
    zero when momentum turns, which is earlier and cleaner than the two lines crossing."""
    f, s = ema(values, fast), ema(values, slow)
    if not f or not s:
        return [], [], []
    line = [a - b for a, b in zip(f[-len(s):], s, strict=True)]
    sig = ema(line, signal)
    if not sig:
        return line, [], []
    return line[-len(sig):], sig, [a - b for a, b in zip(line[-len(sig):], sig, strict=True)]


def obv(closes: list[float], volumes: list[float]) -> list[float]:
    """On-balance volume: volume added on an up close and taken off on a down close.

    A running total, so only its slope means anything. Rising while price is flat says buying is
    going on under the surface; falling while price rises says the move is not being paid for."""
    out = [0.0]
    for i in range(1, len(closes)):
        step = volumes[i] if closes[i] > closes[i - 1] else -volumes[i] if closes[i] < closes[i - 1] else 0.0
        out.append(out[-1] + step)
    return out


def realised_vol(closes: Sequence[float], bar_hours: float) -> float | None:
    """Annualised standard deviation of log returns: comparable with an option's IV."""
    if len(closes) < 3:
        return None
    rets = [math.log(b / a) for a, b in itertools.pairwise(closes) if a > 0 and b > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(YEAR_HOURS / bar_hours)


def parkinson_vol(bars: Sequence[dict[str, Any]], bar_hours: float) -> float | None:
    """Annualised volatility from each bar's high-low range (Parkinson): about five times as much
    information per bar as close-to-close, so a day of 15m bars measures it far less noisily."""
    xs = [math.log(b["high"] / b["low"]) ** 2 for b in bars if b.get("low") and b.get("high") and b["low"] > 0]
    if len(xs) < 20:
        return None
    return math.sqrt(sum(xs) / (4 * math.log(2) * len(xs))) * math.sqrt(YEAR_HOURS / bar_hours)


def fold(bars: Sequence[dict[str, Any]], span_seconds: int) -> list[dict[str, Any]]:
    """Bars folded into `span_seconds` buckets that start at `time // span * span` (UTC).
    The last bucket is dropped: it may still be forming."""
    out: list[dict[str, Any]] = []
    for b in bars:
        start = int(b["time"]) // span_seconds * span_seconds
        if out and out[-1]["time"] == start:
            cur = out[-1]
            cur["high"] = max(cur["high"], b["high"])
            cur["low"] = min(cur["low"], b["low"])
            cur["close"] = b["close"]
            cur["volume"] += b.get("volume", 0.0)
        else:
            out.append({"time": start, "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"], "volume": b.get("volume", 0.0)})
    return out[:-1]
