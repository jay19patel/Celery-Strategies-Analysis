"""Market structure from closed bars: where the levels are, when one broke, and which way.

Pure and deterministic — lists in, dataclasses out, no network and no clock. So the same function
answers the dashboard, a strategy on a Celery worker, and the scan over a year of stored bars that
tells us how often each of these actually led anywhere.

What it reads, and why each one is here rather than another oscillator:

  swing high / low   the level the market has to clear. Everything below is about one of these.
  BOS                a close through the prior swing — the "breakout". Tested as a *fresh cross*,
                     not as `close > level`: the level stays broken for hours afterwards, and a
                     condition that is true every bar is not an event. (pcr_options v1 made exactly
                     this mistake and signalled 262 times in 70 hours.)
  CHoCH              a BOS against the prevailing structure: the first sign a trend is turning,
                     as opposed to one continuing.
  FVG                a three-bar gap nobody traded through. Price tends to come back to it, so it
                     is a level to expect a reaction at, not a direction.
  displacement       how big the breaking bar was against the recent average. A break on an
                     ordinary bar and a break on a three-times-average bar are different events.
  squeeze            Bollinger width far below its own average: the range before an expansion.

Nothing here predicts anything. It says what happened and where the levels are; `outlook.py` is
what turns that into a probability, and only ever from counted history.
"""

from __future__ import annotations

import itertools
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from tradebuddy.delta import Candle

DEFAULT_LOOKBACK = 20  # bars either side of a swing: 5 hours on 15m, 20 hours on 1h
MEAN_WINDOW = 20  # what "an ordinary bar" means, for displacement and volume
SQUEEZE_PERIOD = 20
SQUEEZE_WINDOW = 50
SQUEEZE_RATIO = 0.6  # band width this far under its own average is a squeeze
RECENT_BREAKS = 6


@dataclass(frozen=True)
class Break:
    """A close through a prior swing level."""

    kind: str  # "bos" (continuation or first break) | "choch" (against the prevailing structure)
    direction: str  # "up" | "down"
    bar_time: int
    level: float  # the swing that broke
    close: float
    displacement: float  # this bar's range / the mean of the last MEAN_WINDOW
    volume_ratio: float  # this bar's volume / the same mean
    index: int  # position in the series it was found in

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Gap:
    """A fair value gap: three bars where the middle one left a range nobody traded through."""

    direction: str  # "up" (gap below price) | "down" (gap above price)
    low: float
    high: float
    bar_time: int
    filled: bool  # price has since traded back into it

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Structure:
    bias: str  # "bullish" (higher high and higher low) | "bearish" | "range"
    swing_high: float | None
    swing_low: float | None
    position: float | None  # 0 at the swing low, 1 at the swing high
    breaks: list[Break] = field(default_factory=list)  # most recent last
    gaps: list[Gap] = field(default_factory=list)  # unfilled only
    squeeze: bool = False
    bars: int = 0

    @property
    def last_break(self) -> Break | None:
        return self.breaks[-1] if self.breaks else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bias": self.bias, "swing_high": self.swing_high, "swing_low": self.swing_low,
            "position": self.position, "squeeze": self.squeeze, "bars": self.bars,
            "breaks": [b.to_dict() for b in self.breaks],
            "gaps": [g.to_dict() for g in self.gaps],
        }


# -- the pieces ---------------------------------------------------------------------


def rolling(values: list[float], n: int, fn) -> list[float | None]:
    """fn over the last `n` values, ending at each bar. None until there are n of them."""
    return [fn(values[i - n + 1 : i + 1]) if i >= n - 1 else None for i in range(len(values))]


def swings(candles: list[Candle], lookback: int = DEFAULT_LOOKBACK) -> tuple[list[float | None], list[float | None]]:
    """The highest high and lowest low of the last `lookback` bars, per bar."""
    return (
        rolling([c.high for c in candles], lookback, max),
        rolling([c.low for c in candles], lookback, min),
    )


def find_breaks(candles: list[Candle], lookback: int = DEFAULT_LOOKBACK) -> list[Break]:
    """Every fresh close through a prior swing, oldest first.

    "Prior" matters: the level compared against is the swing as it stood on the *previous* bar, so
    a bar cannot break a level it set itself."""
    if len(candles) < lookback + 2:
        return []
    highs, lows = swings(candles, lookback)
    ranges = [c.high - c.low for c in candles]
    volumes = [c.volume for c in candles]
    mean_range = rolling(ranges, MEAN_WINDOW, lambda w: sum(w) / len(w))
    mean_volume = rolling(volumes, MEAN_WINDOW, lambda w: sum(w) / len(w))

    out: list[Break] = []
    bias = "range"
    for i in range(lookback + 1, len(candles)):
        level_up, level_down = highs[i - 1], lows[i - 1]
        prev_up, prev_down = highs[i - 2], lows[i - 2]
        if level_up is None or level_down is None or prev_up is None or prev_down is None:
            continue
        close, before = candles[i].close, candles[i - 1].close
        up = close > level_up and before <= prev_up  # fresh: it was not already through
        down = close < level_down and before >= prev_down
        if not up and not down:
            bias = _bias_at(highs, lows, i, lookback) or bias
            continue
        direction = "up" if up else "down"
        # A break the other way from the prevailing structure is a change of character, not a
        # continuation — the difference between "the trend goes on" and "the trend may be over".
        against = (direction == "up" and bias == "bearish") or (direction == "down" and bias == "bullish")
        mr, mv = mean_range[i], mean_volume[i]
        out.append(Break(
            kind="choch" if against else "bos",
            direction=direction,
            bar_time=candles[i].time,
            level=level_up if up else level_down,
            close=close,
            displacement=round(ranges[i] / mr, 2) if mr else 1.0,
            volume_ratio=round(volumes[i] / mv, 2) if mv else 1.0,
            index=i,
        ))
        bias = _bias_at(highs, lows, i, lookback) or bias
    return out


