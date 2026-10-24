"""The few price measures the regime reads: EMA, RSI, realised volatility, and bars folded to
a higher timeframe. Pure functions over closes; no smoothing beyond what each one names."""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from typing import Any

YEAR_HOURS = 365 * 24


def ema(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder's RSI of the last bar, or None with too little history."""
    if len(closes) <= period:
        return None
    gains = [max(0.0, b - a) for a, b in itertools.pairwise(closes)]
    losses = [max(0.0, a - b) for a, b in itertools.pairwise(closes)]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:], strict=True):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


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
