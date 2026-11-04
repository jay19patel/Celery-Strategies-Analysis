"""Order flow from the recorded tape (`market.tape`): who crossed the spread, where, and whether it
moved price — measured, not estimated from candles.

    delta / CVD     aggressive buying minus selling per bar, and its running sum
    divergence      price makes a new extreme the CVD does not confirm
    imbalance       buying at a price against selling one step below it (the diagonal a footprint
                    is read on), at `IMBALANCE`x or more; STACKED when `STACK` in a row
    absorption      heavy, one-sided aggression that price refused: a big sell delta on a bar that
                    still closed up (bullish), or the mirror — someone passive took it all
    exhaustion      a new extreme on climax volume and delta, then within `EXHAUST_WINDOW` bars a
                    close back through that bar's middle with delta turned — the push ran out
    delta profile   delta by price over the window: where each side was aggressive

Pure and deterministic: rows in, numbers out. Every reading names the bar and the numbers it came
from, so a strategy, the dashboard and a backtest all get the same answer.
"""

from __future__ import annotations

import itertools
import statistics
from typing import Any

IMBALANCE = 3.0
STACK = 3
CLIMAX = 2.0  # volume against its mean that counts as heavy
ONE_SIDED = 0.25  # |delta| / volume that counts as one-sided
QUIET_RANGE = 0.6  # range against its mean that counts as "price did not move"
EXHAUST_WINDOW = 2
SWING = 20  # bars an extreme is measured over


def fold(rows: list[dict[str, Any]], minutes: int) -> list[dict[str, Any]]:
    """Minute rows into `minutes`-minute bars, aligned to the clock. A bucket missing its last minute
    is still the bar it is — the tape only writes minutes that traded."""
    span = minutes * 60
    out: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda r: r["time"]):
        start = int(r["time"]) // span * span
        if not out or out[-1]["time"] != start:
            out.append({"time": start, "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
                        "volume": 0.0, "buy": 0.0, "sell": 0.0, "trades": 0, "max_trade": 0.0, "levels": {},
                        "bid_depth": [], "ask_depth": []})
        b = out[-1]
        b["high"], b["low"], b["close"] = max(b["high"], r["high"]), min(b["low"], r["low"]), r["close"]
        for k in ("volume", "buy", "sell"):
            b[k] += r[k]
        b["trades"] += r["trades"]
        b["max_trade"] = max(b["max_trade"], r["max_trade"])
        for price, bought, sold in r.get("levels") or []:
            level = b["levels"].setdefault(float(price), [0.0, 0.0])
            level[0] += bought
            level[1] += sold
        for k in ("bid_depth", "ask_depth"):
            if r.get(k) is not None:
                b[k].append(r[k])
    for b in out:
        b["delta"] = b["buy"] - b["sell"]
        b["levels"] = sorted([p, v[0], v[1]] for p, v in b["levels"].items())
        for k in ("bid_depth", "ask_depth"):
            b[k] = statistics.fmean(b[k]) if b[k] else None
    return out


def imbalances(levels: list[list[float]], step: float, min_size: float = 0.0) -> list[dict[str, Any]]:
    """Diagonal imbalances in one bar's footprint: bought at a price against sold one step below.
    Only neighbouring steps are compared: where nothing traded in between there is no diagonal."""
    out = []
    for (p0, _b0, s0), (p1, b1, _s1) in itertools.pairwise(levels):
        if abs(p1 - p0 - step) > step * 0.01:
            continue
        if b1 >= IMBALANCE * max(s0, 1e-12) and b1 > min_size:
            out.append({"price": p1, "side": "buy", "ratio": round(b1 / s0, 1) if s0 else None})
        if s0 >= IMBALANCE * max(b1, 1e-12) and s0 > min_size:
            out.append({"price": p0, "side": "sell", "ratio": round(s0 / b1, 1) if b1 else None})
    return out


def stacked(found: list[dict[str, Any]], step: float) -> list[dict[str, Any]]:
    """Runs of `STACK` or more same-side imbalances at consecutive steps: a zone one side owned."""
    runs = []
    for side in ("buy", "sell"):
        prices = sorted(i["price"] for i in found if i["side"] == side)
        run: list[float] = []
        for p in prices:
            if run and abs(p - run[-1] - step) > step * 0.01:
                if len(run) >= STACK:
                    runs.append({"side": side, "low": run[0], "high": run[-1], "levels": len(run)})
                run = []
            run.append(p)
        if len(run) >= STACK:
            runs.append({"side": side, "low": run[0], "high": run[-1], "levels": len(run)})
    return runs


