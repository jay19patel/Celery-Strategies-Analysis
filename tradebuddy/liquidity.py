"""Where the stops are, and what happened when price went and took them.

`structure.py` answers "did a level break". This answers the question underneath it: **which levels
are worth breaking**, and whether the break was real or a raid. The difference is most of what
separates a trade from a donation.

  pivot          A high with `n` lower highs on each side. Marked only once those `n` bars have
                 CLOSED, so a pivot is never known on the bar it happened — which is exactly the
                 rule a backtest has to obey and a chart usually does not.

  pool           Two or more pivots at the same price, within a tolerance set by ATR rather than a
                 percentage. Equal highs are not a coincidence: they are resting stop orders, and
                 everyone can see them. Touches are counted, because a level tested four times is
                 a different proposition from one tested twice.

  sweep          Price trades THROUGH a pool and closes back inside it. That is the signature of
                 the stops being taken rather than the level giving way, and it is the single most
                 useful pattern on this page — a failed break tells you more than a clean one.
                 Depth is in ATRs; the reaction is measured `react_bars` later and is left
                 unresolved until those bars exist. A sweep with no reaction yet is reported as a
                 sweep with no reaction yet, never as a failed one.

  reference      Previous day, week and month high/low/open/close, and each session's range. These
                 matter for no cleverer reason than that everybody watches them, which is enough.

Sessions are UTC and they overlap on purpose — London opens while Asia is still trading, and the
London/New York overlap is when most of the day's range is made. A crypto perpetual has no close,
so these are the only structure the clock gives it.

Pure: candles in, dataclasses out. No clock — "now" is the last bar — no network, no database.
"""

from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from tradebuddy.delta import Candle

DAY = 86_400
# Epoch day 0 was a Thursday, so Monday is day 4. Used to find the week a bar belongs to without
# pulling in a calendar for something floor division answers.
MONDAY_OFFSET = 4

PIVOT_BARS = 3  # lower highs either side; 5 on a higher timeframe
POOL_TOLERANCE_ATR = 0.1  # pivots this close together are the same level
MIN_POOL_TOUCHES = 2  # one pivot is a high, two at a price is a pool
SWEEP_REACTION_BARS = 5
MAX_POOLS = 12  # the nearest ones; a chart with forty lines on it has none
MIN_SWEEP_SAMPLE = 30  # fewer resolved sweeps than this and no rate is called an edge
# How far back the sweep scan goes. Not a performance knob dressed up as a rule: over 2,000 bars
# the sweep record reads +3.3 points of edge and over 35,000 it reads +0.2, which is what a few
# hundred samples of a coin look like. A wider window is a better answer AND a slower one, so the
# window is stated here and the baseline beside every rate is what keeps it honest either way.
SWEEP_SCAN_BARS = 5_000

# UTC hours, start inclusive, end exclusive. They overlap because the market does.
SESSIONS = (
    ("asia", 0, 8),
    ("london", 7, 16),
    ("newyork", 13, 22),
)
SESSION_LABEL = {"asia": "Asia", "london": "London", "newyork": "New York"}


@dataclass(frozen=True)
class Pivot:
    """A confirmed turning point: `bars` lower highs (or higher lows) on each side."""

    kind: str  # "high" | "low"
    price: float
    bar_time: int
    index: int
    confirmed_index: int  # the bar by which this was knowable — never earlier than index + bars

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Pool:
    """Pivots stacked at one price: resting liquidity, and everyone can see it."""

    kind: str  # "high" (stops above) | "low" (stops below)
    price: float
    touches: int
    first_time: int
    last_time: int
    swept: bool = False
    swept_at: int | None = None
    distance_pct: float | None = None  # from the latest close; negative means below

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Sweep:
    """Price went through a pool and closed back inside it. The stops were taken; the level held."""

    direction: str  # "high" = highs swept, a bearish failure | "low" = lows swept, bullish
    level: float
    extreme: float  # how far through it actually traded
    depth_atr: float
    bar_time: int
    index: int
    touches: int  # how much liquidity the pool had behind it
    resolved: bool  # have `react_bars` closed since?
    reaction_pct: float | None  # the move after it, the way the sweep implies
    worked: bool | None  # did price go the way a sweep suggests

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Level:
    """A reference price everybody is looking at."""

    name: str
    label: str
    price: float
    kind: str  # "high" | "low" | "open" | "close"
    period: str  # "day" | "week" | "month" | "session"
    at: int  # the window it came from
    distance_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -- the clock ------------------------------------------------------------------------


