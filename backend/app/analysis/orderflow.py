"""Who was leaning on the market — estimated from candles, and labelled as an estimate.

**Everything in this module is approximate and says so.** Real order flow needs the trade tape:
every print, with the side it hit. This system does not record one yet — the feed subscribes to
tickers and candles only — so delta here is inferred from where each bar closed inside its own
range. That inference is a guess with a respectable track record and no guarantee at all:

    buy_volume = volume x (close - low) / (high - low)

A bar that closes on its high is called all buying, one that closes on its low all selling. It is
wrong whenever a bar was bought up and then sold back into the close, which happens constantly.
Every value leaves here with `approx = True` and the UI badges it APPROX. A footprint built from
candles is not a footprint, and calling it one is how a chart earns trust it has not got.

So why build it at all? Because it measures something nothing else here does. Across a year of
stored bars the approximated delta correlates 0.03-0.32 with every price-derived indicator in the
system — RSI, MACD, Bollinger position, distance to EMA, all of which correlate 0.8-0.97 with each
other. It is the one genuinely independent axis available without a tape, and an independent axis
with a weak signal beats a seventh copy of a strong one.

  delta        buying minus selling on a bar, as volume and as a share of it
  CVD          delta accumulated. Reset each UTC day, because an unbounded running total drifts
               and the question is who is winning *today*
  divergence   price makes a higher high while CVD does not (or the mirror). The classic reading:
               the move is being sold into. Counted against a baseline like everything else.
  absorption   heavy volume, small range, delta pushing the other way — somebody is filling size
               against the move. The one pattern here that genuinely wants tick data

Pure: candles in, numbers out. No clock, no network, no database.
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from typing import Any

from app.analysis.liquidity import day_start, pivots
from app.market.delta import Candle

APPROX_NOTE = "Estimated from candles, not from the trade tape."
DIVERGENCE_LOOKBACK = 60  # bars scanned for the two swings a divergence needs
# Heavy volume that went nowhere. Rare by construction, and the numbers say how rare: over 5,000
# stored 15m bars, 1.8x volume with under 0.7x range happened ZERO times, and 1.5x with under 0.9x
# happened ten. Volume and range are strongly correlated, so the conjunction is close to a
# contradiction. These are set where a handful survive — enough to mark on a chart, never enough
# for a hit rate, which is why absorptions are reported as markers and given no record.
ABSORPTION_VOLUME = 1.5  # times the recent average
ABSORPTION_RANGE = 0.9  # and no more than this much of the average range
MEAN_WINDOW = 20
MIN_SAMPLE = 30  # fewer resolved readings than this and no rate is reported
REACTION_BARS = 5


@dataclass
class Bar:
    """One bar's estimated flow."""

    time: int
    buy: float
    sell: float
    delta: float
    ratio: float  # delta as a share of the bar's volume: -1 all selling, +1 all buying
    cvd: float  # accumulated within the UTC day

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Divergence:
    kind: str  # "bearish" (price higher, flow not) | "bullish"
    bar_time: int
    index: int
    price: float
    cvd: float
    prior_time: int
    resolved: bool
    reaction_pct: float | None
    worked: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Absorption:
    """Heavy volume, little movement, flow pushing the other way."""

    direction: str  # "bull" (selling absorbed) | "bear"
    bar_time: int
    index: int
    volume_ratio: float
    range_ratio: float
    delta_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def bar_delta(c: Candle) -> tuple[float, float]:
    """(buy, sell) for one candle. A bar with no range splits evenly — it cannot say who won."""
    span = c.high - c.low
    volume = c.volume or 0.0
    if span <= 0:
        return volume / 2, volume / 2
    buy = volume * (c.close - c.low) / span
    return buy, volume - buy


