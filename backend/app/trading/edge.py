"""What each strategy has earned so far, measured, and what that earns it in size.

Every closed trade is read in R: its realised P&L over what it risked at entry (`max_loss`: the
stop's loss for a perpetual, the worst case for a defined-risk option structure, the margin for an
undefined one). Over a strategy's trades the mean R is its expectancy, and a bootstrap of those
trades gives the range that mean could plausibly have come from. Under `MIN_TRADES` there is no
measurement, only a hope, and the strategy keeps the configured risk.

Size follows the measurement, never ahead of it:

  - proven positive (the whole 95% range above zero): a quarter of the Kelly fraction, computed on
    the low end of the range, never less than the configured risk nor more than `MAX_SCALE` times it;
  - proven negative (the whole range below zero): nothing — the auto-trader stops taking it;
  - measured but losing on average, not proven either way: half the risk;
  - on real money, anything not yet proven positive: nothing. Paper is where an edge is shown.

A drawdown from the broker's equity peak halves the risk at `DRAWDOWN_HALVE` and quarters it at
`DRAWDOWN_DEEP`, until the losses are made back. It never stops entries outright: a broker that
cannot trade cannot make anything back, and the daily loss limit is what halts a bad day.
Pure: trades in, numbers out.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Any

MIN_TRADES = 30
KELLY_SHARE = 0.25
MAX_SCALE = 2.0
DRAWDOWN_HALVE, DRAWDOWN_DEEP = 0.10, 0.20
RESAMPLES = 2000


def r_multiple(pnl: float | None, risk: float | None) -> float | None:
    return None if pnl is None or not risk or risk <= 0 else pnl / risk


def bootstrap_mean(xs: Sequence[float], level: float = 0.95, runs: int = RESAMPLES) -> tuple[float, float]:
    """The range the mean of `xs` falls in for `level` of resamples of the same trades. Seeded, so
    the same trades always read the same. Past a few hundred trades the mean is normal enough that
    the closed form gives the same range without the cost."""
    n = len(xs)
    if n >= 300:
        mean = sum(xs) / n
        half = 1.96 * math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1) / n)
        return mean - half, mean + half
    rng = random.Random(n)  # noqa: S311 - a resample, not a secret
    means = sorted(sum(rng.choices(xs, k=n)) / n for _ in range(runs))
    tail = (1 - level) / 2
    return means[int(tail * runs)], means[min(runs - 1, int((1 - tail) * runs))]


def measure(rs: Sequence[float]) -> dict[str, Any]:
    """Expectancy in R with its 95% range, and the verdict that range supports."""
    n = len(rs)
    if n == 0:
        return {"trades_r": 0, "expectancy_r": None, "ci_low_r": None, "ci_high_r": None, "kelly": None, "verdict": "unproven"}
    mean = sum(rs) / n
    lo, hi = bootstrap_mean(rs) if n >= 2 else (mean, mean)
    second = sum(r * r for r in rs) / n
    if n < MIN_TRADES:
        verdict = "unproven"
    elif lo > 0:
        verdict = "positive"
    elif hi < 0:
        verdict = "negative"
    else:
        verdict = "unclear"
    return {
        "trades_r": n,
        "expectancy_r": round(mean, 4),
        "ci_low_r": round(lo, 4),
        "ci_high_r": round(hi, 4),
        # The share of equity to risk a trade that grows it fastest, on the low end of the range.
        "kelly": round(max(0.0, lo) / second, 4) if second > 0 else None,
        "verdict": verdict,
    }


def scale(m: dict[str, Any], risk_pct: float, real_money: bool) -> tuple[float, str]:
    """How many times the configured risk this strategy is given, and why."""
    n, verdict = m["trades_r"], m["verdict"]
    if verdict == "negative":
        return 0.0, f"measured losing: {m['expectancy_r']:+.2f}R a trade over {n} trades, the whole range below zero"
    if real_money and verdict != "positive":
        return 0.0, f"not proven on paper yet ({n} trades, {verdict}): real money waits for a measured edge"
    if verdict == "positive":
        k = KELLY_SHARE * (m["kelly"] or 0.0) * 100 / risk_pct
        x = min(MAX_SCALE, max(1.0, k))
        return x, f"measured edge {m['ci_low_r']:+.2f}R..{m['ci_high_r']:+.2f}R over {n} trades: {x:.2f}x risk"
    if verdict == "unclear" and (m["expectancy_r"] or 0.0) < 0:
        return 0.5, f"losing on average over {n} trades ({m['expectancy_r']:+.2f}R), not proven either way: half risk"
    return 1.0, ""


def drawdown(equity: float, pnls: Sequence[float]) -> float:
    """How far `equity` is below the highest it has stood, read back through the realised P&L of
    the broker's closed trades in the order they closed."""
    start = equity - sum(pnls)
    run = peak = start
    for p in pnls:
        run += p
        peak = max(peak, run)
    peak = max(peak, equity)
    return (peak - equity) / peak if peak > 0 else 0.0


def drawdown_scale(dd: float) -> tuple[float, str]:
    if dd >= DRAWDOWN_DEEP:
        return 0.25, f"{100 * dd:.1f}% below the equity peak: quarter risk"
    if dd >= DRAWDOWN_HALVE:
        return 0.5, f"{100 * dd:.1f}% below the equity peak: half risk"
    return 1.0, ""