def day_start(ts: int) -> int:
    return int(ts) // DAY * DAY


def week_start(ts: int) -> int:
    """00:00 UTC on the Monday of this bar's week."""
    days = int(ts) // DAY
    return ((days - MONDAY_OFFSET) // 7 * 7 + MONDAY_OFFSET) * DAY


def month_start(ts: int) -> int:
    d = datetime.fromtimestamp(int(ts), tz=UTC)
    return int(datetime(d.year, d.month, 1, tzinfo=UTC).timestamp())


def sessions_at(ts: int) -> list[str]:
    """Which sessions are open at this bar. More than one, often — that is the point."""
    hour = (int(ts) % DAY) / 3600
    return [name for name, lo, hi in SESSIONS if lo <= hour < hi]


# -- pivots, and the pools they stack into --------------------------------------------


def pivots(candles: list[Candle], bars: int = PIVOT_BARS) -> list[Pivot]:
    """Confirmed turning points, oldest first.

    `bars` lower highs on each side for a pivot high, and the mirror for a low. The right-hand
    side is what makes it honest: the pivot at bar i is not knowable until bar i + bars has closed,
    so `confirmed_index` is what a strategy or a backtest is allowed to use, never `index`."""
    out: list[Pivot] = []
    for i in range(bars, len(candles) - bars):
        window = candles[i - bars : i + bars + 1]
        c = candles[i]
        # Strict on the left, loose on the right: with `>=` on both sides two equal highs next to
        # each other cancel and neither is marked — which is precisely the shape of a liquidity
        # pool, so it would hide the thing we are looking for.
        if c.high > max(b.high for b in window[:bars]) and c.high >= max(b.high for b in window[bars + 1 :]):
            out.append(Pivot("high", c.high, c.time, i, i + bars))
        if c.low < min(b.low for b in window[:bars]) and c.low <= min(b.low for b in window[bars + 1 :]):
            out.append(Pivot("low", c.low, c.time, i, i + bars))
    return sorted(out, key=lambda p: (p.confirmed_index, p.kind))


def pools(
    candles: list[Candle], atr: float, found: list[Pivot] | None = None,
    tolerance_atr: float = POOL_TOLERANCE_ATR, min_touches: int = MIN_POOL_TOUCHES,
) -> list[Pool]:
    """Pivots clustered into levels, nearest the current price first.

    The tolerance is in ATRs, not percent: "within 0.1%" means something different on a quiet day
    than a violent one, and the whole question here is whether two highs are close enough that one
    set of stops sits behind both."""
    if not candles or atr <= 0:
        return []
    found = pivots(candles) if found is None else found
    tolerance = tolerance_atr * atr
    out: list[Pool] = []
    for kind in ("high", "low"):
        same = sorted((p for p in found if p.kind == kind), key=lambda p: p.price)
        group: list[Pivot] = []
        for p in [*same, None]:  # a trailing None flushes the last group
            if group and (p is None or p.price - group[0].price > tolerance):
                if len(group) >= min_touches:
                    out.append(Pool(
                        kind=kind,
                        # The price the stops sit at is the extreme of the cluster, not its mean:
                        # a stop above "the highs" is above the highest of them.
                        price=round(max(g.price for g in group) if kind == "high" else min(g.price for g in group), 2),
                        touches=len(group),
                        first_time=min(g.bar_time for g in group),
                        last_time=max(g.bar_time for g in group),
                    ))
                group = []
            if p is not None:
                group.append(p)

    close = candles[-1].close
    # "Has price been back through this level since it formed?" asked once for the whole series
    # rather than once per pool. The obvious version rescans every candle for every candidate and
    # a year of bars makes thousands of candidates — that loop alone was a second of wall clock.
    times = [c.time for c in candles]
    best_high, best_low = _suffix_extremes(candles)
    for pool in out:
        pool.distance_pct = round(100 * (pool.price / close - 1), 3)
        after = bisect.bisect_right(times, pool.last_time)
        if after >= len(candles):
            continue
        pool.swept = best_high[after] > pool.price if pool.kind == "high" else best_low[after] < pool.price

    # Only the pools that survive the cut pay for the exact bar they were taken on.
    kept = sorted(out, key=lambda p: (p.swept, abs(p.distance_pct or 0)))[:MAX_POOLS]
    for pool in kept:
        if not pool.swept:
            continue
        beyond = (lambda c, pl=pool: c.high > pl.price) if pool.kind == "high" else (lambda c, pl=pool: c.low < pl.price)
        start = bisect.bisect_right(times, pool.last_time)
        pool.swept_at = next((c.time for c in candles[start:] if beyond(c)), None)
    return kept



def _suffix_extremes(candles: list[Candle]) -> tuple[list[float], list[float]]:
    """(highest high, lowest low) from each index to the end. Lets "did price ever come back
    through this" be an array lookup instead of a scan."""
    n = len(candles)
    best_high, best_low = [0.0] * n, [0.0] * n
    hi, lo = float("-inf"), float("inf")
    for i in range(n - 1, -1, -1):
        hi = max(hi, candles[i].high)
        lo = min(lo, candles[i].low)
        best_high[i], best_low[i] = hi, lo
    return best_high, best_low


def sweeps(
    candles: list[Candle], atr: float, found: list[Pool] | None = None,
    react_bars: int = SWEEP_REACTION_BARS, tolerance_atr: float = POOL_TOLERANCE_ATR,
) -> list[Sweep]:
    """Every bar that traded through a pool and closed back inside it, oldest first.

    Only pivots confirmed strictly BEFORE the bar are considered, so a bar can never sweep a level
    it helped to make. The reaction is left unresolved until `react_bars` have closed — an
    unfinished sweep is reported as unfinished, never as a failure.

    The pivot list is walked once with a moving pointer rather than refiltered on every bar: the
    obvious version is O(bars x pivots) and took most of a second over a year of 15m candles."""
    if len(candles) < 2 or atr <= 0:
        return []
    all_pivots = pivots(candles)  # already sorted by confirmed_index
    tolerance = tolerance_atr * atr
    seen: dict[str, list[float]] = {"high": [], "low": []}
    cursor = 0
    out: list[Sweep] = []
    for i, c in enumerate(candles):
        while cursor < len(all_pivots) and all_pivots[cursor].confirmed_index < i:
            p = all_pivots[cursor]
            bisect.insort(seen[p.kind], p.price)
            cursor += 1
        for kind in ("high", "low"):
            level = _nearest_pool_price(seen[kind], kind, c, tolerance)
            if level is None:
                continue
            through = c.high > level if kind == "high" else c.low < level
            back = c.close < level if kind == "high" else c.close > level
            if not (through and back):
                continue
            extreme = c.high if kind == "high" else c.low
            later = candles[i + 1 : i + 1 + react_bars]
            resolved = len(later) == react_bars
            # A high sweep is a bearish event, so "worked" means price fell afterwards.
            move = (later[-1].close / c.close - 1) if resolved else None
            reaction = None if move is None else (-move if kind == "high" else move)
            lo = bisect.bisect_left(seen[kind], level - tolerance)
            hi = bisect.bisect_right(seen[kind], level + tolerance)
            out.append(Sweep(
                direction=kind, level=round(level, 2), extreme=round(extreme, 2),
                depth_atr=round(abs(extreme - level) / atr, 2), bar_time=c.time, index=i,
                touches=hi - lo,
                resolved=resolved,
                reaction_pct=None if reaction is None else round(100 * reaction, 3),
                worked=None if reaction is None else bool(reaction > 0),
            ))
    return out


def _nearest_pool_price(prices: list[float], kind: str, c: Candle, tolerance: float) -> float | None:
    """The pooled level this bar breached, or None when there is no pool where it went.

    `prices` is kept sorted, so the candidates are the ones between the bar's open and its extreme
    — a level the bar opened beyond was not swept by it, it was already gone."""
    if len(prices) < MIN_POOL_TOUCHES:
        return None
    if kind == "high":
        window = prices[bisect.bisect_left(prices, c.open):bisect.bisect_right(prices, c.high)]
        window = window[::-1]  # the highest one it reached, first
    else:
        window = prices[bisect.bisect_left(prices, c.low):bisect.bisect_right(prices, c.open)]
    for price in window:
        lo = bisect.bisect_left(prices, price - tolerance)
        hi = bisect.bisect_right(prices, price + tolerance)
        if hi - lo >= MIN_POOL_TOUCHES:
            return price
    return None


def sweep_record(found: list[Sweep], candles: list[Candle], react_bars: int = SWEEP_REACTION_BARS) -> dict[str, Any]:
    """How often a sweep was followed by the move it implies — beside how often ANY bar was.

    A rate on its own is not a finding. If price fell over the next five bars on 49% of all bars,
    then "sweeps of the highs were followed by a fall 51% of the time" is the market, not the
    pattern. Unresolved sweeps are excluded from the rate and counted separately, so a quiet week
    cannot flatter it."""
    done = [s for s in found if s.resolved]
    # The baseline: over every bar in the same series, how often did price fall (or rise) over the
    # same horizon. This is what the sweep has to beat to be worth drawing.
    moves = [candles[i + react_bars].close / candles[i].close - 1 for i in range(len(candles) - react_bars)]
    fell = sum(1 for m in moves if m < 0) / len(moves) if moves else None
    base = {"high": fell, "low": None if fell is None else 1 - fell}
    by_dir: dict[str, dict[str, Any]] = {}
    for kind in ("high", "low"):
        group = [s for s in done if s.direction == kind]
        worked = sum(1 for s in group if s.worked)
        rate = round(worked / len(group), 4) if group else None
        baseline = None if base[kind] is None else round(base[kind], 4)
        by_dir[kind] = {
            "n": len(group), "worked": worked, "rate": rate, "baseline": baseline,
            "edge": None if rate is None or baseline is None else round(rate - baseline, 4),
            "enough": len(group) >= MIN_SWEEP_SAMPLE,
            "mean_reaction_pct": round(sum(s.reaction_pct or 0 for s in group) / len(group), 3) if group else None,
        }
    return {
        "total": len(found), "resolved": len(done), "pending": len(found) - len(done),
        "react_bars": react_bars, "min_sample": MIN_SWEEP_SAMPLE, "by_direction": by_dir,
    }


# -- the levels everybody watches -----------------------------------------------------


def _ohlc(group: list[Candle]) -> dict[str, float]:
    return {
        "open": group[0].open, "high": max(c.high for c in group),
        "low": min(c.low for c in group), "close": group[-1].close,
    }


def reference_levels(candles: list[Candle]) -> list[Level]:
    """Previous day, week and month high/low/open/close, plus the current day's open.

    Only *completed* periods. The current day's high is not a level, it is a number that is still
    moving, and drawing it as one is how a chart ends up with a support that has never held."""
    if not candles:
        return []
    close = candles[-1].close
    now = candles[-1].time
    out: list[Level] = []
    for period, starter, label in (("day", day_start, "day"), ("week", week_start, "week"), ("month", month_start, "month")):
        current = starter(now)
        group = [c for c in candles if starter(c.time) == starter(current - 1)]
        if not group:
            continue
        values = _ohlc(group)
        short = label[0].upper()
        for key in ("high", "low", "open", "close"):
            price = round(values[key], 2)
            out.append(Level(
                name=f"P{short}{key[0].upper()}", label=f"Prev {label} {key}", price=price,
                kind=key, period=period, at=starter(current - 1),
                distance_pct=round(100 * (price / close - 1), 3),
            ))
    today = [c for c in candles if day_start(c.time) == day_start(now)]
    if today:
        price = round(today[0].open, 2)
        out.append(Level(name="DO", label="Day open", price=price, kind="open", period="day",
                         at=day_start(now), distance_pct=round(100 * (price / close - 1), 3)))
    return out


def session_ranges(candles: list[Candle], days: int = 2) -> list[dict[str, Any]]:
    """Each session's range over the last `days` UTC days, newest last.

    `complete` says whether the session has finished. An unfinished session's high is not a level
    yet, and the page must be able to tell the two apart."""
    if not candles:
        return []
    last = candles[-1].time
    first_day = day_start(last) - (days - 1) * DAY
    out = []
    for day in range(first_day, day_start(last) + DAY, DAY):
        for name, lo, hi in SESSIONS:
            start, end = day + lo * 3600, day + hi * 3600
            group = [c for c in candles if start <= c.time < end]
            if not group:
                continue
            values = _ohlc(group)
            out.append({
                "session": name, "label": SESSION_LABEL[name], "day": day, "start": start, "end": end,
                "complete": bool(last >= end), "bars": len(group),
                **{k: round(v, 2) for k, v in values.items()},
                "range_pct": round(100 * (values["high"] / values["low"] - 1), 3) if values["low"] else None,
            })
    return out


# -- one reading ----------------------------------------------------------------------


@dataclass
class Liquidity:
    ready: bool
    note: str = ""
    pools: list[Pool] = field(default_factory=list)
    sweeps: list[Sweep] = field(default_factory=list)
    record: dict[str, Any] = field(default_factory=dict)
    levels: list[Level] = field(default_factory=list)
    sessions: list[dict[str, Any]] = field(default_factory=list)
    nearest_above: Pool | None = None
    nearest_below: Pool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready, "note": self.note,
            "pools": [p.to_dict() for p in self.pools],
            "sweeps": [s.to_dict() for s in self.sweeps[-20:]],
            "record": self.record,
            "levels": [level.to_dict() for level in self.levels],
            "sessions": self.sessions,
            "nearest_above": self.nearest_above.to_dict() if self.nearest_above else None,
            "nearest_below": self.nearest_below.to_dict() if self.nearest_below else None,
        }