def series(candles: list[Candle], reset_daily: bool = True) -> list[Bar]:
    """Per-bar delta and the cumulative total.

    Reset at 00:00 UTC by default. An unbounded CVD drifts with however much volume has been
    traded since the series began, which makes "CVD is rising" true for most of recorded history
    and useful for none of it."""
    out: list[Bar] = []
    running = 0.0
    day = None
    for c in candles:
        if reset_daily and day_start(c.time) != day:
            day, running = day_start(c.time), 0.0
        buy, sell = bar_delta(c)
        delta = buy - sell
        running += delta
        out.append(Bar(
            time=c.time, buy=round(buy, 4), sell=round(sell, 4), delta=round(delta, 4),
            ratio=round(delta / c.volume, 4) if c.volume else 0.0, cvd=round(running, 4),
        ))
    return out


def divergences(candles: list[Candle], flow: list[Bar], react_bars: int = REACTION_BARS,
                lookback: int = DIVERGENCE_LOOKBACK) -> list[Divergence]:
    """Price made a new extreme and the flow did not.

    Measured at confirmed pivots only, so a divergence is never called on the bar that made it —
    the same rule the rest of the system obeys. The prior pivot compared against is the most
    recent one of the same kind inside `lookback` bars."""
    if len(candles) < 20 or len(flow) != len(candles):
        return []
    cvd = [b.cvd for b in flow]
    out: list[Divergence] = []
    found = pivots(candles)
    for kind, same in (("high", [p for p in found if p.kind == "high"]), ("low", [p for p in found if p.kind == "low"])):
        for prev, now in itertools.pairwise(same):
            if now.index - prev.index > lookback:
                continue
            if kind == "high":
                diverged = now.price > prev.price and cvd[now.index] < cvd[prev.index]
                label = "bearish"
            else:
                diverged = now.price < prev.price and cvd[now.index] > cvd[prev.index]
                label = "bullish"
            if not diverged:
                continue
            # Known only once the pivot is confirmed, so that is where the reaction starts.
            at = now.confirmed_index
            later = candles[at + 1 : at + 1 + react_bars]
            resolved = len(later) == react_bars and at < len(candles)
            move = (later[-1].close / candles[at].close - 1) if resolved else None
            signed = None if move is None else (-move if label == "bearish" else move)
            out.append(Divergence(
                kind=label, bar_time=candles[at].time, index=at,
                price=round(now.price, 2), cvd=round(cvd[now.index], 2), prior_time=prev.bar_time,
                resolved=resolved,
                reaction_pct=None if signed is None else round(100 * signed, 3),
                worked=None if signed is None else bool(signed > 0),
            ))
    return sorted(out, key=lambda d: d.index)


def absorptions(candles: list[Candle], flow: list[Bar], window: int = MEAN_WINDOW) -> list[Absorption]:
    """Heavy volume that went nowhere, with the flow pushing against the close.

    Somebody filled size against the move. This is the reading that most wants a real tape — from
    candles it is a plausible guess and nothing more — so it is reported as a marker, never a
    signal, and carries the same APPROX badge as everything else here. There is deliberately no
    hit rate for it: ten occurrences in 5,000 bars will never reach a sample worth quoting, and a
    rate built on ten events is a number that only looks like one."""
    if len(candles) < window + 2:
        return []
    out: list[Absorption] = []
    for i in range(window, len(candles)):
        base = candles[i - window : i]
        mean_vol = sum(c.volume for c in base) / window
        mean_range = sum(c.high - c.low for c in base) / window
        if mean_vol <= 0 or mean_range <= 0:
            continue
        c, f = candles[i], flow[i]
        vol_ratio = c.volume / mean_vol
        range_ratio = (c.high - c.low) / mean_range
        if vol_ratio < ABSORPTION_VOLUME or range_ratio > ABSORPTION_RANGE:
            continue
        # Heavy selling that failed to push price down is buying absorbing it, and the mirror.
        if f.ratio < -0.2 and c.close >= c.open:
            direction = "bull"
        elif f.ratio > 0.2 and c.close <= c.open:
            direction = "bear"
        else:
            continue
        out.append(Absorption(
            direction=direction, bar_time=c.time, index=i,
            volume_ratio=round(vol_ratio, 2), range_ratio=round(range_ratio, 2), delta_ratio=f.ratio,
        ))
    return out


