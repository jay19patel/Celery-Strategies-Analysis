"""Fibonacci retracements and extensions, from the swings the structure module already finds.

A fib level is worth exactly as much as the leg it was drawn from, which is the part most charts
hide: move the anchors and every level moves with them. So this module does not let a caller
choose anchors. The leg is **the last completed impulse** — from the most recent opposing swing to
the extreme that followed it — found by the same `swings()` the rest of the system reads, with the
same lookback. Two people asking this module the same question at the same minute get the same
numbers, which is the only property that makes a level countable later.

  retracement   Where a pullback inside the leg tends to stall: .382 .5 .618 .786. The .618-.65
                band is marked `golden` because that is the one people actually act on, not
                because the arithmetic prefers it.

  extension     Where the leg projects to beyond its own end: 1.272 1.414 1.618 2.0 2.618.
                This is the one that earns its place here. Everything else in the system says
                where price might REACT; an extension says how FAR a move that keeps going would
                travel — and `options/zerohero.py` needs a distance, not a direction, to answer
                whether a contract can multiply before the clock takes it.

Nothing here is predictive and nothing is fitted. It is one leg, measured, divided by constants.
Whether price respects any of it is `nearby.py`'s confluence count and the verdict record's
business, not this module's.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.analysis.structure import DEFAULT_LOOKBACK, swings
from app.market.delta import Candle

RETRACEMENTS = (0.236, 0.382, 0.5, 0.618, 0.786)
EXTENSIONS = (1.272, 1.414, 1.618, 2.0, 2.618)
GOLDEN = (0.618, 0.786)  # the band between these two is the "golden pocket"
MIN_BARS = 30


@dataclass(frozen=True)
class Level:
    ratio: float
    price: float
    kind: str  # retracement | extension
    label: str  # "0.618" / "1.618"
    golden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Leg:
    """The impulse the levels are drawn from: low to high, or high to low."""

    direction: str  # "up" | "down"
    start_price: float
    end_price: float
    start_time: int
    end_time: int

    @property
    def size(self) -> float:
        return abs(self.end_price - self.start_price)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"size": round(self.size, 2)}


@dataclass
class Fib:
    ready: bool
    note: str = ""
    leg: Leg | None = None
    levels: list[Level] = field(default_factory=list)
    price: float | None = None
    retraced: float | None = None  # how far back into the leg price has come, 0 at the end, 1 at the start
    in_golden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready, "note": self.note,
            "leg": self.leg.to_dict() if self.leg else None,
            "levels": [v.to_dict() for v in self.levels],
            "price": self.price, "retraced": self.retraced, "in_golden": self.in_golden,
        }


def _label(ratio: float) -> str:
    return f"{ratio:.3f}".rstrip("0").rstrip(".")


def levels_for(leg: Leg) -> list[Level]:
    """Every retracement inside the leg and every extension beyond its end."""
    span = leg.end_price - leg.start_price  # signed: positive on an up leg
    out = [
        Level(r, round(leg.end_price - span * r, 2), "retracement", _label(r), GOLDEN[0] <= r <= GOLDEN[1])
        for r in RETRACEMENTS
    ]
    out += [Level(r, round(leg.start_price + span * r, 2), "extension", _label(r)) for r in EXTENSIONS]
    return sorted(out, key=lambda v: v.price)


def last_leg(candles: list[Candle], lookback: int = DEFAULT_LOOKBACK) -> Leg | None:
    """The most recent completed impulse: the swing extreme nearest the end, back to the opposing
    extreme that preceded it.

    Direction comes from which extreme is more recent — if the running high was set after the
    running low, the leg is up. Both are read from `swings()`, so a leg is only ever drawn between
    bars that have closed.
    """
    if len(candles) < max(MIN_BARS, lookback + 2):
        return None
    highs, lows = swings(candles, lookback)
    top, bottom = highs[-1], lows[-1]
    if top is None or bottom is None or top <= bottom:
        return None

    # The bar each extreme was actually made on, scanning back over the lookback window.
    window = candles[-lookback:]
    hi_bar = max(window, key=lambda c: c.high)
    lo_bar = min(window, key=lambda c: c.low)
    if hi_bar.time == lo_bar.time or top - bottom <= 0:
        return None  # a flat window has no impulse, and ratios of zero are decoration

    if hi_bar.time > lo_bar.time:
        return Leg("up", lo_bar.low, hi_bar.high, lo_bar.time, hi_bar.time)
    return Leg("down", hi_bar.high, lo_bar.low, hi_bar.time, lo_bar.time)


def read(candles: list[Candle], lookback: int = DEFAULT_LOOKBACK) -> Fib:
    """The leg, its levels, and where price sits inside it."""
    if len(candles) < MIN_BARS:
        return Fib(ready=False, note=f"needs {MIN_BARS} bars, has {len(candles)}")
    leg = last_leg(candles, lookback)
    if leg is None or leg.size <= 0:
        return Fib(ready=False, note="no completed impulse in the lookback window")
    price = candles[-1].close
    span = leg.end_price - leg.start_price
    retraced = (leg.end_price - price) / span if span else None
    return Fib(
        ready=True, leg=leg, levels=levels_for(leg), price=round(price, 2),
        retraced=None if retraced is None else round(retraced, 4),
        in_golden=retraced is not None and GOLDEN[0] <= retraced <= GOLDEN[1],
    )


def next_extension(fib: Fib, direction: int, price: float) -> Level | None:
    """The first extension beyond `price` in `direction` (+1 up, -1 down): how far a move that
    keeps going would travel before it meets the leg's own projection.

    None when the leg points the other way — an up leg projects upward only, and inventing a
    downward target from it would be drawing a line to suit the trade.
    """
    if not fib.ready or fib.leg is None or direction == 0:
        return None
    if (fib.leg.direction == "up") != (direction > 0):
        return None
    beyond = [v for v in fib.levels if v.kind == "extension" and (v.price > price if direction > 0 else v.price < price)]
    return min(beyond, key=lambda v: abs(v.price - price)) if beyond else None


def nearest(fib: Fib, price: float, kind: str = "") -> Level | None:
    pool = [v for v in fib.levels if not kind or v.kind == kind]
    return min(pool, key=lambda v: abs(v.price - price)) if pool else None
