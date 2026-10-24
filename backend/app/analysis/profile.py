"""Volume by price, not by time.

Every other reading in this system is a function of time: what happened at 14:45. This one asks
the question a level is really about — **at which prices did the size actually change hands** —
and the answer does not care what order it happened in.

  point of control  The price with the most volume behind it. Acts as a magnet, because it is
                    where the most people have a position to defend.
  value area        The band holding 70% of the volume. Its edges are where price has
                    historically been rejected back inside.
  HVN               A volume peak: prices the market agreed on. Price moves slowly through them.
  LVN               A volume trough: prices it rejected. Price moves FAST through them, which is
                    why they make better targets than supports.
  naked POC         A previous session's point of control price has never returned to. Unfinished
                    business, and the most reliable magnet on the list.
  TPO               The same distribution counted in time rather than volume: one letter per
                    30 minutes at each price. It answers "how LONG did it spend here", which is a
                    different question from "how much traded here" and sometimes a better one.

Built from the base series where it is available. This matters more than it sounds: a 15-minute
bar that travelled 2% has its volume smeared evenly across the whole range because nothing in it
says where inside the bar the trade happened. Fifteen one-minute bars say exactly that. The same
code runs on either — `bars_per_period` is all that changes — and the result carries `source` so
a reader knows which they are looking at.

Pure: rows in, dataclasses out. No clock, no network, no database.
"""

from __future__ import annotations

import string
from dataclasses import asdict, dataclass, field
from typing import Any

from app.analysis.liquidity import DAY, SESSION_LABEL, SESSIONS, day_start
from app.market.delta import Candle

DEFAULT_BINS = 60
VALUE_AREA = 0.70
HVN_RATIO = 1.3  # a bin this far above the local average is a high-volume node
LVN_RATIO = 0.7  # and this far below is a low-volume one
SMOOTH = 3  # bins either side, for finding peaks without chasing single-bin noise
TPO_PERIOD = 1800  # half an hour per letter, the market-profile convention
IB_PERIODS = 2  # the initial balance is the first hour


@dataclass
class Node:
    price: float
    volume: float
    share: float
    in_value: bool
    poc: bool
    kind: str = ""  # "hvn" | "lvn" | ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Profile:
    """One distribution of volume over price."""

    label: str
    start: int
    end: int
    poc: float
    value_low: float
    value_high: float
    high: float
    low: float
    volume: float
    bars: int
    source: str  # the interval the rows came from: "1m" is a real profile, "15m" is an estimate
    complete: bool
    nodes: list[Node] = field(default_factory=list)
    hvn: list[float] = field(default_factory=list)
    lvn: list[float] = field(default_factory=list)
    naked: bool = False  # has price never been back to this POC?

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"nodes": [n.to_dict() for n in self.nodes]}


def _rows_to_candles(rows: list[Any]) -> list[Candle]:
    if rows and isinstance(rows[0], Candle):
        return list(rows)
    return [
        Candle(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r.get("volume") or 0))
        for r in rows
    ]


def build(
    rows: list[Any], label: str = "", bins: int = DEFAULT_BINS, value_area: float = VALUE_AREA,
    source: str = "15m", complete: bool = True,
) -> Profile | None:
    """The distribution, its point of control and its value area.

    A bar's volume is spread evenly across the range it covered rather than dropped on its close.
    A bar that travelled 2% did not trade all of its size at one price, and pretending it did puts
    the point of control wherever the closes happened to cluster — which is a chart of closes, not
    of volume."""
    candles = _rows_to_candles(rows)
    if len(candles) < 3:
        return None
    low = min(c.low for c in candles)
    high = max(c.high for c in candles)
    if high <= low:
        return None
    step = (high - low) / bins
    volume = [0.0] * bins

    def index(price: float) -> int:
        return min(bins - 1, max(0, int((price - low) / step)))

    for c in candles:
        first, last = index(c.low), index(c.high)
        share = (c.volume or 0) / (last - first + 1)
        for b in range(first, last + 1):
            volume[b] += share

    traded = sum(volume)
    if traded <= 0:
        return None
    poc = max(range(bins), key=lambda b: volume[b])
    # Grow out from the point of control, always taking the heavier neighbour, until the band holds
    # the value area. The standard construction, and the one that keeps the band contiguous.
    lo = hi = poc
    held = volume[poc]
    while held < value_area * traded and (lo > 0 or hi < bins - 1):
        below = volume[lo - 1] if lo > 0 else -1.0
        above = volume[hi + 1] if hi < bins - 1 else -1.0
        if above >= below:
            hi += 1
            held += volume[hi]
        else:
            lo -= 1
            held += volume[lo]

    at = lambda b: round(low + (b + 0.5) * step, 2)  # noqa: E731
    kinds = _nodes(volume)
    nodes = [
        Node(price=at(b), volume=round(volume[b], 4), share=round(volume[b] / traded, 5),
             in_value=lo <= b <= hi, poc=b == poc, kind=kinds[b])
        for b in range(bins)
    ]
    return Profile(
        label=label, start=candles[0].time, end=candles[-1].time,
        poc=at(poc), value_low=round(low + lo * step, 2), value_high=round(low + (hi + 1) * step, 2),
        high=round(high, 2), low=round(low, 2), volume=round(traded, 2), bars=len(candles),
        source=source, complete=complete, nodes=nodes,
        hvn=[n.price for n in nodes if n.kind == "hvn"],
        lvn=[n.price for n in nodes if n.kind == "lvn"],
    )


