"""The day's bias: one score from -1 to +1 and a style, every point with the reason it was given.

    where today opened against yesterday's value   above VAH buyers took the auction (+), below
                                                    VAL sellers did (-); inside, the market is
                                                    still agreeing on value: a range day
    price against yesterday's point of control      which side of the magnet it trades
    the 4h structure's last break                   the higher timeframe
    today's CVD                                     who has been crossing the spread (the tape)
    option premium bought, calls against puts       where option money went (the tape)
    funding at an extreme                           the crowded side, read against it

STYLE says how to trade it, not which way: "range" (positive gamma, or an open inside value) fades
the edges; "trend" (negative gamma, or an open outside value) follows breaks.

Every part is optional and says so when it is missing: the tape is only recorded from the day it
was switched on, and a reason nobody can check is not a reason. Days are UTC, as the profiles are.
"""

from __future__ import annotations

from typing import Any

from app.analysis import profile as profile_mod
from app.analysis import structure as structure_mod
from app.market.delta import Candle

DAY = 86_400
BULLISH, BEARISH = 0.25, -0.25


def _clamp(x: float) -> float:
    return max(-1.0, min(1.0, x))


def read(
    candles: list[Candle], now: float, *, gex: dict[str, Any] | None = None, cvd: tuple[float, float] | None = None,
    options: dict[str, float] | None = None, funding_pct: float | None = None,
) -> dict[str, Any]:
    """`candles` are 15m bars covering at least the last two days (weeks, for the 4h structure).
    `cvd` is today's (delta, volume) from the tape; `options` today's premium that crossed."""
    today = int(now // DAY * DAY)
    yesterday = [c for c in candles if today - DAY <= c.time < today]
    today_bars = [c for c in candles if c.time >= today]
    if len(yesterday) < 48 or not today_bars:
        return {"ready": False, "note": "needs yesterday's 15m bars and at least one of today's"}
    prior = profile_mod.build(yesterday, label="yesterday", source="15m", complete=True)
    if prior is None:
        return {"ready": False, "note": "yesterday's bars carry no volume to profile"}
    opened, price = today_bars[0].open, today_bars[-1].close
    parts: list[tuple[str, float]] = []

    if opened > prior.value_high:
        parts.append((f"opened {opened:,.2f} above yesterday's value ({prior.value_high:,.2f}): buyers took the auction", 0.25))
    elif opened < prior.value_low:
        parts.append((f"opened {opened:,.2f} below yesterday's value ({prior.value_low:,.2f}): sellers took the auction", -0.25))
    else:
        parts.append((f"opened inside yesterday's value ({prior.value_low:,.2f}-{prior.value_high:,.2f}): still agreeing on price", 0.0))
    side = 1 if price > prior.poc else -1
    parts.append((f"trading {'above' if side > 0 else 'below'} yesterday's point of control {prior.poc:,.2f}", 0.15 * side))

    four_hour = structure_mod.resample([c for c in candles if c.time < today + DAY], 16)
    last_break = structure_mod.read(four_hour).last_break if len(four_hour) >= 30 else None
    if last_break is not None:
        up = last_break.direction == "up"
        parts.append((f"the 4h structure last broke {last_break.direction} through {last_break.level:,.2f}", 0.2 if up else -0.2))
    else:
        parts.append(("no 4h break on record yet", 0.0))

    if cvd and cvd[1] > 0:
        lean = cvd[0] / cvd[1]
        if abs(lean) >= 0.05:
            parts.append((f"today's tape: {lean:+.0%} of the volume was net {'buying' if lean > 0 else 'selling'}", 0.15 if lean > 0 else -0.15))
        else:
            parts.append((f"today's tape is balanced ({lean:+.0%} net)", 0.0))
    if options:
        calls = options.get("call_bought", 0.0) - options.get("call_sold", 0.0)
        puts = options.get("put_bought", 0.0) - options.get("put_sold", 0.0)
        total = sum(abs(options.get(k, 0.0)) for k in ("call_bought", "call_sold", "put_bought", "put_sold"))
        if total > 0 and abs(calls - puts) / total >= 0.2:
            bull = calls > puts
            parts.append((f"option premium leaned to {'calls' if bull else 'puts'} today (net ${calls - puts:+,.0f})", 0.1 if bull else -0.1))
    if funding_pct is not None and abs(funding_pct) >= 0.03:
        parts.append((f"funding {funding_pct:+.3f}%: the {'long' if funding_pct > 0 else 'short'} side is crowded", -0.05 if funding_pct > 0 else 0.05))

    score = _clamp(sum(p for _, p in parts))
    inside = prior.value_low <= opened <= prior.value_high
    gamma = (gex or {}).get("regime")
    style = "range" if (gamma == "positive" and inside) else "trend" if (gamma == "negative" or not inside) else "mixed"
    return {
        "ready": True, "day": today, "score": round(score, 3),
        "label": "bullish" if score >= BULLISH else "bearish" if score <= BEARISH else "neutral",
        "style": style, "gamma": gamma, "open": opened, "price": price,
        "reasons": [{"text": text, "points": round(points, 2)} for text, points in parts],
        "levels": {
            "vah": prior.value_high, "val": prior.value_low, "poc": prior.poc,
            "flip": (gex or {}).get("flip"), "call_wall": (gex or {}).get("call_wall"), "put_wall": (gex or {}).get("put_wall"),
        },
    }


def settle(label: str, opened: float, close: float) -> tuple[float, bool | None]:
    """The day's return, and whether a directional bias got its sign right (neutral is not scored)."""
    ret = close / opened - 1 if opened else 0.0
    if label == "neutral":
        return ret, None
    return ret, (ret > 0) == (label == "bullish")
