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


def crossed_above(a: list[float], b: list[float]) -> bool:
    return len(a) >= 2 and len(b) >= 2 and a[-2] <= b[-2] and a[-1] > b[-1]


def crossed_below(a: list[float], b: list[float]) -> bool:
    return len(a) >= 2 and len(b) >= 2 and a[-2] >= b[-2] and a[-1] < b[-1]


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


def adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> tuple[list[float], list[float], list[float]]:
    """Wilder's ADX with +DI and -DI, aligned to the end.

    ADX measures how *strongly* price is trending, not which way — it is the one reading that
    separates a trend from a range, and nothing else here does that. Below 20 is chop, where
    breakout rules misfire; above 25 a trend is worth following.
    """
    n = len(closes)
    if n < 2 * period + 1:
        return [], [], []
    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, n):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    def wilder(series: list[float]) -> list[float]:
        out = [sum(series[:period])]
        for v in series[period:]:
            out.append(out[-1] - out[-1] / period + v)
        return out

    atr_s, plus_s, minus_s = wilder(tr), wilder(plus_dm), wilder(minus_dm)
    pdi = [100 * p / a if a else 0.0 for p, a in zip(plus_s, atr_s, strict=True)]
    mdi = [100 * m / a if a else 0.0 for m, a in zip(minus_s, atr_s, strict=True)]
    dx = [100 * abs(p - m) / (p + m) if (p + m) else 0.0 for p, m in zip(pdi, mdi, strict=True)]
    if len(dx) < period:
        return [], pdi, mdi
    out = [sum(dx[:period]) / period]
    for v in dx[period:]:
        out.append((out[-1] * (period - 1) + v) / period)
    return out, pdi[-len(out):], mdi[-len(out):]


def bollinger(values: list[float], period: int = 20, mult: float = 2.0) -> tuple[list[float], list[float], list[float]]:
    """(middle, upper, lower), aligned to the end."""
    if len(values) < period:
        return [], [], []
    mid, up, low = [], [], []
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        m = sum(window) / period
        sd = (sum((v - m) ** 2 for v in window) / period) ** 0.5
        mid.append(m)
        up.append(m + mult * sd)
        low.append(m - mult * sd)
    return mid, up, low


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