def _nodes(volume: list[float], smooth: int = SMOOTH) -> list[str]:
    """High and low volume nodes: the peaks and troughs of the SMOOTHED profile.

    Smoothed because a raw profile is spiky and a single busy bin is not a node — the question is
    which prices the market lingered at, not which one got an extra fill. A peak must also stand
    above the profile's own average, so a flat distribution yields no nodes at all rather than
    naming its least-flat bin.

    (The first version tested each bin against the average of a window that included the bin
    itself. On any smooth distribution that ratio is ~1 by construction, so it found no high
    volume nodes anywhere, on any symbol.)"""
    n = len(volume)
    out = [""] * n
    occupied = [v for v in volume if v > 0]
    if len(occupied) < 2 * smooth + 1:
        return out
    mean = sum(occupied) / len(occupied)
    smoothed = [
        sum(volume[max(0, i - smooth):i + smooth + 1]) / len(volume[max(0, i - smooth):i + smooth + 1])
        for i in range(n)
    ]
    for i in range(n):
        if volume[i] <= 0:
            continue
        window = smoothed[max(0, i - smooth):i + smooth + 1]
        if smoothed[i] == max(window) and smoothed[i] >= HVN_RATIO * mean:
            out[i] = "hvn"
        elif smoothed[i] == min(window) and smoothed[i] <= LVN_RATIO * mean:
            out[i] = "lvn"
    return out


# -- the windows worth building one over ---------------------------------------------


def by_day(rows: list[Any], days: int = 5, bins: int = DEFAULT_BINS, source: str = "15m") -> list[Profile]:
    """One profile per UTC day, oldest first. The last one is marked incomplete."""
    candles = _rows_to_candles(rows)
    if not candles:
        return []
    last_day = day_start(candles[-1].time)
    out = []
    for day in range(last_day - (days - 1) * DAY, last_day + DAY, DAY):
        group = [c for c in candles if day <= c.time < day + DAY]
        p = build(group, label=_day_label(day), bins=bins, source=source, complete=day < last_day)
        if p:
            out.append(p)
    return out


def by_session(rows: list[Any], days: int = 2, bins: int = 40, source: str = "15m") -> list[Profile]:
    """One profile per session per UTC day. Sessions overlap, so these do too — on purpose."""
    candles = _rows_to_candles(rows)
    if not candles:
        return []
    last = candles[-1].time
    out = []
    for day in range(day_start(last) - (days - 1) * DAY, day_start(last) + DAY, DAY):
        for name, lo, hi in SESSIONS:
            start, end = day + lo * 3600, day + hi * 3600
            group = [c for c in candles if start <= c.time < end]
            p = build(group, label=f"{SESSION_LABEL[name]} {_day_label(day)}", bins=bins,
                      source=source, complete=last >= end)
            if p:
                out.append(p)
    return out


def composite(rows: list[Any], days: int = 20, bins: int = 80, source: str = "15m") -> Profile | None:
    """One profile over the whole window. The levels that have held across many sessions rather
    than the ones today happened to make."""
    candles = _rows_to_candles(rows)
    if not candles:
        return None
    since = candles[-1].time - days * DAY
    return build([c for c in candles if c.time >= since], label=f"Last {days} days", bins=bins, source=source)


