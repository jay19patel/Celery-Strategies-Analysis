"""Price areas with a history: where they came from, what happened at them, and whether they are
still worth anything.

A level is a line and a line is a fiction — the market does not turn at a price, it turns in a
band. What makes a band tradeable is not its existence but its **state**, and that is the whole
point of this module. A fair value gap nobody has filled and one price has already run through are
drawn identically on most charts and are not remotely the same proposition.

  order block    The last opposite-coloured candle before a displacement move that broke structure.
                 The reasoning is plain: somebody absorbed the other side there and then price left
                 in a hurry, so unfilled orders are likely still sitting at it. Requires BOTH the
                 displacement (>= `displacement_atr` x ATR) and the break — a big candle that broke
                 nothing is just a big candle.

  fair value gap A three-bar imbalance nobody traded through, at least `min_atr` x ATR wide so a
                 tick of noise does not become a level.

  support /      Pivots clustered into a band, not a line. Strength counts the touches and the
  resistance     volume that traded in the band, because a level tested four times on heavy volume
                 is a different thing from one grazed twice.

**The lifecycle only moves forward**: fresh -> tested -> mitigated -> invalidated. A zone cannot
become fresh again, which is the property that makes the record countable — and the one a chart
that redraws its zones every refresh quietly breaks.

    fresh        created, price has not been back
    tested       price traded into it and left the same way: the zone did its job
    mitigated    price traded all the way through it; the orders there are filled
    invalidated  a bar CLOSED beyond the far side, against the zone. It failed.

Pure: candles in, dataclasses out. No clock, no network, no database.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from tradebuddy import structure as st
from tradebuddy.delta import Candle

# Forward only. The index is the comparison: a zone's state can rise and never fall.
STATES = ("fresh", "tested", "mitigated", "invalidated")
STATE_ORDER = {s: i for i, s in enumerate(STATES)}
LIVE_STATES = ("fresh", "tested")  # still worth drawing

DISPLACEMENT_ATR = 1.5  # the move away from an order block has to be this big
MIN_GAP_ATR = 0.2  # a gap under this is noise with a name
SR_BAND_ATR = 0.5  # pivots within this of each other are one band
MIN_SR_TOUCHES = 2
MAX_ZONES = 40
MIN_ZONE_SAMPLE = 30  # fewer resolved first-touches than this and no rate is called an edge
LOOKBACK = 400  # bars scanned for new zones; older ones have been traded through many times over


@dataclass
class Zone:
    """One price area, and everything that has happened to it since it formed."""

    kind: str  # "ob" | "fvg" | "sr"
    direction: str  # "bull" (demand, expect support) | "bear" (supply, expect resistance)
    low: float
    high: float
    created_at: int  # bar time it formed
    timeframe: str
    state: str = "fresh"
    touches: int = 0
    last_touched_at: int | None = None
    strength: float = 1.0  # displacement x volume for OBs, touches x volume for S/R
    fill_pct: float = 0.0  # how much of the band price has traded through
    distance_pct: float | None = None
    index: int = 0
    # What happened the FIRST time price came back. Everything after that is a different question
    # asked of a zone that has already been used.
    first_touch_at: int | None = None
    reaction_pct: float | None = None  # the move after that first touch, the way the zone implies
    worked: bool | None = None  # None while the reaction window has not closed yet

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    @property
    def width(self) -> float:
        return self.high - self.low

    def advance(self, state: str) -> None:
        """Move the lifecycle on. Never back: a mitigated zone does not become fresh because a
        later pass over the data looked at fewer bars."""
        if STATE_ORDER[state] > STATE_ORDER[self.state]:
            self.state = state

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "mid": round(self.mid, 2), "width": round(self.width, 2),
            "live": self.state in LIVE_STATES,
        }


# -- finding them ---------------------------------------------------------------------


def order_blocks(candles: list[Candle], atr: float, timeframe: str = "15m",
                 displacement_atr: float = DISPLACEMENT_ATR, lookback: int = LOOKBACK) -> list[Zone]:
    """The last opposite-coloured candle before each structure break that arrived on a big bar.

    Both conditions are required. `structure.find_breaks` supplies the break (a fresh close through
    a prior swing, never a level that was already gone); the displacement test supplies the "in a
    hurry" part. A break that crept through on an ordinary candle leaves no order block, because
    nothing was absorbed."""
    if atr <= 0 or len(candles) < 40:
        return []
    window = candles[-lookback:] if len(candles) > lookback else candles
    offset = len(candles) - len(window)
    breaks = st.find_breaks(window)
    out: list[Zone] = []
    for b in breaks:
        i = b.index
        if (window[i].high - window[i].low) < displacement_atr * atr:
            continue
        want_down = b.direction == "up"  # a break up leaves the last DOWN candle behind it
        # Walk back for the last candle that closed the other way. Ten bars is generous; beyond
        # that the "last candle before the move" is not before the move any more.
        source = next(
            (j for j in range(i - 1, max(i - 11, -1), -1)
             if (window[j].close < window[j].open) == want_down and window[j].close != window[j].open),
            None,
        )
        if source is None:
            continue
        c = window[source]
        out.append(Zone(
            kind="ob",
            direction="bull" if b.direction == "up" else "bear",
            # The body, not the full range: the wick is where it was rejected, the body is where
            # it traded. `full_range` is the other convention and is one edit away if wanted.
            low=round(min(c.open, c.close), 2),
            high=round(max(c.open, c.close), 2),
            created_at=c.time,
            timeframe=timeframe,
            strength=round(b.displacement * max(b.volume_ratio, 0.1), 2),
            index=offset + source,
        ))
    return out


def fair_value_gaps(candles: list[Candle], atr: float, timeframe: str = "15m",
                    min_atr: float = MIN_GAP_ATR, lookback: int = LOOKBACK) -> list[Zone]:
    """Three-bar imbalances wide enough to matter.

    `structure.find_gaps` already finds these but reports only the unfilled ones, because that is
    what its caller wants to draw. Here the filled ones matter too — a gap's record is only
    countable if the ones that got filled are kept."""
    if atr <= 0 or len(candles) < 4:
        return []
    window = candles[-lookback:] if len(candles) > lookback else candles
    offset = len(candles) - len(window)
    out: list[Zone] = []
    for i in range(2, len(window)):
        a, c = window[i - 2], window[i]
        if c.low > a.high:
            lo, hi, direction = a.high, c.low, "bull"
        elif c.high < a.low:
            lo, hi, direction = c.high, a.low, "bear"
        else:
            continue
        if hi - lo < min_atr * atr:
            continue
        out.append(Zone(
            kind="fvg", direction=direction, low=round(lo, 2), high=round(hi, 2),
            created_at=c.time, timeframe=timeframe,
            strength=round((hi - lo) / atr, 2), index=offset + i,
        ))
    return out


def sr_zones(candles: list[Candle], atr: float, timeframe: str = "15m",
             band_atr: float = SR_BAND_ATR, min_touches: int = MIN_SR_TOUCHES,
             lookback: int = LOOKBACK) -> list[Zone]:
    """Pivots clustered into bands, with the volume that traded in each.

    A band rather than a line, because that is how price actually behaves, and because a band can
    be tested — a line can only be crossed."""
    from tradebuddy.liquidity import pivots

    if atr <= 0 or len(candles) < 60:
        return []
    window = candles[-lookback:] if len(candles) > lookback else candles
    found = pivots(window)
    if not found:
        return []
    band = band_atr * atr
    out: list[Zone] = []
    for kind, direction in (("high", "bear"), ("low", "bull")):
        same = sorted((p for p in found if p.kind == kind), key=lambda p: p.price)
        group: list[Any] = []
        for p in [*same, None]:
            if group and (p is None or p.price - group[0].price > band):
                if len(group) >= min_touches:
                    lo = min(g.price for g in group)
                    hi = max(g.price for g in group)
                    # Give a single-price cluster some width, or it is a line again.
                    if hi - lo < band / 2:
                        mid = (hi + lo) / 2
                        lo, hi = mid - band / 4, mid + band / 4
                    traded = sum(c.volume for c in window if c.low <= hi and c.high >= lo)
                    total = sum(c.volume for c in window) or 1.0
                    out.append(Zone(
                        kind="sr", direction=direction, low=round(lo, 2), high=round(hi, 2),
                        created_at=min(g.bar_time for g in group), timeframe=timeframe,
                        touches=len(group),
                        strength=round(len(group) * (1 + traded / total), 2),
                        index=max(g.index for g in group),
                    ))
                group = []
            if p is not None:
                group.append(p)
    return out


# -- what has happened to them since --------------------------------------------------


REACTION_BARS = 5


def settle(zone: Zone, candles: list[Candle], react_bars: int = REACTION_BARS) -> Zone:
    """Walk every bar after the zone formed and move its state on.

    Only bars strictly after `created_at`, so a zone is never tested by the candle that made it.
    The walk is forward and the state is monotonic, so running this over more history can only
    push a zone further along its lifecycle — never back.

    The **first** touch is what gets measured. Asking "did price ever trade through this zone"
    over a year of bars answers yes for almost every zone ever drawn, which is why the first
    version of this reported hold rates of 0-14% and meant nothing by them. The question a trader
    is actually asking is: when price came back to it the first time, did it react."""
    bull = zone.direction == "bull"
    far = zone.low if bull else zone.high  # where the zone fails
    deepest = 0.0
    first: int | None = None
    for i, c in enumerate(candles):
        if c.time <= zone.created_at:
            continue
        if c.low > zone.high or c.high < zone.low:
            continue  # the bar never reached it
        if first is None:
            first = i
            zone.first_touch_at = c.time
        zone.touches += 1
        zone.last_touched_at = c.time
        zone.advance("tested")
        reach = (zone.high - c.low) if bull else (c.high - zone.low)
        deepest = max(deepest, min(1.0, reach / zone.width if zone.width else 1.0))
        if (c.low <= zone.low) if bull else (c.high >= zone.high):
            zone.advance("mitigated")
        # A CLOSE beyond the far side is the zone failing, not merely being traded through.
        if (c.close < far) if bull else (c.close > far):
            zone.advance("invalidated")
    zone.fill_pct = round(deepest, 3)

    if first is not None and first + react_bars < len(candles):
        move = candles[first + react_bars].close / candles[first].close - 1
        signed = move if bull else -move
        zone.reaction_pct = round(100 * signed, 3)
        zone.worked = bool(signed > 0)
    return zone


@dataclass
class Zones:
    ready: bool
    note: str = ""
    zones: list[Zone] = field(default_factory=list)
    record: dict[str, Any] = field(default_factory=dict)
    nearest_above: Zone | None = None
    nearest_below: Zone | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready, "note": self.note,
            "zones": [z.to_dict() for z in self.zones],
            "record": self.record,
            "nearest_above": self.nearest_above.to_dict() if self.nearest_above else None,
            "nearest_below": self.nearest_below.to_dict() if self.nearest_below else None,
            "states": list(STATES),
        }


MIN_BARS = 60


def record_of(zones: list[Zone], candles: list[Candle], react_bars: int = REACTION_BARS) -> dict[str, Any]:
    """Per kind: how often the FIRST touch was followed by the move the zone implies — beside how
    often any bar at all was.

    Without the baseline this is unreadable. If price rose over the next five bars on 51% of every
    bar in the series, then "demand zones bounced 52% of the time" is the market and not the zone.
    Reported with its count, so a handful cannot look like a finding."""
    moves = [candles[i + react_bars].close / candles[i].close - 1 for i in range(len(candles) - react_bars)]
    rose = sum(1 for m in moves if m > 0) / len(moves) if moves else None
    out = {}
    for kind in ("ob", "fvg", "sr"):
        group = [z for z in zones if z.kind == kind]
        done = [z for z in group if z.worked is not None]
        worked = sum(1 for z in done if z.worked)
        # The baseline has to match the mix: a kind made mostly of demand zones is judged against
        # how often price rose, not against a coin.
        bulls = sum(1 for z in done if z.direction == "bull")
        baseline = None if rose is None or not done else (bulls * rose + (len(done) - bulls) * (1 - rose)) / len(done)
        rate = round(worked / len(done), 4) if done else None
        out[kind] = {
            "found": len(group),
            "fresh": sum(1 for z in group if z.state == "fresh"),
            "visited": sum(1 for z in group if z.state != "fresh"),
            "resolved": len(done),
            "worked": worked,
            "rate": rate,
            "baseline": None if baseline is None else round(baseline, 4),
            "edge": None if rate is None or baseline is None else round(rate - baseline, 4),
            "enough": len(done) >= MIN_ZONE_SAMPLE,
        }
    return out | {"react_bars": react_bars, "min_sample": MIN_ZONE_SAMPLE}


def read(candles: list[Candle], atr: float, timeframe: str = "15m") -> Zones:
    """Every zone on one timeframe, settled against what happened after it."""
    if len(candles) < MIN_BARS:
        return Zones(ready=False, note=f"needs {MIN_BARS} bars, has {len(candles)}")
    if atr <= 0:
        return Zones(ready=False, note="no ATR to size the zones with")
    found = [
        *order_blocks(candles, atr, timeframe),
        *fair_value_gaps(candles, atr, timeframe),
        *sr_zones(candles, atr, timeframe),
    ]
    close = candles[-1].close
    for z in found:
        settle(z, candles)
        z.distance_pct = round(100 * (z.mid / close - 1), 3)
    live = [z for z in found if z.state in LIVE_STATES]
    # Live zones first, then the nearest. A mitigated zone is history and should not push a fresh
    # one off the end of the list.
    found.sort(key=lambda z: (z.state not in LIVE_STATES, abs(z.distance_pct or 0)))
    return Zones(
        ready=True,
        zones=found[:MAX_ZONES],
        record=record_of(found, candles),
        nearest_above=min((z for z in live if (z.distance_pct or 0) > 0), key=lambda z: z.distance_pct or 0, default=None),
        nearest_below=max((z for z in live if (z.distance_pct or 0) < 0), key=lambda z: z.distance_pct or 0, default=None),
    )