MIN_BARS = 60


def read(candles: list[Candle], atr: float, react_bars: int = SWEEP_REACTION_BARS) -> Liquidity:
    """Everything above, for one symbol on one timeframe."""
    if len(candles) < MIN_BARS:
        return Liquidity(ready=False, note=f"needs {MIN_BARS} bars, has {len(candles)}")
    if atr <= 0:
        return Liquidity(ready=False, note="no ATR to scale the tolerances with")
    found = pools(candles, atr)
    scan = candles[-SWEEP_SCAN_BARS:] if len(candles) > SWEEP_SCAN_BARS else candles
    raids = sweeps(scan, atr, react_bars=react_bars)
    resting = [p for p in found if not p.swept]
    return Liquidity(
        ready=True,
        pools=found,
        sweeps=raids,
        record=sweep_record(raids, scan, react_bars) | {"scanned_bars": len(scan)},
        levels=reference_levels(candles),
        sessions=session_ranges(candles),
        nearest_above=min((p for p in resting if (p.distance_pct or 0) > 0), key=lambda p: p.distance_pct or 0, default=None),
        nearest_below=max((p for p in resting if (p.distance_pct or 0) < 0), key=lambda p: p.distance_pct or 0, default=None),
    )


def untouched(found: list[Pool]) -> list[Pool]:
    """Pools price has not been back to. What is left to go and take."""
    return [p for p in found if not p.swept]
