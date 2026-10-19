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
from typing import Any

from tradebuddy import structure as st
from tradebuddy.delta import Candle
from tradebuddy.strategies.indicators import adx, atr, bollinger, ema, macd, obv, rsi

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


def indicator_block(candles: list[Candle]) -> dict[str, Any]:
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
        "series": [
            {"time": c.time, "close": c.close} for c in candles[-VOLUME_WINDOW:]
        ],
        "rsi_series": [{"time": candles[i].time, "value": round(v, 1)}
                       for i, v in zip(range(len(candles) - len(r), len(candles)), r, strict=True)][-VOLUME_WINDOW:],
        "adx_series": [{"time": candles[i].time, "value": round(v, 1)}
                       for i, v in zip(range(len(candles) - len(adx_line), len(candles)), adx_line, strict=True)][-VOLUME_WINDOW:],
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