def _events(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Absorption and exhaustion over the window, each with the bar and the numbers behind it."""
    out: list[dict[str, Any]] = []
    for i in range(SWING, len(bars)):
        b, prior = bars[i], bars[i - SWING : i]
        vol = statistics.fmean(x["volume"] for x in prior) or 0.0
        rng = statistics.fmean(x["high"] - x["low"] for x in prior) or 0.0
        if vol <= 0 or rng <= 0 or b["volume"] < CLIMAX * vol:
            continue
        lean = b["delta"] / b["volume"] if b["volume"] else 0.0
        heavy = f"volume {b['volume'] / vol:.1f}x normal, delta {lean:+.0%} of it"
        # Absorption: one-sided aggression, a quiet range, and price closing against the aggressor.
        if abs(lean) >= ONE_SIDED and (b["high"] - b["low"]) <= QUIET_RANGE * rng:
            if lean < 0 and b["close"] >= b["open"]:
                out.append({"kind": "absorption", "side": "bullish", "time": b["time"], "price": b["low"],
                            "detail": f"selling absorbed at {b['low']:,.2f}: {heavy}, yet the bar closed up"})
            elif lean > 0 and b["close"] <= b["open"]:
                out.append({"kind": "absorption", "side": "bearish", "time": b["time"], "price": b["high"],
                            "detail": f"buying absorbed at {b['high']:,.2f}: {heavy}, yet the bar closed down"})
        # Exhaustion: a new extreme on the climax, then a close back through its middle, delta turned.
        mid = (b["high"] + b["low"]) / 2
        after = bars[i + 1 : i + 1 + EXHAUST_WINDOW]
        if lean >= ONE_SIDED and b["high"] >= max(x["high"] for x in prior):
            turn = next((a for a in after if a["close"] < mid and a["delta"] < 0), None)
            if turn:
                out.append({"kind": "exhaustion", "side": "bearish", "time": turn["time"], "price": b["high"],
                            "detail": f"buying climax at {b['high']:,.2f} ({heavy}), then a close back under {mid:,.2f} on selling"})
        if lean <= -ONE_SIDED and b["low"] <= min(x["low"] for x in prior):
            turn = next((a for a in after if a["close"] > mid and a["delta"] > 0), None)
            if turn:
                out.append({"kind": "exhaustion", "side": "bullish", "time": turn["time"], "price": b["low"],
                            "detail": f"selling climax at {b['low']:,.2f} ({heavy}), then a close back over {mid:,.2f} on buying"})
    return out


def _divergence(bars: list[dict[str, Any]], cvd: list[float]) -> dict[str, Any] | None:
    """The last bar's extreme against the swing before it, price and CVD side by side."""
    if len(bars) < 2 * SWING:
        return None
    now, before = slice(-SWING, None), slice(-2 * SWING, -SWING)
    high_now, high_before = max(b["high"] for b in bars[now]), max(b["high"] for b in bars[before])
    low_now, low_before = min(b["low"] for b in bars[now]), min(b["low"] for b in bars[before])
    if high_now > high_before and max(cvd[now]) < max(cvd[before]):
        return {"side": "bearish", "detail": f"price made a higher high ({high_now:,.2f}) on less net buying than the last ({high_before:,.2f})"}
    if low_now < low_before and min(cvd[now]) > min(cvd[before]):
        return {"side": "bullish", "detail": f"price made a lower low ({low_now:,.2f}) on less net selling than the last ({low_before:,.2f})"}
    return None


def profile(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Volume and delta by price over the window: the POC and where each side leaned hardest."""
    by: dict[float, list[float]] = {}
    for b in bars:
        for price, bought, sold in b["levels"]:
            row = by.setdefault(price, [0.0, 0.0])
            row[0] += bought
            row[1] += sold
    if not by:
        return {"ready": False}
    rows = sorted(by.items())
    poc = max(rows, key=lambda r: r[1][0] + r[1][1])[0]
    most_bought = max(rows, key=lambda r: r[1][0] - r[1][1])
    most_sold = min(rows, key=lambda r: r[1][0] - r[1][1])
    return {
        "ready": True, "poc": poc,
        "buyers_at": most_bought[0] if most_bought[1][0] > most_bought[1][1] else None,
        "sellers_at": most_sold[0] if most_sold[1][1] > most_sold[1][0] else None,
        "rows": [{"price": p, "bought": round(v[0], 4), "sold": round(v[1], 4), "delta": round(v[0] - v[1], 4)} for p, v in rows],
    }


def read(rows: list[dict[str, Any]], minutes: int = 5, step: float = 1.0) -> dict[str, Any]:
    """Everything above over the stored minutes, folded to `minutes`-minute bars."""
    bars = fold(rows, minutes)
    if len(bars) < SWING + 1:
        return {"ready": False, "note": f"needs {SWING + 1} bars of {minutes}m tape, has {len(bars)}: the tape is recorded from the day it was switched on",
                "bars": len(bars)}
    cvd, run = [], 0.0
    for b in bars:
        run += b["delta"]
        cvd.append(run)
    last = bars[-1]
    found = imbalances(last["levels"], step, min_size=statistics.fmean(b["volume"] for b in bars[-SWING:]) / 50)
    book = None
    if last["bid_depth"] and last["ask_depth"]:
        book = round((last["bid_depth"] - last["ask_depth"]) / (last["bid_depth"] + last["ask_depth"]), 3)
    return {
        "ready": True, "interval": f"{minutes}m", "bars": len(bars), "price": last["close"],
        "delta": last["delta"], "cvd": run,
        "series": [{"time": b["time"], "close": b["close"], "delta": b["delta"], "cvd": c} for b, c in zip(bars, cvd, strict=True)],
        "imbalances": found, "stacked": stacked(found, step),
        "events": _events(bars)[-20:],
        "divergence": _divergence(bars, cvd),
        "profile": profile(bars),
        "book_imbalance": book,  # +1 every resting order bids, -1 every one offers
        "footprint": [{"time": b["time"], "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"],
                       "delta": b["delta"], "levels": b["levels"]} for b in bars[-12:]],
    }