def _day_label(day: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(day, tz=UTC).strftime("%d %b")


def mark_naked(profiles: list[Profile], rows: list[Any]) -> list[Profile]:
    """Flag the points of control price has not traded back to since that window closed.

    A naked POC is the most reliable magnet on this page for a boring reason: it is volume that
    was transacted and then abandoned, and the market has a habit of going back to finish."""
    candles = _rows_to_candles(rows)
    for p in profiles:
        after = [c for c in candles if c.time > p.end]
        p.naked = bool(p.complete and after and not any(c.low <= p.poc <= c.high for c in after))
    return profiles


# -- time at price, rather than volume at price ---------------------------------------


@dataclass
class Tpo:
    """Market profile for one day: how long price spent at each level, in half-hour letters."""

    label: str
    start: int
    rows: list[dict[str, Any]] = field(default_factory=list)  # price, letters
    poc: float | None = None
    value_low: float | None = None
    value_high: float | None = None
    ib_high: float | None = None
    ib_low: float | None = None
    single_prints: list[float] = field(default_factory=list)
    poor_high: bool = False
    poor_low: bool = False
    periods: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def tpo(rows: list[Any], bins: int = 40, period: int = TPO_PERIOD, label: str = "") -> Tpo | None:
    """One day as a market profile.

    Each half hour gets a letter, and the letter is printed at every price it traded through. A
    price with many letters was accepted; a price with one was rejected and is a **single print**,
    which tends not to be revisited slowly. The **initial balance** is the first hour's range — the
    day's opening auction, and the reference everything after it is measured against.

    A **poor high** is an extreme with two or more letters sitting on it: the auction stopped
    without a tail, which usually means it was not finished and price comes back for it.
    """
    candles = _rows_to_candles(rows)
    if len(candles) < 4:
        return None
    low = min(c.low for c in candles)
    high = max(c.high for c in candles)
    if high <= low:
        return None
    step = (high - low) / bins
    start = candles[0].time // period * period
    letters: list[set[str]] = [set() for _ in range(bins)]
    seen: list[str] = []
    for c in candles:
        slot = (c.time - start) // period
        letter = string.ascii_uppercase[slot % 26] if slot < 26 else string.ascii_lowercase[(slot - 26) % 26]
        if letter not in seen:
            seen.append(letter)
        first = min(bins - 1, max(0, int((c.low - low) / step)))
        last = min(bins - 1, max(0, int((c.high - low) / step)))
        for b in range(first, last + 1):
            letters[b].add(letter)

    counts = [len(s) for s in letters]
    total = sum(counts)
    poc_bin = max(range(bins), key=lambda b: counts[b])
    lo = hi = poc_bin
    held = counts[poc_bin]
    while held < VALUE_AREA * total and (lo > 0 or hi < bins - 1):
        below = counts[lo - 1] if lo > 0 else -1
        above = counts[hi + 1] if hi < bins - 1 else -1
        if above >= below:
            hi += 1
            held += counts[hi]
        else:
            lo -= 1
            held += counts[lo]

    at = lambda b: round(low + (b + 0.5) * step, 2)  # noqa: E731
    ib = [c for c in candles if c.time < start + IB_PERIODS * period]
    occupied = [b for b in range(bins) if counts[b]]
    return Tpo(
        label=label, start=start,
        rows=[{"price": at(b), "letters": "".join(sorted(letters[b])), "count": counts[b],
               "in_value": lo <= b <= hi, "poc": b == poc_bin, "single": counts[b] == 1}
              for b in reversed(range(bins)) if counts[b]],
        poc=at(poc_bin), value_low=round(low + lo * step, 2), value_high=round(low + (hi + 1) * step, 2),
        ib_high=round(max(c.high for c in ib), 2) if ib else None,
        ib_low=round(min(c.low for c in ib), 2) if ib else None,
        single_prints=[at(b) for b in range(bins) if counts[b] == 1],
        # Two or more letters at the extreme: the auction stopped without a tail.
        poor_high=bool(occupied and counts[max(occupied)] >= 2),
        poor_low=bool(occupied and counts[min(occupied)] >= 2),
        periods=len(seen),
    )


# -- one reading ----------------------------------------------------------------------


@dataclass
class Profiles:
    ready: bool
    note: str = ""
    source: str = ""
    session: Profile | None = None
    previous: Profile | None = None
    composite: Profile | None = None
    days: list[Profile] = field(default_factory=list)
    sessions: list[Profile] = field(default_factory=list)
    naked_pocs: list[dict[str, Any]] = field(default_factory=list)
    tpo: Tpo | None = None
    price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready, "note": self.note, "source": self.source, "price": self.price,
            "session": self.session.to_dict() if self.session else None,
            "previous": self.previous.to_dict() if self.previous else None,
            "composite": self.composite.to_dict() if self.composite else None,
            "days": [p.to_dict() for p in self.days],
            "sessions": [p.to_dict() for p in self.sessions],
            "naked_pocs": self.naked_pocs,
            "tpo": self.tpo.to_dict() if self.tpo else None,
        }


def read(rows: list[Any], source: str = "15m", days: int = 5, composite_days: int = 20) -> Profiles:
    """Today, yesterday, the composite, every session, the naked points of control, and the TPO."""
    candles = _rows_to_candles(rows)
    if len(candles) < 30:
        return Profiles(ready=False, note=f"needs 30 bars, has {len(candles)}")
    daily = mark_naked(by_day(candles, days, source=source), candles)
    sessions = mark_naked(by_session(candles, 2, source=source), candles)
    today = day_start(candles[-1].time)
    naked = [
        {"price": p.poc, "label": p.label, "end": p.end,
         "distance_pct": round(100 * (p.poc / candles[-1].close - 1), 3)}
        for p in sorted([*daily, *sessions], key=lambda p: -p.end) if p.naked
    ]
    return Profiles(
        ready=True, source=source, price=round(candles[-1].close, 2),
        session=daily[-1] if daily else None,
        previous=daily[-2] if len(daily) > 1 else None,
        composite=composite(candles, composite_days, source=source),
        days=daily, sessions=sessions,
        naked_pocs=naked[:10],
        tpo=tpo([c for c in candles if c.time >= today], label=_day_label(today)),
    )
