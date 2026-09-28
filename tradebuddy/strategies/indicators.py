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
