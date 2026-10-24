"""What kind of market this is, and which structure fits it.

Two measurements decide almost everything for an options seller:

  - **Volatility premium**: the ATM IV the options are priced at, against the volatility the
    underlying has actually realised (24h and 7d, from the stored 15m bars). Selling premium pays
    on average only when IV is above what is realised; buying it pays when IV is below.
  - **Direction**: whether price is trending (hourly EMA alignment, the 24h move measured in its own
    expected moves, hourly RSI). A neutral structure loses to a trend; a credit spread wants one.

Each strategy gets a rule score from 50, moved by named components — every point is written down
in `reasons`, so a score can be checked rather than trusted. The scores are heuristics over measured
numbers, not fitted probabilities; the probability of profit is the pricing module's job.
Pure: bars and a chain summary in, numbers out.
"""

from __future__ import annotations

import math
from typing import Any

from app.market.indicators import ema, fold, realised_vol, rsi

BAR_HOURS = 0.25  # the stored series is 15m
YEAR_HOURS = 365 * 24


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def measure(bars: list[dict[str, Any]], spot: float, summary: dict[str, Any]) -> dict[str, Any]:
    """The numbers the scores are made of. Anything without enough history is None."""
    closes = [b["close"] for b in bars]
    rv_24h = realised_vol(closes[-97:], BAR_HOURS) if len(closes) >= 97 else None
    rv_7d = realised_vol(closes[-673:], BAR_HOURS) if len(closes) >= 200 else None
    known = [v for v in (rv_24h, rv_7d) if v]
    rv_ref = sum(known) / len(known) if known else None
    iv = summary.get("atm_iv")

    change_24h = closes[-1] / closes[-97] - 1 if len(closes) >= 97 and closes[-97] else None
    z_24h = change_24h / (rv_ref * math.sqrt(24 / YEAR_HOURS)) if change_24h is not None and rv_ref else None

    hourly = fold(bars, 3600)
    h_closes = [b["close"] for b in hourly]
    ema_fast = ema(h_closes, 20)[-1] if len(h_closes) >= 50 else None
    ema_slow = ema(h_closes, 50)[-1] if len(h_closes) >= 50 else None
    last_day = bars[-96:]
    high_24h = max((b["high"] for b in last_day), default=None)
    low_24h = min((b["low"] for b in last_day), default=None)
    max_pain = summary.get("max_pain")

    return {
        "spot": spot,
        "atm_iv": iv,
        "rv_24h": rv_24h,
        "rv_7d": rv_7d,
        "vol_premium": (iv - rv_ref) if iv and rv_ref else None,
        "rv_ratio": (rv_24h / rv_7d) if rv_24h and rv_7d else None,
        "change_24h_pct": None if change_24h is None else 100 * change_24h,
        "z_24h": z_24h,
        "ema_fast_1h": ema_fast,
        "ema_slow_1h": ema_slow,
        "rsi_1h": rsi(h_closes, 14),
        "range_position_24h": (spot - low_24h) / (high_24h - low_24h) if high_24h and low_24h and high_24h > low_24h else None,
        "implied_move_pct": summary.get("implied_move_pct"),
        "max_pain": max_pain,
        "max_pain_distance_pct": 100 * (max_pain - spot) / spot if max_pain and spot else None,
        "pcr_oi": summary.get("pcr_oi"),
        "skew_25d": summary.get("skew_25d"),
        "call_wall": summary.get("call_wall"),
        "put_wall": summary.get("put_wall"),
        "bars": len(bars),
    }


def direction(m: dict[str, Any]) -> tuple[float, list[str]]:
    """-1 (strong down) .. +1 (strong up), with the parts that made it."""
    parts: list[str] = []
    d = 0.0
    if m["ema_fast_1h"] and m["ema_slow_1h"]:
        up = m["ema_fast_1h"] > m["ema_slow_1h"] and m["spot"] > m["ema_fast_1h"]
        down = m["ema_fast_1h"] < m["ema_slow_1h"] and m["spot"] < m["ema_fast_1h"]
        if up or down:
            d += 0.4 if up else -0.4
            parts.append(f"hourly EMA20 {'above' if up else 'below'} EMA50 with price {'over' if up else 'under'} both")
    if m["z_24h"] is not None:
        d += _clamp(m["z_24h"] / 2, -1, 1) * 0.4
        parts.append(f"24h move {m['change_24h_pct']:+.2f}% = {m['z_24h']:+.2f} expected moves")
    if m["rsi_1h"] is not None:
        d += (m["rsi_1h"] - 50) / 50 * 0.2
        parts.append(f"hourly RSI {m['rsi_1h']:.0f}")
    return _clamp(d, -1, 1), parts