def _bias_at(highs: list[float | None], lows: list[float | None], i: int, lookback: int) -> str | None:
    """Higher high and higher low against the swing a full window ago, or the mirror of it."""
    j = i - lookback
    if j < 0 or highs[i] is None or lows[i] is None or highs[j] is None or lows[j] is None:
        return None
    if highs[i] > highs[j] and lows[i] > lows[j]:
        return "bullish"
    if highs[i] < highs[j] and lows[i] < lows[j]:
        return "bearish"
    return "range"


def find_gaps(candles: list[Candle], limit: int = 40) -> list[Gap]:
    """Unfilled three-bar imbalances among the last `limit` bars, nearest the end last."""
    out: list[Gap] = []
    window = candles[-limit:] if len(candles) > limit else candles
    for i in range(2, len(window)):
        a, c = window[i - 2], window[i]
        if c.low > a.high:
            lo, hi, direction = a.high, c.low, "up"
        elif c.high < a.low:
            lo, hi, direction = c.high, a.low, "down"
        else:
            continue
        # Filled once any later bar trades back into it. A gap price has already revisited is
        # history, not a level to watch.
        later = window[i + 1 :]
        filled = any(b.low <= hi and b.high >= lo for b in later)
        if not filled:
            out.append(Gap(direction=direction, low=lo, high=hi, bar_time=c.time, filled=False))
    return out


def in_squeeze(closes: list[float], period: int = SQUEEZE_PERIOD, window: int = SQUEEZE_WINDOW) -> bool:
    """Bollinger width well under its own recent average: the coil before an expansion."""
    if len(closes) < period + window:
        return False
    widths = []
    for i in range(len(closes) - window, len(closes)):
        chunk = closes[i - period + 1 : i + 1]
        mid = sum(chunk) / period
        widths.append(4 * statistics.pstdev(chunk) / mid if mid else 0.0)
    average = sum(widths) / len(widths)
    return bool(average) and widths[-1] < SQUEEZE_RATIO * average


def read(candles: list[Candle], lookback: int = DEFAULT_LOOKBACK) -> Structure:
    """Where the market stands now."""
    if len(candles) < lookback + 2:
        return Structure(bias="range", swing_high=None, swing_low=None, position=None, bars=len(candles))
    highs, lows = swings(candles, lookback)
    hi, lo = highs[-1], lows[-1]
    close = candles[-1].close
    return Structure(
        bias=_bias_at(highs, lows, len(candles) - 1, lookback) or "range",
        swing_high=hi,
        swing_low=lo,
        position=round((close - lo) / (hi - lo), 3) if hi is not None and lo is not None and hi > lo else None,
        breaks=find_breaks(candles, lookback)[-RECENT_BREAKS:],
        gaps=find_gaps(candles),
        squeeze=in_squeeze([c.close for c in candles]),
        bars=len(candles),
    )


def bias_series(candles: list[Candle], lookback: int = DEFAULT_LOOKBACK) -> list[str | None]:
    """The structure bias at every bar — what a scan over history buckets on."""
    highs, lows = swings(candles, lookback)
    return [_bias_at(highs, lows, i, lookback) for i in range(len(candles))]


