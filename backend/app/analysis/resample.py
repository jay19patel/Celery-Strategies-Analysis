"""Higher timeframes from one stored base, folded on the clock.

Every timeframe above the base is *derived*, never fetched. Fetching each one separately is how a
dashboard ends up with an hourly candle that is not the four fifteen-minute candles underneath it —
the two come from different requests, taken at different moments, and nothing in the system can
tell you which is right. Here there is one answer because there is one source.

What the rules are, and why each one:

  aligned to the clock   A bucket starts at ``time // span * span``. Epoch seconds are UTC, and
                         60 / 300 / 900 / 3600 / 14400 / 86400 all divide the epoch evenly, so the
                         daily bar starts at 00:00 UTC and the 4-hour bars at 00, 04, 08 … with no
                         timezone arithmetic anywhere. A market that trades every day of the year
                         has no other sensible anchor.

  label = left           A bar is stamped with the time it OPENED, covering ``[T, T + span)`` —
                         the same convention the stored `bars` table already uses. Stamp it on the
                         right and every chart and every join silently shifts by one bar.

  closed = left          A bucket is emitted only once the source has run past its end. Not "once
                         it has enough bars" — a quiet hour with two minutes of trading is still a
                         finished hour, while a busy one still in progress is not. This is the rule
                         that stops a still-forming daily bar being drawn as a closed one, which is
                         the most common way a backtest ends up seeing the future.

  coverage, stated       An hour missing half its minutes is still an hour, but it is not an hour
                         you should measure anything against. Each folded bar carries how many
                         source bars it was built from, and `fold` drops the ones below
                         `min_coverage` rather than quietly averaging over a hole.

Rows in, rows out: plain dicts shaped like `store.bars()` returns, so the API, the analytics and a
backtest all fold the same way. Pure — no clock, no network, no database handle. The absence of a
clock is deliberate: "has this bucket finished" is answered by the data, so the same bars fold to
the same higher timeframe today, tomorrow and inside a replay of last March.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from typing import Any

# Every interval the system folds to, and how long one of its bars is.
BAR_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14_400, "1d": 86_400}


# What a fold will accept before it refuses to emit the bar. A tenth of an hour missing barely
# moves its high, low or close; a tenth of a day missing is two and a half hours of market nobody
# saw, and the open or the close may be on the wrong side of it. So: strict for short folds where a
# hole is a large share of the bar, looser for long ones where it is not.
MIN_COVERAGE = {"3m": 1.0, "5m": 1.0, "15m": 1.0, "30m": 0.9, "1h": 0.9, "4h": 0.9, "1d": 0.9}


class ResampleError(ValueError):
    """A fold that cannot be done: an unknown interval, or a target below the source."""


def seconds(interval: str) -> int:
    try:
        return BAR_SECONDS[interval]
    except KeyError:
        raise ResampleError(f"unknown interval {interval!r}; known: {', '.join(BAR_SECONDS)}") from None


def bucket(time: float, span: int) -> int:
    """The start of the bucket `time` falls in. Floor division, so it is the same answer for every
    bar in the bucket and for the bucket's own start."""
    return int(time) // span * span


def divides(source: str, target: str) -> int:
    """How many `source` bars make one `target` bar. Raises unless it is a whole number — folding
    15m into 1h is four bars; folding 15m into "1d minus a bit" is not a timeframe."""
    lo, hi = seconds(source), seconds(target)
    if hi < lo:
        raise ResampleError(f"cannot fold {source} up from {target}: target is shorter than source")
    if hi % lo:
        raise ResampleError(f"{target} is not a whole number of {source} bars")
    return hi // lo


# -- folding -------------------------------------------------------------------------


def fold(
    rows: Sequence[Mapping[str, Any]], source: str, target: str, min_coverage: float | None = None,
) -> list[dict[str, Any]]:
    """`rows` (oldest first, as `store.bars` returns them) folded up to `target`.

    Open is the first bar's open, close the last bar's close, high and low the extremes, volume the
    sum. Open interest and funding are *levels*, not flows, so they take the last known value in the
    bucket rather than a sum — adding up open interest would be meaningless.

    Only buckets the source has run past are emitted, so the bar still forming is never returned.
    `bars` on each result says how many source bars went into it, and `coverage` the share of a full
    bucket that is — both kept on the row so a caller can show a hole instead of hiding it.
    """
    want = divides(source, target)
    span = seconds(target)
    floor = MIN_COVERAGE.get(target, 1.0) if min_coverage is None else min_coverage
    # Rows must actually be the source they are claimed to be. Hand 1m bars to a fold that was told
    # "15m" and nothing fails — it just builds buckets of the wrong size and reports a coverage of
    # 1,500%. The bar times themselves settle it: a 15m series sits on 15m boundaries.
    step = seconds(source)
    off = next((int(r["time"]) for r in rows if int(r["time"]) % step), None)
    if off is not None:
        raise ResampleError(f"bar at {off} is not on a {source} boundary: these rows are not {source} bars")
    if want == 1:
        return [dict(r) | {"bars": 1, "coverage": 1.0} for r in rows]

    groups: dict[int, list[Mapping[str, Any]]] = {}
    for r in rows:
        groups.setdefault(bucket(r["time"], span), []).append(r)
    if not groups:
        return []

    # Where the source series actually reaches: the last bar's open plus its own length. A bucket
    # ending after this has not finished, whatever is inside it.
    reach = int(max(int(r["time"]) for r in rows)) + seconds(source)

    out = []
    for start in sorted(groups):
        if start + span > reach:
            continue  # still forming
        group = sorted(groups[start], key=lambda r: r["time"])
        share = len(group) / want
        if share < floor:
            continue
        out.append({
            "time": start,
            "open": float(group[0]["open"]),
            "high": max(float(r["high"]) for r in group),
            "low": min(float(r["low"]) for r in group),
            "close": float(group[-1]["close"]),
            "volume": sum(float(r.get("volume") or 0) for r in group),
            "oi": _last(group, "oi"),
            "funding": _last(group, "funding"),
            "bars": len(group),
            "coverage": round(share, 4),
        })
    return out


def _last(group: Sequence[Mapping[str, Any]], key: str) -> float | None:
    """The newest value of a level inside the bucket, or None when nothing carried one."""
    for r in reversed(group):
        v = r.get(key)
        if v is not None:
            return float(v)
    return None


# -- what is wrong with the base ------------------------------------------------------


def gaps(rows: Sequence[Mapping[str, Any]], interval: str) -> list[tuple[int, int]]:
    """(after, before) pairs where consecutive bars are more than one bar apart.

    Reported, never filled. A chart that joins across a missing hour draws a move the market did
    not make, and a base rate counted across one is counted over market nobody saw."""
    step = seconds(interval)
    times = sorted(int(r["time"]) for r in rows)
    return [(a, b) for a, b in itertools.pairwise(times) if b - a > step]
