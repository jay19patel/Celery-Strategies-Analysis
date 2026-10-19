"""The Market page's tabs, each one a self-contained reading of the same bars.

The page was one long scroll and the reader had to work out which numbers belonged together — which
is how a trend reading and a skill-less model's direction ended up next to each other saying
opposite things. The tabs are drawn along the only line that matters: **what the reading is made
of**, the same split `provenance.py` names.

    Volume        who is behind the move, from traded size alone
    Patterns      structure and candle shapes, each with what it has actually led to
    Indicators    the classical readings, and the arithmetic of how far price can travel
    Model & AI    the forecaster, used only where it beat its baseline, and TB-AI's prose
    Open interest what the options and futures books are positioned for
    Scorecard     what every card claimed, against what happened

Each block returns numbers plus a verdict, so a tab can be read in one line and then unpacked.
Pure: bars in, numbers out, no clock and no database.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from tradebuddy import structure as st
from tradebuddy.delta import Candle
from tradebuddy.strategies.indicators import adx, atr, bollinger, ema, macd, obv, rsi, supertrend

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

    # Volume that arrived on up closes against down closes, over the last day. Not a forecast —
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


def indicator_block(candles: list[Candle], window: int = VOLUME_WINDOW) -> dict[str, Any]:
    """The classical readings, each with the state it is in rather than a bare number.

    "RSI 68" is only useful if you remember where the lines are. Every reading here carries its own
    verdict word, and ADX is the one that decides whether the others mean anything: below 20 the
    market is ranging and trend-following readings misfire, which is the single most common way
    indicators are misread.
    """
    if len(candles) < 60:
        return {"ready": False, "note": f"needs 60 bars, has {len(candles)}"}
    closes = [c.close for c in candles]
    highs, lows = [c.high for c in candles], [c.low for c in candles]
    price = closes[-1]

    r = rsi(closes, 14)
    macd_line, macd_sig, macd_hist = macd(closes)
    adx_line, pdi, mdi = adx(highs, lows, closes, 14)
    mid, upper, lower = bollinger(closes, 20, 2.0)
    bands = atr(highs, lows, closes, 14)
    emas = {n: ema(closes, n) for n in (20, 50, 200)}
    line = supertrend(highs, lows, closes, 10, 3.0)

    def state(value: float | None, low: float, high: float, below: str, between: str, above: str) -> str:
        if value is None:
            return "unknown"
        return below if value < low else above if value > high else between

    width = round(100 * (upper[-1] - lower[-1]) / mid[-1], 3) if mid else None
    widths = [100 * (u - lo) / m for m, u, lo in zip(mid[-50:], upper[-50:], lower[-50:], strict=True) if m] if mid else []
    stack = [emas[n][-1] if emas[n] else None for n in (20, 50, 200)]
    return {
        "ready": True,
        "price": round(price, 2),
        "rsi": {
            "value": round(r[-1], 1) if r else None,
            "state": state(r[-1] if r else None, 30, 70, "oversold", "neutral", "overbought"),
        },
        "macd": {
            "line": round(macd_line[-1], 4) if macd_line else None,
            "signal": round(macd_sig[-1], 4) if macd_sig else None,
            "hist": round(macd_hist[-1], 4) if macd_hist else None,
            "state": "rising" if macd_hist and macd_hist[-1] > 0 else "falling" if macd_hist else "unknown",
        },
        "adx": {
            "value": round(adx_line[-1], 1) if adx_line else None,
            "plus_di": round(pdi[-1], 1) if pdi else None,
            "minus_di": round(mdi[-1], 1) if mdi else None,
            # The gate on every other reading here.
            "state": state(adx_line[-1] if adx_line else None, 20, 25, "ranging", "forming", "trending"),
        },
        "bollinger": {
            "upper": round(upper[-1], 2) if upper else None,
            "middle": round(mid[-1], 2) if mid else None,
            "lower": round(lower[-1], 2) if lower else None,
            "width_pct": width,
            "position": round((price - lower[-1]) / (upper[-1] - lower[-1]), 3) if upper and upper[-1] > lower[-1] else None,
            # Squeeze is relative to this market's own recent width, not an absolute number.
            "squeeze": bool(widths and width is not None and width < 0.6 * (sum(widths) / len(widths))),
        },
        "atr": {
            "value": round(bands[-1], 2) if bands else None,
            "pct": round(100 * bands[-1] / price, 3) if bands else None,
        },
        "ema": {
            "ema20": round(stack[0], 2) if stack[0] else None,
            "ema50": round(stack[1], 2) if stack[1] else None,
            "ema200": round(stack[2], 2) if stack[2] else None,
            "stacked_up": bool(all(stack) and stack[0] > stack[1] > stack[2]),
            "stacked_down": bool(all(stack) and stack[0] < stack[1] < stack[2]),
        },
        # Where the trend claim stops being true. The only reading here that names a price rather
        # than a state, which is why a strategy can put a stop on it and the others cannot.
        "supertrend": {
            "level": round(line[-1][0], 2) if line else None,
            "direction": line[-1][1] if line else 0,
            "state": "above price" if line and line[-1][1] < 0 else "below price" if line else "unknown",
            "distance_pct": round(100 * abs(price - line[-1][0]) / price, 3) if line else None,
        },
        "series": [{"time": c.time, "close": c.close} for c in candles[-window:]],
        "rsi_series": _series(candles, r, window),
        "adx_series": _series(candles, adx_line, window),
        "macd_series": _series(candles, macd_hist, window),
    }


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


# -- what the broker would actually have done ------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """How a bracketed trade from one bar actually ended."""

    exit: str  # "tp" | "sl" | "timeout"
    bars_held: int
    net_pct: float  # after the round trip, as a % of entry
    r_multiple: float  # in units of the risk taken, which is the only comparable scale

    @property
    def won(self) -> bool:
        return self.net_pct > 0


def barrier_outcome(
    candles: list[Candle], i: int, side: str, stop_pct: float, target_pct: float,
    lookahead: int, cost_pct: float = 0.14,
) -> Outcome | None:
    """Walk forward from bar `i` and return what a bracketed trade would have done.

    This is the only honest outcome for this system. Everything was scored on "did the close go up
    over N hours", but the Trader places a stop and a target (invariant 7) — so the page was
    marking a question nobody trades. A forecast can be "right" while the trade it implies was
    stopped out eight hours earlier.

    Three properties, each of which changes the answer:

      SL wins a tie     when both barriers fall inside one candle, intrabar order is unknown, so
                        the loss is assumed. Optimism here is how a backtest doubles its win rate.
      entry at the next open  the signal bar's close is not tradeable.
      net of costs      a 52% hit rate is a loss after fees, so fees come off before `won`.

    Returns None when the bars do not cover the horizon — unsettled, not assumed flat.
    """
    if not 0 <= i < len(candles) - 1 or stop_pct <= 0 or target_pct <= 0:
        return None
    entry = candles[i + 1].open
    if entry <= 0:
        return None
    long = side in ("buy", "long", "bull", "bullish", "up")
    stop = entry * (1 - stop_pct / 100) if long else entry * (1 + stop_pct / 100)
    target = entry * (1 + target_pct / 100) if long else entry * (1 - target_pct / 100)
    risk = abs(entry - stop) / entry * 100

    last = min(i + lookahead, len(candles) - 1)
    if last <= i:
        return None
    for step, bar in enumerate(candles[i + 1 : last + 1], start=1):
        hit_stop = bar.low <= stop if long else bar.high >= stop
        hit_target = bar.high >= target if long else bar.low <= target
        if hit_stop:  # before the target: one candle cannot say which came first
            net = -stop_pct - cost_pct
            return Outcome("sl", step, round(net, 4), round(net / risk, 3) if risk else 0.0)
        if hit_target:
            net = target_pct - cost_pct
            return Outcome("tp", step, round(net, 4), round(net / risk, 3) if risk else 0.0)
    if last < i + lookahead:
        return None  # the horizon is not covered yet
    close = candles[last].close
    move = (close / entry - 1) * 100 * (1 if long else -1)
    net = move - cost_pct
    return Outcome("timeout", last - i, round(net, 4), round(net / risk, 3) if risk else 0.0)


def regime_at(candles: list[Candle], i: int, window: int = 96) -> str:
    """One word for the market a claim was made in, so the scorecard can be read per regime.

    A card that is useless on average can be good in one regime and bad in another, and the
    average buries both. Computed from the bars at scoring time rather than stored, so it applies
    to every claim already on record instead of only to new ones.
    """
    if i < window + 15:
        return "unknown"
    window_bars = candles[max(0, i - window) : i + 1]
    highs = [c.high for c in window_bars]
    lows = [c.low for c in window_bars]
    closes = [c.close for c in window_bars]
    strength, _, _ = adx(highs, lows, closes, 14)  # (adx, +DI, -DI): only the first is the regime
    if not strength:
        return "unknown"
    trending = strength[-1] >= 25
    # Fast or slow against this market's own recent pace, not an absolute number.
    recent = st.realised_vol(closes, min(48, len(closes) - 1), 900)
    longer = st.realised_vol([c.close for c in candles[max(0, i - 4 * window) : i + 1]], min(384, i), 900)
    fast = bool(recent and longer and recent > 1.2 * longer)
    if trending:
        return "trending fast" if fast else "trending"
    return "ranging fast" if fast else "ranging"


TIMEFRAMES = (
    {"interval": "15m", "factor": 1, "label": "Short term", "window": 96},
    {"interval": "1h", "factor": 4, "label": "Medium term", "window": 120},
    {"interval": "1d", "factor": 96, "label": "Long term", "window": 120},
)


def indicators_by_timeframe(candles: list[Candle]) -> dict[str, Any]:
    """The same readings on every timeframe, folded from the one stored series.

    Reading RSI on a 15-minute chart and nothing else is how a pullback inside a daily uptrend gets
    traded as a reversal. The readings disagree across timeframes most of the time, and which ones
    agree is worth more than any single value.
    """
    out: dict[str, Any] = {}
    for spec in TIMEFRAMES:
        folded = st.resample(candles, spec["factor"])
        block = indicator_block(folded, spec["window"])
        out[spec["interval"]] = block | {"label": spec["label"], "bars": len(folded)}
    return out


def indicator_agreement(by_tf: dict[str, Any]) -> dict[str, Any]:
    """Which way each timeframe's readings lean, and how much they agree.

    One vote per reading per timeframe — EMA stack, MACD histogram, RSI either side of 50, and
    Supertrend's side of price — gated by whether ADX says there is a trend to read at all.
    """
    rows = []
    for interval, b in by_tf.items():
        if not b.get("ready"):
            rows.append({"interval": interval, "votes": 0, "lean": "unknown", "trending": False, "note": b.get("note", "")})
            continue
        ema_b = b["ema"]
        votes = [
            1 if ema_b["stacked_up"] else -1 if ema_b["stacked_down"] else 0,
            1 if (b["macd"]["hist"] or 0) > 0 else -1 if (b["macd"]["hist"] or 0) < 0 else 0,
            1 if (b["rsi"]["value"] or 50) > 55 else -1 if (b["rsi"]["value"] or 50) < 45 else 0,
            b["supertrend"]["direction"] or 0,
        ]
        total = sum(votes)
        rows.append({
            "interval": interval,
            "label": b.get("label", interval),
            "votes": total,
            "of": len(votes),
            "lean": "up" if total >= 2 else "down" if total <= -2 else "mixed",
            "trending": b["adx"]["state"] == "trending",
            "adx": b["adx"]["value"],
            "detail": {"ema": votes[0], "macd": votes[1], "rsi": votes[2], "supertrend": votes[3]},
        })
    scores = [r["votes"] for r in rows if r.get("lean") != "unknown"]
    agree = "aligned up" if scores and all(v >= 2 for v in scores) else (
        "aligned down" if scores and all(v <= -2 for v in scores) else (
            "conflicting" if scores and max(scores) >= 2 and min(scores) <= -2 else "mixed"))
    return {"rows": rows, "agreement": agree}


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


def levels_block(candles: list[Candle], bar_seconds: int = 900) -> dict[str, Any]:
    """The high and low of each well-known window, and where price sits between them.

    These are the levels other people are watching, which is most of why they matter — a 52-week
    high is not a prediction, it is a price a great many orders are parked around. Each window
    reports how far price is from both ends, how long ago the extreme was set, and whether it is
    close enough to be in play rather than a fact about the past.

    A fresh extreme is called out separately: making a new high today is a different event from
    sitting under one set three weeks ago.
    """
    if len(candles) < 20:
        return {"ready": False, "note": f"needs 20 bars, has {len(candles)}"}
    price = candles[-1].close
    out = []
    for spec in LEVEL_WINDOWS:
        window = candles[-spec["bars"]:] if len(candles) > spec["bars"] else candles
        if len(window) < 20:
            continue
        highs = [c.high for c in window]
        lows = [c.low for c in window]
        hi, lo = max(highs), min(lows)
        hi_at = len(window) - 1 - highs[::-1].index(hi)
        lo_at = len(window) - 1 - lows[::-1].index(lo)
        to_high = 100 * (hi / price - 1)
        to_low = 100 * (1 - lo / price)
        out.append({
            "key": spec["key"],
            "label": spec["label"],
            "complete": len(window) >= spec["bars"],  # a 52-week high needs 52 weeks of bars
            "bars": len(window),
            "high": round(hi, 2),
            "low": round(lo, 2),
            "to_high_pct": round(to_high, 2),
            "to_low_pct": round(to_low, 2),
            "position": round((price - lo) / (hi - lo), 3) if hi > lo else None,
            "high_hours_ago": round((len(window) - 1 - hi_at) * bar_seconds / 3600, 1),
            "low_hours_ago": round((len(window) - 1 - lo_at) * bar_seconds / 3600, 1),
            "high_in_play": to_high <= IN_PLAY_PCT,
            "low_in_play": to_low <= IN_PLAY_PCT,
            # Set on one of the last few bars: price is making the extreme, not approaching it.
            "fresh_high": hi_at >= len(window) - 3,
            "fresh_low": lo_at >= len(window) - 3,
        })
    return {"ready": bool(out), "price": round(price, 2), "windows": out}