def record_of(found: list[Divergence], candles: list[Candle], react_bars: int = REACTION_BARS) -> dict[str, Any]:
    """How often a divergence was followed by the move it implies, beside how often any bar was."""
    moves = [candles[i + react_bars].close / candles[i].close - 1 for i in range(len(candles) - react_bars)]
    rose = sum(1 for m in moves if m > 0) / len(moves) if moves else None
    out: dict[str, Any] = {}
    for kind in ("bullish", "bearish"):
        group = [d for d in found if d.kind == kind and d.worked is not None]
        worked = sum(1 for d in group if d.worked)
        baseline = None if rose is None else (rose if kind == "bullish" else 1 - rose)
        rate = round(worked / len(group), 4) if group else None
        out[kind] = {
            "n": len(group), "worked": worked, "rate": rate,
            "baseline": None if baseline is None else round(baseline, 4),
            "edge": None if rate is None or baseline is None else round(rate - baseline, 4),
            "enough": len(group) >= MIN_SAMPLE,
        }
    return out | {"react_bars": react_bars, "min_sample": MIN_SAMPLE}


@dataclass
class Flow:
    ready: bool
    note: str = ""
    approx: bool = True
    approx_note: str = APPROX_NOTE
    bars: list[dict[str, Any]] = field(default_factory=list)
    delta_ratio: float | None = None
    cvd: float | None = None
    cvd_change_pct: float | None = None
    pressure: str = "balanced"  # "buyers" | "sellers" | "balanced"
    day_buy: float = 0.0
    day_sell: float = 0.0
    divergences: list[Divergence] = field(default_factory=list)
    absorptions: list[Absorption] = field(default_factory=list)
    record: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready, "note": self.note, "approx": self.approx, "approx_note": self.approx_note,
            "bars": self.bars, "delta_ratio": self.delta_ratio, "cvd": self.cvd,
            "cvd_change_pct": self.cvd_change_pct, "pressure": self.pressure,
            "day_buy": self.day_buy, "day_sell": self.day_sell,
            "divergences": [d.to_dict() for d in self.divergences[-20:]],
            "absorptions": [a.to_dict() for a in self.absorptions[-20:]],
            "record": self.record,
        }


MIN_BARS = 40
CHART_BARS = 200
PRESSURE_THRESHOLD = 0.1


def read(candles: list[Candle], react_bars: int = REACTION_BARS) -> Flow:
    """Everything above, for one symbol on one timeframe. All of it approximate, all of it marked."""
    if len(candles) < MIN_BARS:
        return Flow(ready=False, note=f"needs {MIN_BARS} bars, has {len(candles)}")
    flow = series(candles)
    today = [f for f in flow if day_start(f.time) == day_start(candles[-1].time)]
    buy = sum(f.buy for f in today)
    sell = sum(f.sell for f in today)
    traded = buy + sell
    share = (buy - sell) / traded if traded else 0.0
    found = divergences(candles, flow, react_bars)
    # CVD over the visible window, as a share of the volume behind it — a raw figure means nothing.
    window = flow[-CHART_BARS:]
    moved = window[-1].cvd - window[0].cvd
    volume = sum(c.volume for c in candles[-len(window):]) or 1.0
    return Flow(
        ready=True,
        bars=[f.to_dict() for f in window],
        delta_ratio=flow[-1].ratio,
        cvd=round(flow[-1].cvd, 2),
        cvd_change_pct=round(100 * moved / volume, 3),
        pressure="buyers" if share > PRESSURE_THRESHOLD else "sellers" if share < -PRESSURE_THRESHOLD else "balanced",
        day_buy=round(buy, 2), day_sell=round(sell, 2),
        divergences=found,
        absorptions=absorptions(candles, flow),
        record=record_of(found, candles, react_bars),
    )
