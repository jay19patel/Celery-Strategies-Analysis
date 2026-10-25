"""Trendline and multi-swing support/resistance detection.

Pure and deterministic — candles in, trendlines and clustered levels out.
Detects:
1. Validated diagonal trendlines:
   - Ascending support connecting higher swing lows
   - Descending resistance connecting lower swing highs
   - Touch counts (2, 3, 4+ touches), slope, current projected price
2. Horizontal multi-swing clusters (cross-levels / key S/R):
   - Price levels where multiple swing highs or swing lows align within a tight threshold
3. Exponential Moving Averages (EMA 20 & EMA 50) for trend alignment
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.market.delta import Candle

SWING_PIVOT_WINDOW = 4  # Bars each side to qualify a local pivot peak or trough
TOUCH_TOLERANCE_PCT = 0.0035  # 0.35% price band to count as touching a trendline or level
CLUSTER_TOLERANCE_PCT = 0.0045  # 0.45% price band to cluster multi-swing highs/lows
MIN_TOUCHES = 2
MAX_TRENDLINES = 6
MAX_HORIZONTAL_LEVELS = 8


@dataclass
class SwingPoint:
    """A local peak or trough in the price series."""

    time: int
    price: float
    index: int
    kind: str  # "high" | "low"


@dataclass
class Trendline:
    """A diagonal support or resistance line connecting multiple swing points."""

    id: str
    kind: str  # "support" | "resistance"
    start_time: int
    start_price: float
    end_time: int
    end_price: float
    current_price: float  # Projected level at latest bar
    slope: float  # Price change per bar
    touches: int
    touch_times: list[int] = field(default_factory=list)
    distance_pct: float = 0.0  # Distance from latest close
    status: str = "holding"  # "holding" | "approaching" | "broken"
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HorizontalCluster:
    """A horizontal support or resistance level formed by multiple swing pivots."""

    id: str
    price: float
    kind: str  # "support" | "resistance" | "cross_level"
    touches: int
    touch_times: list[int] = field(default_factory=list)
    distance_pct: float = 0.0  # Distance from latest close
    status: str = "holding"  # "holding" | "approaching" | "broken"
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def find_swing_points(candles: list[Candle], window: int = SWING_PIVOT_WINDOW) -> list[SwingPoint]:
    """Find local high peaks and low troughs in the candlestick series."""
    if len(candles) < 2 * window + 1:
        return []

    points: list[SwingPoint] = []
    for i in range(window, len(candles) - window):
        c = candles[i]
        # Check swing high
        is_high = all(candles[j].high <= c.high for j in range(i - window, i + window + 1) if j != i)
        if is_high:
            points.append(SwingPoint(time=c.time, price=c.high, index=i, kind="high"))

        # Check swing low
        is_low = all(candles[j].low >= c.low for j in range(i - window, i + window + 1) if j != i)
        if is_low:
            points.append(SwingPoint(time=c.time, price=c.low, index=i, kind="low"))

    return points


def calculate_ema(values: list[float], period: int) -> list[float | None]:
    """Calculate Exponential Moving Average over a series of numbers."""
    if len(values) < period:
        return [None] * len(values)

    multiplier = 2.0 / (period + 1)
    ema_list: list[float | None] = [None] * (period - 1)

    # Initial SMA
    sma = sum(values[:period]) / period
    ema_list.append(sma)

    current_ema = sma
    for val in values[period:]:
        current_ema = (val - current_ema) * multiplier + current_ema
        ema_list.append(round(current_ema, 2))

    return ema_list


def detect_trendlines(candles: list[Candle], swings: list[SwingPoint]) -> list[Trendline]:
    """Detect validated support and resistance trendlines connecting 2 or more swing pivots."""
    if len(candles) < 20 or len(swings) < 4:
        return []

    latest_bar = candles[-1]
    latest_close = latest_bar.close
    latest_idx = len(candles) - 1

    lows = [s for s in swings if s.kind == "low"]
    highs = [s for s in swings if s.kind == "high"]

    found: list[Trendline] = []

    # 1. Ascending & Horizontal Support Trendlines (connecting swing lows)
    for i in range(len(lows)):
        for j in range(i + 1, len(lows)):
            p1, p2 = lows[i], lows[j]
            dx = p2.index - p1.index
            if dx < 6:  # Points too close are noise
                continue

            dy = p2.price - p1.price
            slope = dy / dx

            # Support trendline should not slope steeply down (> -0.5% per 10 bars)
            max_drop = p1.price * 0.0005
            if slope < -max_drop:
                continue

            # Check touches and violations
            touches = 2
            touch_times = [p1.time, p2.time]
            broken = False

            # Check all bars between and after p2
            for k in range(p1.index, len(candles)):
                line_price = p1.price + slope * (k - p1.index)
                bar = candles[k]

                # Significant close below invalidates support
                if bar.close < line_price * (1.0 - TOUCH_TOLERANCE_PCT * 1.5):
                    broken = True
                    break

                # Count touches at swings or wicks
                if k != p1.index and k != p2.index and abs(bar.low - line_price) <= line_price * TOUCH_TOLERANCE_PCT:
                    touches += 1
                    touch_times.append(bar.time)

            if broken or touches < MIN_TOUCHES:
                continue

            projected = p1.price + slope * (latest_idx - p1.index)
            dist_pct = round(100 * (projected / latest_close - 1), 3)

            # Only keep trendlines reasonably near the market (within 5%)
            if abs(dist_pct) > 5.0:
                continue

            status = "approaching" if abs(dist_pct) < 0.6 else "holding"
            direction_name = "Ascending" if slope > 0.01 else "Flat"
            label = f"{direction_name} Support ({touches} touches)"

            found.append(Trendline(
                id=f"tl_sup_{p1.index}_{p2.index}",
                kind="support",
                start_time=p1.time,
                start_price=round(p1.price, 2),
                end_time=p2.time,
                end_price=round(p2.price, 2),
                current_price=round(projected, 2),
                slope=round(slope, 4),
                touches=touches,
                touch_times=touch_times,
                distance_pct=dist_pct,
                status=status,
                label=label,
            ))

    # 2. Descending & Horizontal Resistance Trendlines (connecting swing highs)
    for i in range(len(highs)):
        for j in range(i + 1, len(highs)):
            p1, p2 = highs[i], highs[j]
            dx = p2.index - p1.index
            if dx < 6:
                continue

            dy = p2.price - p1.price
            slope = dy / dx

            # Resistance trendline should not slope steeply up
            max_rise = p1.price * 0.0005
            if slope > max_rise:
                continue

            touches = 2
            touch_times = [p1.time, p2.time]
            broken = False

            for k in range(p1.index, len(candles)):
                line_price = p1.price + slope * (k - p1.index)
                bar = candles[k]

                # Close above line invalidates resistance
                if bar.close > line_price * (1.0 + TOUCH_TOLERANCE_PCT * 1.5):
                    broken = True
                    break

                if k != p1.index and k != p2.index and abs(bar.high - line_price) <= line_price * TOUCH_TOLERANCE_PCT:
                    touches += 1
                    touch_times.append(bar.time)

            if broken or touches < MIN_TOUCHES:
                continue

            projected = p1.price + slope * (latest_idx - p1.index)
            dist_pct = round(100 * (projected / latest_close - 1), 3)

            if abs(dist_pct) > 5.0:
                continue

            status = "approaching" if abs(dist_pct) < 0.6 else "holding"
            direction_name = "Descending" if slope < -0.01 else "Flat"
            label = f"{direction_name} Resistance ({touches} touches)"

            found.append(Trendline(
                id=f"tl_res_{p1.index}_{p2.index}",
                kind="resistance",
                start_time=p1.time,
                start_price=round(p1.price, 2),
                end_time=p2.time,
                end_price=round(p2.price, 2),
                current_price=round(projected, 2),
                slope=round(slope, 4),
                touches=touches,
                touch_times=touch_times,
                distance_pct=dist_pct,
                status=status,
                label=label,
            ))

    # Sort by touch count descending and proximity to price
    found.sort(key=lambda t: (-t.touches, abs(t.distance_pct)))

    # Deduplicate lines that are almost identical
    unique_lines: list[Trendline] = []
    for t in found:
        duplicate = any(
            u.kind == t.kind and abs(u.current_price - t.current_price) / t.current_price < 0.003
            for u in unique_lines
        )
        if not duplicate:
            unique_lines.append(t)
        if len(unique_lines) >= MAX_TRENDLINES:
            break

    return unique_lines


def detect_horizontal_clusters(candles: list[Candle], swings: list[SwingPoint]) -> list[HorizontalCluster]:
    """Cluster multiple swing highs and lows into horizontal support/resistance cross-levels."""
    if not candles or len(swings) < 3:
        return []

    latest_close = candles[-1].close
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    clusters: list[HorizontalCluster] = []

    # 1. Resistance clusters from swing highs
    high_groups: list[list[SwingPoint]] = []
    for h in highs:
        matched = False
        for g in high_groups:
            avg_price = sum(p.price for p in g) / len(g)
            if abs(h.price - avg_price) / avg_price <= CLUSTER_TOLERANCE_PCT:
                g.append(h)
                matched = True
                break
        if not matched:
            high_groups.append([h])

    for g in high_groups:
        if len(g) >= 2:
            avg = sum(p.price for p in g) / len(g)
            dist_pct = round(100 * (avg / latest_close - 1), 3)
            # Only near price
            if abs(dist_pct) <= 6.0:
                name = "Triple Top" if len(g) >= 3 else "Double Top"
                clusters.append(HorizontalCluster(
                    id=f"cluster_res_{round(avg)}",
                    price=round(avg, 1),
                    kind="resistance",
                    touches=len(g),
                    touch_times=[p.time for p in g],
                    distance_pct=dist_pct,
                    status="approaching" if abs(dist_pct) < 0.5 else "holding",
                    label=f"{name} Resistance ({len(g)} swings)",
                ))

    # 2. Support clusters from swing lows
    low_groups: list[list[SwingPoint]] = []
    for low_pt in lows:
        matched = False
        for g in low_groups:
            avg_price = sum(p.price for p in g) / len(g)
            if abs(low_pt.price - avg_price) / avg_price <= CLUSTER_TOLERANCE_PCT:
                g.append(low_pt)
                matched = True
                break
        if not matched:
            low_groups.append([low_pt])

    for g in low_groups:
        if len(g) >= 2:
            avg = sum(p.price for p in g) / len(g)
            dist_pct = round(100 * (avg / latest_close - 1), 3)
            if abs(dist_pct) <= 6.0:
                name = "Triple Bottom" if len(g) >= 3 else "Double Bottom"
                clusters.append(HorizontalCluster(
                    id=f"cluster_sup_{round(avg)}",
                    price=round(avg, 1),
                    kind="support",
                    touches=len(g),
                    touch_times=[p.time for p in g],
                    distance_pct=dist_pct,
                    status="approaching" if abs(dist_pct) < 0.5 else "holding",
                    label=f"{name} Support ({len(g)} swings)",
                ))

    clusters.sort(key=lambda c: (-c.touches, abs(c.distance_pct)))
    return clusters[:MAX_HORIZONTAL_LEVELS]


def analyze_trendlines(candles: list[Candle]) -> dict[str, Any]:
    """Run full trendline and multi-swing level analytics on given candles."""
    if len(candles) < 20:
        return {
            "ready": False,
            "note": "Insufficient candles for trendline analysis",
            "trendlines": [],
            "horizontal_clusters": [],
            "ema_20": None,
            "ema_50": None,
        }

    closes = [c.close for c in candles]
    swings = find_swing_points(candles)
    trendlines = detect_trendlines(candles, swings)
    clusters = detect_horizontal_clusters(candles, swings)

    ema_20_series = calculate_ema(closes, 20)
    ema_50_series = calculate_ema(closes, 50)

    latest_ema_20 = ema_20_series[-1] if ema_20_series else None
    latest_ema_50 = ema_50_series[-1] if ema_50_series else None

    return {
        "ready": True,
        "symbol": "",
        "bars": len(candles),
        "latest_close": round(candles[-1].close, 2),
        "trendlines": [t.to_dict() for t in trendlines],
        "horizontal_clusters": [c.to_dict() for c in clusters],
        "ema_20": latest_ema_20,
        "ema_50": latest_ema_50,
    }