def resample(candles: list[Candle], factor: int) -> list[Candle]:
    """Fold `factor` bars into one, aligned to the boundary so an hourly bar is a real hour.

    The whole higher-timeframe view is built from the 15-minute bars already stored — no second
    download, and the hourly candle is exactly the four 15-minute ones the system already saw.
    A group missing any of its bars is dropped rather than closed early: a partial hour pretending
    to be an hour is what makes a trend look like it turned.
    """
    if factor <= 1:
        return list(candles)
    step = (candles[1].time - candles[0].time) if len(candles) > 1 else 0
    if step <= 0:
        return []
    span = step * factor
    groups: dict[int, list[Candle]] = {}
    for c in candles:
        groups.setdefault(c.time // span * span, []).append(c)
    out = []
    for start in sorted(groups):
        group = groups[start]
        if len(group) != factor:
            continue
        out.append(Candle(
            start, group[0].open, max(c.high for c in group), min(c.low for c in group),
            group[-1].close, sum(c.volume for c in group),
        ))
    return out


def realised_vol(closes: list[float], bars: int, bar_seconds: int) -> float | None:
    """Annualised realised volatility of the last `bars` log returns, as a fraction.

    The same definition insights._rv uses, taking the bar length as an argument so an hourly
    series and a 15-minute one come out on the same scale."""
    if len(closes) < bars + 1:
        return None
    window = closes[-bars - 1 :]
    rets = [math.log(b / a) for a, b in itertools.pairwise(window) if a > 0 and b > 0]
    if len(rets) < 2:
        return None
    per_year = 365 * 24 * 3600 / bar_seconds
    return statistics.stdev(rets) * math.sqrt(per_year)


# -- candlestick shapes ---------------------------------------------------------------

DOJI_BODY = 0.1  # body this small a share of the range is indecision, not a direction
WICK_RATIO = 2.0  # a hammer's lower wick is at least this many times its body
MARUBOZU_BODY = 0.9
CANDLE_NAMES = {
    "hammer": "Hammer", "shooting_star": "Shooting star", "bullish_engulfing": "Bullish engulfing",
    "bearish_engulfing": "Bearish engulfing", "doji": "Doji", "inside": "Inside bar",
    "outside": "Outside bar", "marubozu_bull": "Bullish marubozu", "marubozu_bear": "Bearish marubozu",
}


@dataclass(frozen=True)
class Shape:
    """One named candle, or pair of candles, on one bar.

    A shape is not a signal. A hammer in a downtrend at a level is one thing and a hammer in the
    middle of a range is noise, and the only way to tell them apart is to count what each has
    actually led to — which is why every shape is stored and scored like a break."""

    name: str
    direction: str  # "bull" | "bear" | "neutral"
    bar_time: int
    index: int
    strength: float  # 0..1: how pronounced the shape is, for sorting and for a threshold

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"label": CANDLE_NAMES.get(self.name, self.name)}


def _anatomy(c: Candle) -> tuple[float, float, float, float]:
    """(body, range, upper wick, lower wick). Everything below is a ratio of these."""
    rng = c.high - c.low
    body = abs(c.close - c.open)
    upper = c.high - max(c.open, c.close)
    lower = min(c.open, c.close) - c.low
    return body, rng, upper, lower


def find_shapes(candles: list[Candle], limit: int = 200) -> list[Shape]:
    """Named candle shapes over the last `limit` bars, oldest first.

    Only shapes with an agreed definition, each written as an explicit ratio rather than a feeling:
    a reader can check any of them against the bar it fired on.
    """
    window = candles[-limit:] if len(candles) > limit else candles
    offset = len(candles) - len(window)
    out: list[Shape] = []
    for i, c in enumerate(window):
        body, rng, upper, lower = _anatomy(c)
        if rng <= 0:
            continue
        found: list[tuple[str, str, float]] = []
        # A hammer is a long lower wick with the body at the top; a shooting star is its mirror.
        if body > 0 and lower >= WICK_RATIO * body and upper <= body:
            found.append(("hammer", "bull", lower / (WICK_RATIO * body) / 2))
        if body > 0 and upper >= WICK_RATIO * body and lower <= body:
            found.append(("shooting_star", "bear", upper / (WICK_RATIO * body) / 2))
        # Only if neither fired. A hammer has a small body and would also pass the doji test, but
        # the specific shape is the better description — and labelling one bar both "bull" and
        # "neutral" is exactly the kind of contradiction this page exists to remove.
        if not found:
            if body / rng <= DOJI_BODY:
                found.append(("doji", "neutral", 1 - body / rng))
            elif body / rng >= MARUBOZU_BODY:
                up = c.close > c.open
                found.append(("marubozu_bull" if up else "marubozu_bear", "bull" if up else "bear", body / rng))
        if i:
            prev = window[i - 1]
            pbody = abs(prev.close - prev.open)
            prange = prev.high - prev.low
            if c.high <= prev.high and c.low >= prev.low:
                found.append(("inside", "neutral", 1 - rng / prange if prange > 0 else 0.5))
            elif c.high > prev.high and c.low < prev.low:
                found.append(("outside", "bull" if c.close > c.open else "bear", rng / prange - 1 if prange > 0 else 0.5))
            # Engulfing: this body swallows the last one, and the two close opposite ways.
            if (pbody > 0 and body > pbody
                    and min(c.open, c.close) <= min(prev.open, prev.close)
                    and max(c.open, c.close) >= max(prev.open, prev.close)):
                if c.close > c.open and prev.close < prev.open:
                    found.append(("bullish_engulfing", "bull", body / pbody / 3))
                elif c.close < c.open and prev.close > prev.open:
                    found.append(("bearish_engulfing", "bear", body / pbody / 3))
        out += [
            Shape(name=n, direction=d, bar_time=c.time, index=offset + i, strength=round(min(1.0, max(0.0, st_)), 3))
            for n, d, st_ in found
        ]
    return out
