"""The Market page's tabs, each one a self-contained reading of the same bars.

The page was one long scroll and the reader had to work out which numbers belonged together — which
is how a counted trend reading and a bare arithmetic one ended up next to each other saying
opposite things. The tabs are drawn along the only line that matters: **what the reading is made
of**, the same split `provenance.py` names.

    Volume        who is behind the move, from traded size alone
    Patterns      structure and candle shapes, each with what it has actually led to
    Indicators    the classical readings, and the arithmetic of how far price can travel
    Open interest what the options and futures books are positioned for
    Scorecard     what every card claimed, against what happened

Each block returns numbers plus a verdict, so a tab can be read in one line and then unpacked.
Pure: bars in, numbers out, no clock and no database.
"""

from __future__ import annotations

import statistics
from typing import Any

from app.analysis import structure as st
from app.market.delta import Candle
from app.market.indicators import atr, obv

VOLUME_WINDOW = 96  # a day of 15-minute bars: what "normal volume" means here
MIN_PATTERN_SAMPLE = 20  # fewer than this of a shape and its rate is not worth printing


def _pct(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else round(100 * (a / b - 1), 3)


def _z(values: list[float], window: int) -> float | None:
    """How unusual the last value is against the window before it, in standard deviations."""
    if len(values) < window + 1:
        return None
    base = values[-window - 1 : -1]
    mean = sum(base) / len(base)
    sd = statistics.pstdev(base)
    return round((values[-1] - mean) / sd, 2) if sd else None


# -- volume ---------------------------------------------------------------------------


def volume_block(candles: list[Candle]) -> dict[str, Any]:
    """Who is behind the move, from traded size alone.

    Volume cannot say which way price goes. What it can say is whether anyone turned up: a breakout
    on a quarter of normal volume and the same breakout on three times normal are different events,
    and the second is the only one worth paying a spread for. Everything here is a ratio against
    what this market normally does, never a raw figure — 400 contracts means nothing on its own.
    """
    if len(candles) < VOLUME_WINDOW + 2:
        return {"ready": False, "note": f"needs {VOLUME_WINDOW + 2} bars, has {len(candles)}"}
    vols = [c.volume for c in candles]
    closes = [c.close for c in candles]
    base = vols[-VOLUME_WINDOW - 1 : -1]
    normal = sum(base) / len(base)
    last = vols[-1]
    hour = sum(vols[-4:]) / 4

    # Volume that arrived on up closes against down closes, over the last day. Not a prediction —
    # a tally of where the size actually went.
    up_vol = sum(v for v, i in zip(vols[-VOLUME_WINDOW:], range(len(vols) - VOLUME_WINDOW, len(vols)), strict=True)
                 if i > 0 and closes[i] > closes[i - 1])
    down_vol = sum(v for v, i in zip(vols[-VOLUME_WINDOW:], range(len(vols) - VOLUME_WINDOW, len(vols)), strict=True)
                   if i > 0 and closes[i] < closes[i - 1])
    traded = up_vol + down_vol

    line = obv(closes, vols)
    obv_slope = line[-1] - line[-21] if len(line) > 21 else None
    # Scaled by a day of volume, so the number means the same on BTC and on ETH.
    obv_norm = round(obv_slope / (normal * 20), 3) if obv_slope is not None and normal else None

    breaks = st.find_breaks(candles)
    recent = breaks[-1] if breaks else None
    return {
        "ready": True,
        "normal": round(normal, 2),
        "last_bar": round(last, 2),
        "last_ratio": round(last / normal, 2) if normal else None,
        "hour_ratio": round(hour / normal, 2) if normal else None,
        "z": _z(vols, VOLUME_WINDOW),
        "up_share": round(up_vol / traded, 3) if traded else None,
        "down_share": round(down_vol / traded, 3) if traded else None,
        "obv_slope_20": obv_norm,
        "price_change_24h_pct": _pct(closes[-1], closes[-VOLUME_WINDOW]) if len(closes) > VOLUME_WINDOW else None,
        "last_break": {
            "kind": recent.kind, "direction": recent.direction, "bar_time": recent.bar_time,
            "volume_ratio": recent.volume_ratio, "displacement": recent.displacement,
            "bars_ago": len(candles) - 1 - recent.index,
        } if recent else None,
        "series": [
            {"time": c.time, "volume": round(c.volume, 2), "up": c.close >= c.open}
            for c in candles[-VOLUME_WINDOW:]
        ],
    }


# -- indicators -----------------------------------------------------------------------


def _series(candles: list[Candle], values: list[float], window: int) -> list[dict[str, Any]]:
    """A values list aligned to the end of the bars, as points the chart can draw."""
    if not values:
        return []
    start = len(candles) - len(values)
    return [{"time": candles[start + i].time, "value": round(v, 4)} for i, v in enumerate(values)][-window:]


# -- candle shapes, scored like breaks ------------------------------------------------


def shape_record(candles: list[Candle], hours: float, bar_seconds: int = 900) -> list[dict[str, Any]]:
    """What each candle shape has actually led to, `hours` later.

    A hammer is a shape, not a signal. The only thing that makes one worth acting on is how often
    it has been followed by the move it is supposed to promise — against how often any bar was,
    which on most shapes turns out to be about the same.
    """
    ahead = max(1, round(hours * 3600 / bar_seconds))
    closes = [c.close for c in candles]
    forward = [
        (closes[i + ahead] / closes[i] - 1) if i + ahead < len(closes) and closes[i] > 0 else None
        for i in range(len(closes))
    ]
    settled = [r for r in forward if r is not None]
    if len(settled) < 200:
        return []
    baseline_up = sum(1 for r in settled if r > 0) / len(settled)

    groups: dict[tuple[str, str], list[float]] = {}
    for shape in st.find_shapes(candles, limit=len(candles)):
        r = forward[shape.index] if shape.index < len(forward) else None
        if r is not None:
            groups.setdefault((shape.name, shape.direction), []).append(r)

    out = []
    for (name, direction), rows in sorted(groups.items()):
        if direction == "neutral":
            # A doji or an inside bar claims indecision, so the question is whether the market then
            # moved at all — not which way.
            typical = sum(abs(r) for r in settled) / len(settled)
            moved = sum(1 for r in rows if abs(r) > typical) / len(rows)
            base = sum(1 for r in settled if abs(r) > typical) / len(settled)
            followed, baseline = moved, base
        else:
            up = sum(1 for r in rows if r > 0) / len(rows)
            followed = up if direction == "bull" else 1 - up
            baseline = baseline_up if direction == "bull" else 1 - baseline_up
        enough = len(rows) >= MIN_PATTERN_SAMPLE
        out.append({
            "name": name,
            "label": st.CANDLE_NAMES.get(name, name),
            "direction": direction,
            "asks": "moved more than usual" if direction == "neutral" else f"went {direction}",
            "count": len(rows),
            "followed_through": round(followed, 4) if enough else None,
            "baseline": round(baseline, 4),
            "edge": round(followed - baseline, 4) if enough else None,
            "mean_move_pct": round(100 * sum(abs(r) for r in rows) / len(rows), 3),
            "enough": enough,
        })
    return sorted(out, key=lambda s: -abs(s["edge"] or 0))


TIMEFRAMES = (
    {"interval": "15m", "factor": 1, "label": "Short term", "window": 96},
    {"interval": "1h", "factor": 4, "label": "Medium term", "window": 120},
    {"interval": "1d", "factor": 96, "label": "Long term", "window": 120},
)


# -- where the money actually traded ---------------------------------------------------

PROFILE_BUCKETS = 48
PROFILE_WINDOW = 2 * VOLUME_WINDOW  # two days of 15-minute bars
VALUE_AREA = 0.70  # the share of volume the market is said to have "accepted"


def vwap_block(candles: list[Candle], window: int = VOLUME_WINDOW) -> dict[str, Any]:
    """Volume-weighted average price over a rolling window, with bands.

    Different information from every moving average here: an EMA weights by time, VWAP weights by
    size, so it is the price the money actually paid rather than the price the clock saw. Desks
    are measured against it, which is part of why it behaves like a level.

    The bands are volume-weighted standard deviations, so "two sigma above VWAP" means the same
    thing in a quiet week and a violent one.
    """
    if len(candles) < window:
        return {"ready": False, "note": f"needs {window} bars, has {len(candles)}"}
    bars = candles[-window:]
    typical = [(c.high + c.low + c.close) / 3 for c in bars]
    vols = [c.volume for c in bars]
    traded = sum(vols)
    if traded <= 0:
        return {"ready": False, "note": "no volume in the window"}
    vwap = sum(t * v for t, v in zip(typical, vols, strict=True)) / traded
    variance = sum(v * (t - vwap) ** 2 for t, v in zip(typical, vols, strict=True)) / traded
    sd = variance ** 0.5
    price = candles[-1].close
    z = (price - vwap) / sd if sd else 0.0
    return {
        "ready": True,
        "vwap": round(vwap, 2),
        "sd": round(sd, 2),
        "price": round(price, 2),
        "distance_pct": round(100 * (price / vwap - 1), 3),
        "z": round(z, 2),
        "side": "above" if price > vwap else "below" if price < vwap else "at",
        "bands": {
            "upper_1": round(vwap + sd, 2), "lower_1": round(vwap - sd, 2),
            "upper_2": round(vwap + 2 * sd, 2), "lower_2": round(vwap - 2 * sd, 2),
        },
        "window_bars": window,
    }


def volume_profile(candles: list[Candle], buckets: int = PROFILE_BUCKETS, window: int = PROFILE_WINDOW) -> dict[str, Any]:
    """How much volume traded at each price, and therefore which prices the market agreed on.

    Everything else on this page is a function of time. This is a function of *price*: it asks
    where the size actually changed hands, which is the question a level is really about. The
    point of control is the price with the most volume behind it and tends to act as a magnet;
    the value area is the band holding most of the trade, and its edges are where price has
    historically been rejected back inside.

    A bar's volume is spread across the range it covered rather than dropped on its close — a bar
    that travelled 2% did not trade all of its size at one price, and pretending it did puts the
    point of control wherever the closes happened to cluster.
    """
    bars = candles[-window:] if len(candles) > window else candles
    if len(bars) < 20:
        return {"ready": False, "note": f"needs 20 bars, has {len(bars)}"}
    low = min(c.low for c in bars)
    high = max(c.high for c in bars)
    if high <= low:
        return {"ready": False, "note": "no range in the window"}
    step = (high - low) / buckets
    volume = [0.0] * buckets

    def index(price: float) -> int:
        return min(buckets - 1, max(0, int((price - low) / step)))

    for c in bars:
        first, last = index(c.low), index(c.high)
        share = c.volume / (last - first + 1)
        for b in range(first, last + 1):
            volume[b] += share

    traded = sum(volume)
    poc = max(range(buckets), key=lambda b: volume[b])
    # Grow out from the point of control until the band holds the value area, always taking the
    # heavier neighbour — the standard construction, and the one that keeps the band contiguous.
    lo = hi = poc
    held = volume[poc]
    while held < VALUE_AREA * traded and (lo > 0 or hi < buckets - 1):
        below = volume[lo - 1] if lo > 0 else -1
        above = volume[hi + 1] if hi < buckets - 1 else -1
        if above >= below:
            hi += 1
            held += volume[hi]
        else:
            lo -= 1
            held += volume[lo]

    price = candles[-1].close
    at = lambda b: round(low + (b + 0.5) * step, 2)  # noqa: E731
    poc_price, va_low, va_high = at(poc), round(low + lo * step, 2), round(low + (hi + 1) * step, 2)
    return {
        "ready": True,
        "poc": poc_price,
        "value_low": va_low,
        "value_high": va_high,
        "price": round(price, 2),
        "inside_value": bool(va_low <= price <= va_high),
        "poc_distance_pct": round(100 * (price / poc_price - 1), 3) if poc_price else None,
        "window_bars": len(bars),
        "levels": [
            {"price": at(b), "volume": round(volume[b], 2),
             "share": round(volume[b] / traded, 4) if traded else 0.0,
             "in_value": lo <= b <= hi, "poc": b == poc}
            for b in range(buckets)
        ],
    }


def anatomy(candles: list[Candle], window: int = 20) -> dict[str, Any]:
    """What the last bar is made of, against what a bar here normally is.

    Cheap and direct: a break on a bar that is 80% body is being taken, the same break on a bar
    that is 20% body with a long wick against it is being rejected. The ratios say which, and no
    oscillator does — they all read closes.
    """
    if len(candles) < window + 1:
        return {"ready": False, "note": f"needs {window + 1} bars, has {len(candles)}"}
    c = candles[-1]
    body, rng, upper, lower = st.anatomy_of(c)
    if rng <= 0:
        return {"ready": False, "note": "the last bar has no range"}
    recent = candles[-window:]
    mean_range = sum(x.high - x.low for x in recent) / window
    bands = atr([x.high for x in recent], [x.low for x in recent], [x.close for x in recent], 14)
    return {
        "ready": True,
        "body_pct": round(100 * body / rng, 1),
        "upper_wick_pct": round(100 * upper / rng, 1),
        "lower_wick_pct": round(100 * lower / rng, 1),
        # Where the bar closed inside its own range: 1 means it closed on its high.
        "buy_pressure": round((c.close - c.low) / rng, 3),
        "wick_imbalance": round((upper - lower) / rng, 3),  # + = rejected from above
        "vs_normal": round(rng / mean_range, 2) if mean_range else None,
        "vs_atr": round(rng / bands[-1], 2) if bands else None,
        "direction": "up" if c.close > c.open else "down" if c.close < c.open else "flat",
    }




# -- the levels everyone is looking at -------------------------------------------------

LEVEL_WINDOWS = (
    {"key": "1d", "bars": 96, "label": "Today"},
    {"key": "1w", "bars": 672, "label": "This week"},
    {"key": "1m", "bars": 2880, "label": "This month"},
    {"key": "52w", "bars": 35_040, "label": "52 weeks"},
)
IN_PLAY_PCT = 1.0  # within this of an extreme and the level is live rather than history
LEVEL_VOLUME_WINDOW = 20  # bars the "is anyone turning up" ratio is measured against


def levels_block(candles: list[Candle], windows: tuple[dict[str, Any], ...] = LEVEL_WINDOWS) -> dict[str, Any]:
    """The highs and lows other people are watching, and whether anyone is turning up at them.

    The high of the day is not interesting because it is high. It is interesting because every
    stop above it is resting there and everyone can see it, which is a fact about other people
    rather than about the price. So each window reports its extremes, where price sits between
    them, and how far away each one is.

    The part that makes it actionable is the last column. A level reached on ordinary volume is
    price drifting into it; the same level reached on twice the usual volume is somebody taking
    it. The ratio is measured against this market's own recent bars, so it means the same thing
    in a quiet week and a violent one, and it is reported beside the level rather than mixed into
    it — this says "here is the level and here is who turned up", never "therefore buy".

    Pure arithmetic over the bars it names: no fit, no guess, and every figure recomputable from
    the candles handed in.
    """
    if len(candles) < LEVEL_VOLUME_WINDOW + 1:
        return {"ready": False, "note": f"needs {LEVEL_VOLUME_WINDOW + 1} bars, has {len(candles)}"}
    price = candles[-1].close
    recent = [c.volume for c in candles[-LEVEL_VOLUME_WINDOW - 1 : -1]]
    average = sum(recent) / len(recent) if recent else 0.0
    ratio = round(candles[-1].volume / average, 2) if average > 0 else None

    out = []
    for w in windows:
        bars = candles[-int(w["bars"]) :]
        if len(bars) < 2:
            continue
        low, high = min(c.low for c in bars), max(c.high for c in bars)
        span = high - low
        to_high = 100 * (high / price - 1)
        to_low = 100 * (low / price - 1)
        # Which extreme is live. Both can be, in a range tight enough.
        near = []
        if abs(to_high) <= IN_PLAY_PCT:
            near.append("high")
        if abs(to_low) <= IN_PLAY_PCT:
            near.append("low")
        out.append({
            "key": w["key"], "label": w["label"], "bars": len(bars),
            "low": round(low, 2), "high": round(high, 2),
            "position": round((price - low) / span, 3) if span > 0 else None,
            "to_low_pct": round(to_low, 3), "to_high_pct": round(to_high, 3),
            "in_play": near,
        })
    return {
        "ready": True,
        "price": round(price, 2),
        "volume_ratio": ratio,
        "volume_window": LEVEL_VOLUME_WINDOW,
        "in_play_pct": IN_PLAY_PCT,
        "windows": out,
    }


