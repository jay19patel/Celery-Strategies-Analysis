"""Indicators on plain lists. Outputs are aligned to the END of the input."""

from __future__ import annotations

import itertools


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
