"""A backtest of one directional strategy on the stored perpetual bars — the same `decide()` the
runner trades, bar by bar, with nothing from the future.

    SIGNAL   `decide()` on the bars up to and including the one that just closed.
    ENTRY    the NEXT bar's open: the runner acts after a close, at whatever the market then is.
    EXIT     the strategy's own stop and target, read on each later bar's range. A bar that touches
             both is a stop (the order inside a bar is unknown, so the worse one is assumed); a bar
             that opens past the stop fills at its open. Or the open after `max_hold_hours`.
    COSTS    `cost_pct` per round trip (fees both ways and the spread) and `funding_pct` for every
             eight hours held, both taken off every trade.

Results are in % of notional per trade and in R (multiples of the planned risk). Walk-forward folds
show whether the result holds across time, and a Monte Carlo reshuffle of the trades what drawdown
the same trades could have produced in another order.

    python -m app.backtest.perp supertrend --underlying BTC
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from typing import Any

from app.analysis import resample
from app.analysis.service import candles
from app.market.options import PERPETUALS
from app.strategies.base import Context, Strategy

FUNDING_HOURS = 8.0


@dataclass(frozen=True)
class Trade:
    side: int  # +1 long, -1 short
    opened: int  # bar time of the entry
    closed: int
    entry: float
    exit: float
    risk_pct: float  # planned distance to the stop, % of entry
    pnl_pct: float  # after costs and funding
    reason: str

    @property
    def r(self) -> float:
        return self.pnl_pct / self.risk_pct if self.risk_pct > 0 else 0.0


def simulate(
    strategy: Strategy, bars: list[dict[str, Any]], symbol: str, underlying: str,
    cost_pct: float, funding_pct: float = 0.0, max_hold_hours: float = 24.0,
) -> list[Trade]:
    """Every trade `strategy` would have taken on `bars` (its own interval, oldest first), one at a time."""
    span = resample.seconds(strategy.interval)
    max_bars = max(1, int(max_hold_hours * 3600 / span))
    trades: list[Trade] = []
    i = strategy.lookback - 1
    while i < len(bars) - 1:
        window = candles(bars[max(0, i - strategy.lookback + 1): i + 1])
        d = strategy.decide(Context(symbol, underlying, strategy.interval, window, cost_pct=cost_pct))
        if not d.side or d.stop is None or d.target is None:
            i += 1
            continue
        side = 1 if d.side == "buy" else -1
        entry_bar = i + 1
        entry = bars[entry_bar]["open"]
        if (entry - d.stop) * side <= 0 or (d.target - entry) * side <= 0:
            i += 1  # the market opened past a level: the trade as planned no longer exists
            continue
        exit_price, reason, j = None, "", entry_bar
        last = min(len(bars) - 1, entry_bar + max_bars)
        while j <= last:
            bar = bars[j]
            if (bar["open"] - d.stop) * side <= 0 and j > entry_bar:
                exit_price, reason = bar["open"], "gap through the stop"
            elif (bar["low"] if side > 0 else bar["high"]) * side <= d.stop * side:
                exit_price, reason = d.stop, "stop"
            elif (bar["high"] if side > 0 else bar["low"]) * side >= d.target * side:
                exit_price, reason = d.target, "target"
            if exit_price is not None:
                break
            j += 1
        if exit_price is None:
            if last + 1 >= len(bars):
                break  # still open when the data ends: not a result
            j = last + 1
            exit_price, reason = bars[j]["open"], "max hold"
        held_hours = (bars[j]["time"] - bars[entry_bar]["time"]) / 3600 + span / 3600
        pnl = side * (exit_price - entry) / entry * 100 - cost_pct - funding_pct * held_hours / FUNDING_HOURS
        trades.append(Trade(side, int(bars[entry_bar]["time"]), int(bars[j]["time"]), entry, exit_price,
                            abs(entry - d.stop) / entry * 100, round(pnl, 6), reason))
        i = j  # one position at a time: the next signal is read from the bar the last one left on
    return trades


def summary(trades: list[Trade]) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p <= 0]
    n = len(pnls)
    return {
        "trades": n,
        "win_rate": round(100 * len(wins) / n, 1) if n else None,
        "total_pct": round(sum(pnls), 3),
        "expectancy_pct": round(sum(pnls) / n, 4) if n else None,
        "expectancy_r": round(sum(t.r for t in trades) / n, 3) if n else None,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "max_drawdown_pct": round(max_drawdown(pnls), 3),
    }


def max_drawdown(pnls: list[float]) -> float:
    total = peak = worst = 0.0
    for p in pnls:
        total += p
        peak = max(peak, total)
        worst = max(worst, peak - total)
    return worst


def walk_forward(trades: list[Trade], folds: int = 4) -> list[dict[str, Any]]:
    """The trades split into `folds` consecutive stretches of time, each summarised on its own. An
    edge is one that shows in most of them, not one carried by a single stretch."""
    if not trades:
        return []
    start, end = trades[0].opened, trades[-1].opened + 1
    width = (end - start) / folds
    return [summary([t for t in trades if start + k * width <= t.opened < start + (k + 1) * width]) for k in range(folds)]


def monte_carlo(trades: list[Trade], runs: int = 2000, seed: int = 7) -> dict[str, float | None]:
    """The same trades in `runs` random orders: the drawdown the order alone could have dealt."""
    pnls = [t.pnl_pct for t in trades]
    if len(pnls) < 2:
        return {"drawdown_p50_pct": None, "drawdown_p95_pct": None}
    rng = random.Random(seed)  # noqa: S311 - a reshuffle of trades, not a secret
    dds = []
    for _ in range(runs):
        rng.shuffle(pnls)
        dds.append(max_drawdown(pnls))
    dds.sort()
    return {"drawdown_p50_pct": round(dds[runs // 2], 3), "drawdown_p95_pct": round(dds[int(runs * 0.95)], 3)}


def report(strategy: Strategy, bars: list[dict[str, Any]], underlying: str, cost_pct: float, funding_pct: float, max_hold_hours: float) -> dict[str, Any]:
    trades = simulate(strategy, bars, PERPETUALS[underlying], underlying, cost_pct, funding_pct, max_hold_hours)
    return {
        "strategy": strategy.name, "underlying": underlying, "interval": strategy.interval, "bars": len(bars),
        "from": bars[0]["time"] if bars else None, "to": bars[-1]["time"] if bars else None,
        "costs": {"round_trip_pct": cost_pct, "funding_pct_per_8h": funding_pct},
        "overall": summary(trades),
        "walk_forward": walk_forward(trades),
        "monte_carlo": monte_carlo(trades),
        "exits": {r: sum(t.reason == r for t in trades) for r in ("target", "stop", "gap through the stop", "max hold")},
    }


def stored_bars(underlying: str, interval: str) -> list[dict[str, Any]]:
    """The strategy's own bars from the database: folded from the year of 15m bars, or from the
    month of 1m bars for a timeframe below 15m."""
    from app.market.feed import MarketFeed

    symbol = PERPETUALS[underlying]
    source = "15m" if resample.seconds(interval) >= 900 else "1m"
    return resample.fold(MarketFeed.bars(symbol, 100_000, source), source, interval)


def main() -> None:
    from app.config import get_config
    from app.core.db import init_db
    from app.strategies import REGISTRY

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("strategy", choices=sorted(REGISTRY))
    parser.add_argument("--underlying", default="BTC", choices=sorted(PERPETUALS))
    # Delta India perpetual taker fee ~0.05% each way plus about a tick of spread; check the product spec.
    parser.add_argument("--cost-pct", type=float, default=0.11, help="round trip: fees both ways and the spread, %% of notional")
    parser.add_argument("--funding-pct", type=float, default=0.01, help="paid per 8h held, %% of notional")
    parser.add_argument("--max-hold-hours", type=float, default=None, help="default: the strategy's own, else 24")
    args = parser.parse_args()

    strategy = REGISTRY[args.strategy]
    if strategy.wants_outlook or strategy.wants_structure or strategy.wants_options_history or strategy.wants_flow or strategy.wants_bias:
        parser.error(f"{args.strategy} reads the market desk, the options history, the tape or the day's bias, which this backtest does not replay")
    init_db(get_config().db_path)
    bars = stored_bars(args.underlying, strategy.interval)
    hold = args.max_hold_hours or strategy.max_hold_hours or 24.0
    print(json.dumps(report(strategy, bars, args.underlying, args.cost_pct, args.funding_pct, hold), indent=2))


if __name__ == "__main__":
    main()