def label(m: dict[str, Any], d: float) -> str:
    if d > 0.45:
        return "trending up"
    if d < -0.45:
        return "trending down"
    if m["rv_ratio"] and m["rv_ratio"] > 1.4:
        return "volatile"
    if m["rv_ratio"] and m["rv_ratio"] < 0.75 and abs(d) < 0.25:
        return "quiet range"
    return "range"


def score(m: dict[str, Any], d: float, regime: str) -> dict[str, dict[str, Any]]:
    vp_pts = 100 * m["vol_premium"] if m["vol_premium"] is not None else None
    expanding = bool(m["rv_ratio"] and m["rv_ratio"] > 1.3)
    compressed = bool(m["rv_ratio"] and m["rv_ratio"] < 0.75)
    near_pain = (
        m["max_pain_distance_pct"] is not None and m["implied_move_pct"]
        and abs(m["max_pain_distance_pct"]) < 0.5 * m["implied_move_pct"]
    )
    out: dict[str, dict[str, Any]] = {}

    def add(key: str, parts: list[tuple[str, float]]) -> None:
        total = 50 + sum(p for _, p in parts)
        out[key] = {
            "score": round(_clamp(total, 0, 100)),
            "reasons": [f"{text} ({points:+.0f})" for text, points in parts if abs(points) >= 0.5],
        }

    def premium(sell: bool, weight: float = 2.5) -> list[tuple[str, float]]:
        if vp_pts is None:
            return [("no realised volatility yet to compare IV with", -10 if sell else -5)]
        pts = _clamp(vp_pts * weight * (1 if sell else -1), -25, 25)
        word = "premium" if vp_pts >= 0 else "discount"
        return [(f"IV {100 * m['atm_iv']:.1f}% vs realised {100 * (m['atm_iv'] - m['vol_premium']):.1f}%: {abs(vp_pts):.1f} vol pts {word}", pts)]

    trend = abs(d)
    add("iron_condor", [
        *premium(True),
        ("trend against a neutral structure", -trend * 35),
        ("price ranging", 10 if regime in ("range", "quiet range") else 0),
        ("realised volatility expanding", -10 if expanding else 0),
    ])
    add("iron_butterfly", [
        *premium(True),
        ("trend against a pinned structure", -trend * 45),
        ("price near max pain", 8 if near_pain else 0),
        ("volatility compressed", 6 if compressed else 0),
        ("realised volatility expanding", -15 if expanding else 0),
    ])
    add("short_strangle", [
        *premium(True),
        ("trend against a neutral structure", -trend * 35),
        ("price ranging", 8 if regime in ("range", "quiet range") else 0),
        ("unbounded risk while volatility expands", -18 if expanding else 0),
    ])
    add("short_straddle", [
        *premium(True),
        ("trend against a pinned structure", -trend * 50),
        ("price near max pain", 6 if near_pain else 0),
        ("unbounded risk while volatility expands", -22 if expanding else 0),
    ])
    add("bull_put_spread", [
        *premium(True, 1.2),
        ("uptrend", d * 40 if d > 0 else 0),
        ("downtrend against a bullish spread", d * 45 if d < 0 else 0),
    ])
    add("bear_call_spread", [
        *premium(True, 1.2),
        ("downtrend", -d * 40 if d < 0 else 0),
        ("uptrend against a bearish spread", -d * 45 if d > 0 else 0),
    ])
    add("long_straddle", [
        *premium(False),
        ("volatility compressed, often released", 10 if compressed else 0),
        ("movement under way", trend * 10),
    ])
    return out


def analyze(bars: list[dict[str, Any]], spot: float, summary: dict[str, Any]) -> dict[str, Any]:
    m = measure(bars, spot, summary)
    d, parts = direction(m)
    regime = label(m, d)
    notes = []
    if m["bars"] < 200:
        notes.append(f"only {m['bars']} stored 15m bars: realised volatility over 7 days is not measured yet")
    if not m["atm_iv"]:
        notes.append("no ATM implied volatility in the chain")
    return {
        "label": regime,
        "direction": round(d, 3),
        "direction_parts": parts,
        "metrics": {k: (round(v, 6) if isinstance(v, float) else v) for k, v in m.items()},
        "scores": score(m, d, regime),
        "notes": notes,
    }
