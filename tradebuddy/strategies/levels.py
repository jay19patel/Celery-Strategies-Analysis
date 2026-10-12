"""Turning a level off the chart into the percentages a Signal carries.

A fixed "stop at 0.5%" is a different trade in a quiet market than in a fast one, and it ignores
where the trade would actually be wrong. A strategy that knows its own invalidation level — the
Supertrend it is following, the EMA it pulled back to, the candle it broke out of — should say so,
and these helpers turn that price into the `stop_loss_pct` / `take_profit_pct` a Signal needs.

Two guards, because a level sitting on top of the price is not a stop:

    floor_pct    the stop is never closer than this (pass a multiple of ATR%), so it is never
                 inside the spread and the noise
    RANGES       both percentages are clamped to what Settings allows, so a Signal is never
                 refused by `runner.levels_error` for being out of range
"""

from __future__ import annotations

from tradebuddy.settings import RANGES


def from_stop(side: str, price: float, stop: float, reward: float = 2.0, floor_pct: float = 0.0) -> tuple[float, float] | None:
    """(stop %, target %) for a stop at `stop` and a target `reward` times as far from `price`.

    None when the level is on the wrong side of the price — then the strategy has no trade, rather
    than a stop that would be hit the moment it is placed."""
    if price <= 0 or stop <= 0:
        return None
    distance = (price - stop) if side == "buy" else (stop - price)
    if distance <= 0:
        return None
    sl = _clamp(max(100 * distance / price, floor_pct), "stop_loss_pct")
    return sl, _clamp(sl * reward, "take_profit_pct")


def _clamp(value: float, field: str) -> float:
    low, high = RANGES[field]
    return min(max(value, low), high)
