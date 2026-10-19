"""The directional view: which way each timeframe leans, how far price can travel by when, and
how often that has actually worked out.

Three questions, kept apart on purpose, because mixing them is how a dashboard starts lying:

  1. WHERE ARE WE?      Trend and structure on 15m, 1h and 1d, read the same way on each, so
                        "flat on 15m, up on 1h, down on 1d" is visible instead of one line about
                        the 15-minute chart. The disagreement is the useful part: it is the
                        difference between a scalp and a position.

  2. HOW FAR CAN IT GO? Arithmetic, not opinion. Volatility scales with the square root of time,
                        so one number (realised vol) gives the expected move over 15 minutes, an
                        hour and a day, and the band around it. For a day the options market has
                        its own answer (ATM IV) and both are shown — when they disagree, that gap
                        is itself a trade.

  3. WHICH WAY?         The honest answer at these horizons starts at a coin. A driftless market
                        is 50/50 and most of crypto is close to it, so the number shown is never
                        just asserted: it is counted over the stored bars, beside the base rate
                        that needed no reading of the market at all. Below MIN_SAMPLE it refuses
                        to show a percentage and says how many it has.

The model is used only where it has been shown to have skill (`ModelCard.skill`), which at the
24-hour direction it currently does not — so it stays out of the direction number rather than
dressing up a coin flip. See CLAUDE.md on why.

Pure: bars in, numbers out. No network, no clock, no database handle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from tradebuddy import provenance as prov
from tradebuddy import structure as st
from tradebuddy.delta import Candle
from tradebuddy.strategies.indicators import ema

BAR_SECONDS = 900  # everything is built from stored 15-minute bars
HOUR = 3600.0

# One row per timeframe on the page. `factor` folds the stored 15m bars into it.
TIMEFRAMES = (
    {"interval": "15m", "factor": 1, "label": "Short term", "horizon_hours": 1.0, "sense": "a scalp"},
    {"interval": "1h", "factor": 4, "label": "Medium term", "horizon_hours": 24.0, "sense": "a day trade"},
    {"interval": "1d", "factor": 96, "label": "Long term", "horizon_hours": 168.0, "sense": "a position"},
)
# How far ahead the probabilities are asked for.
HORIZONS = (
    {"hours": 0.25, "label": "Next 15 min"},
    {"hours": 1.0, "label": "Next hour"},
    {"hours": 24.0, "label": "Next day"},
)
# Moves a person actually thinks in, rather than multiples of sigma.
MOVE_STEPS = (0.25, 0.5, 1.0, 2.0, 3.0)

MIN_SAMPLE = 200  # fewer counted outcomes than this and no percentage is shown
MIN_BARS = {"15m": 220, "1h": 220, "1d": 60}  # enough for the EMA stack to mean anything
TREND_FLAT_PCT = 0.05  # EMA separation under this is not a trend, it is noise


# -- the arithmetic ------------------------------------------------------------------


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def sigma_over(rv_annual: float, hours: float) -> float:
    """Volatility over `hours`, as a fraction of price. Square root of time."""
    return rv_annual * math.sqrt(hours / (365 * 24))


def expected_abs_move(sigma: float) -> float:
    """E|X| for a zero-mean normal. The typical move, not the biggest one."""
    return sigma * math.sqrt(2 / math.pi)


def p_touch(distance: float, sigma: float) -> float:
    """Chance price trades `distance` ABOVE (or, separately, below) at some point before the horizon.

    One side, not both. Reflection: for a driftless walk P(max >= a) = 2 P(end >= a). The up and
    down numbers are each this and must not be added — a path can touch both, so the sum runs over
    1. `p_touch_either` is the combined one.

    Touching, not closing: a stop does not care where the bar finished. This is the continuous
    figure, which is the right one here because a bar's high and low already carry what happened
    inside it (a simulation that only looks at closes reads lower, and converges up to this as it
    looks more often).
    """
    if sigma <= 0 or distance <= 0:
        return 0.0
    return min(1.0, 2 * (1 - norm_cdf(distance / sigma)))


def p_touch_either(distance: float, sigma: float, terms: int = 40) -> float:
    """Chance price trades `distance` away on EITHER side before the horizon.

    Not the sum of the two one-sided numbers: a path that swings both ways is counted once. The
    reflection principle applied to two barriers gives an alternating series for staying inside
    the band; this is one minus that."""
    if sigma <= 0 or distance <= 0:
        return 0.0
    z = distance / sigma
    stay = sum((-1) ** k * (norm_cdf((2 * k + 1) * z) - norm_cdf((2 * k - 1) * z)) for k in range(-terms, terms + 1))
    return min(1.0, max(0.0, 1 - stay))


def p_stop_first(stop_pct: float, target_pct: float) -> float | None:
    """Of the trades that reach one of the two levels, the share that reach the stop first.

    For a driftless walk with two barriers this is target / (stop + target). Volatility cancels:
    only the ratio matters, so it is the same answer in a quiet market and a fast one. It is the
    number that decides whether a trade is worth taking and the one almost nobody computes — a 1:3
    reward-to-risk is a 75% chance of being stopped out, and it has to be, or it would be free money.

    Conditional on one of them being hit. Over a fixed horizon some trades reach neither; see
    `p_neither`, which is where a time-based exit actually lands.
    """
    if stop_pct <= 0 or target_pct <= 0:
        return None
    return target_pct / (stop_pct + target_pct)


def p_neither(stop_pct: float, target_pct: float, sigma: float) -> float:
    """Chance neither level is reached before the horizon is up — the trade times out.

    Bounded below by the chance of staying inside the nearer barrier on both sides, which is the
    honest approximation here: the exact two-sided, asymmetric, finite-horizon figure needs a
    series that adds precision no one reading this page would use."""
    near = min(stop_pct, target_pct) / 100
    return max(0.0, 1 - p_touch_either(near, sigma))


# -- where are we ---------------------------------------------------------------------


@dataclass
class Timeframe:
    interval: str
    label: str
    sense: str
    bars: int
    enough: bool
    trend: str = "unknown"  # "up" | "weak up" | "flat" | "weak down" | "down" | "unknown"
    direction: int = 0  # -1, 0, +1 — for the alignment tally
    price: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    slope_pct: float | None = None  # EMA20 over the last 10 bars, as a %
    rv: float | None = None  # annualised realised volatility on this timeframe
    structure: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__ | {}


def read_timeframe(candles: list[Candle], spec: dict[str, Any]) -> Timeframe:
    """Trend and structure on one timeframe, read the same way on every one of them."""
    interval = spec["interval"]
    need = MIN_BARS[interval]
    tf = Timeframe(interval=interval, label=spec["label"], sense=spec["sense"], bars=len(candles), enough=len(candles) >= need)
    if len(candles) < 30:
        tf.note = f"needs {need} bars, has {len(candles)}"
        return tf

    closes = [c.close for c in candles]
    price = closes[-1]
    e20, e50, e200 = (ema(closes, n) for n in (20, 50, 200))
    tf.price = round(price, 2)
    tf.ema20 = round(e20[-1], 2) if e20 else None
    tf.ema50 = round(e50[-1], 2) if e50 else None
    tf.ema200 = round(e200[-1], 2) if e200 else None
    if len(e20) > 10:
        tf.slope_pct = round(100 * (e20[-1] / e20[-11] - 1), 3)
    tf.rv = st.realised_vol(closes, min(96, len(closes) - 1), BAR_SECONDS * spec["factor"])
    tf.structure = st.read(candles).to_dict()

    if not tf.ema50:
        tf.note = f"needs {need} bars for the full stack, has {len(candles)}"
        return tf

    # A trend is price on one side of the mid EMA with the fast EMA agreeing. "Strong" adds the
    # slow EMA and the structure; without EMA200 the reading still works, it is just less sure.
    gap = 100 * (price / tf.ema50 - 1)
    bias = tf.structure.get("bias")
    if abs(gap) < TREND_FLAT_PCT:
        tf.trend, tf.direction = "flat", 0
    elif gap > 0 and tf.ema20 > tf.ema50:
        strong = (tf.ema200 is None or tf.ema50 > tf.ema200) and bias == "bullish"
        tf.trend, tf.direction = ("up", 1) if strong else ("weak up", 1)
    elif gap < 0 and tf.ema20 < tf.ema50:
        strong = (tf.ema200 is None or tf.ema50 < tf.ema200) and bias == "bearish"
        tf.trend, tf.direction = ("down", -1) if strong else ("weak down", -1)
    else:
        tf.trend, tf.direction = "flat", 0
    if not tf.enough:
        tf.note = f"only {len(candles)} bars — read it as provisional"
    return tf


def alignment(frames: list[Timeframe]) -> dict[str, Any]:
    """Do the timeframes agree, and what does the disagreement mean in practice.

    This is the part a single-timeframe reading cannot give: flat on 15m with 1h up and 1d down is
    not "no trend", it is a bounce inside a downtrend, and it is a very different trade from the
    same 15m reading inside an uptrend."""
    known = [f for f in frames if f.trend != "unknown"]
    if not known:
        return {"verdict": "unknown", "score": 0, "advice": "Not enough history yet to read any timeframe."}
    score = sum(f.direction for f in known)
    short = next((f for f in known if f.interval == "15m"), None)
    long = next((f for f in known if f.interval == "1d"), None)
    parts = ", ".join(f"{f.interval} {f.trend}" for f in known)

    ups = sum(1 for f in known if f.direction > 0)
    downs = sum(1 for f in known if f.direction < 0)

    if not ups and not downs:
        verdict = "all flat"
        advice = "Nothing is trending on any timeframe. This is a range: trade the edges, not the middle."
    elif ups and downs:
        # Genuine conflict. Which two disagree decides what it means, so say it rather than
        # averaging it away into "mixed" — a 15m bounce inside a 1d downtrend is a specific trade.
        if long is not None and short is not None and long.direction and short.direction == -long.direction:
            verdict = "short term against long term"
            advice = (
                f"The {short.interval} is {short.trend} inside a {long.interval} {long.trend}. That is a counter-trend "
                f"bounce: workable as a scalp, poor as a position, and the target should be the nearest level, not a big move."
            )
        else:
            verdict = "conflicting"
            advice = f"The timeframes point opposite ways ({parts}). Either take the shorter one with a tight stop, or wait."
    elif (ups or downs) == len(known):
        side = "up" if ups else "down"
        verdict = f"aligned {side}"
        advice = (
            f"Every timeframe is {side}. Trades with the trend have the most behind them; "
            f"counter-trend entries here are fighting all three."
        )
    else:
        # Some lean, none oppose. Not a conflict — a partial trend, which is the common case and
        # deserves its own name rather than being filed under "mixed".
        side = "up" if ups else "down"
        flat = [f.interval for f in known if not f.direction]
        verdict = f"leaning {side}"
        advice = (
            f"{parts}. Nothing argues the other way, but {', '.join(flat)} has not joined in — "
            f"workable {side}, with the stop under the nearest level rather than a wide one."
        )
    return {"verdict": verdict, "score": score, "frames": parts, "advice": advice}


# -- how far can it go ----------------------------------------------------------------


def horizon_view(hours: float, label: str, price: float, rv: float, implied: dict[str, Any] | None = None) -> dict[str, Any]:
    """Expected move and the chance of travelling various distances, over one horizon."""
    sigma = sigma_over(rv, hours)
    typical = expected_abs_move(sigma)
    return {
        "hours": hours,
        "label": label,
        "source": prov.MATH,
        "sigma_pct": round(100 * sigma, 3),
        "expected_move_pct": round(100 * typical, 3),
        "expected_move_usd": round(price * typical, 2),
        # Two out of three bars land inside the first band, nineteen out of twenty inside the second.
        "band_68": [round(price * (1 - sigma), 2), round(price * (1 + sigma), 2)],
        "band_95": [round(price * (1 - 2 * sigma), 2), round(price * (1 + 2 * sigma), 2)],
        # Per side, and the combined figure. The two sides are each the same number by symmetry —
        # a driftless walk has no preference — and they are deliberately not added.
        "touch": [
            {
                "move_pct": step,
                "probability": round(p_touch(step / 100, sigma), 4),
                "either": round(p_touch_either(step / 100, sigma), 4),
                "up": round(price * (1 + step / 100), 2),
                "down": round(price * (1 - step / 100), 2),
            }
            for step in MOVE_STEPS
        ],
        "implied": implied,
    }


def implied_for(hours: float, options: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the options market charges for the same horizon, scaled from ATM IV.

    Worth showing next to the realised number: realised vol is what the market just did, implied is
    what it is being paid to insure against. A big gap either way is the only free information on
    the page."""
    if not options:
        return None
    iv = (options.get("day") or {}).get("atm_iv")
    if not iv:
        return None
    sigma = sigma_over(float(iv), hours)
    return {"source": prov.IMPLIED, "atm_iv": round(float(iv), 4), "sigma_pct": round(100 * sigma, 3),
            "expected_move_pct": round(100 * expected_abs_move(sigma), 3)}


# -- which way, counted ----------------------------------------------------------------


def _forward_returns(candles: list[Candle], ahead: int) -> list[float | None]:
    """Return from each bar's close to the close `ahead` bars later. None where unknown."""
    closes = [c.close for c in candles]
    return [
        (closes[i + ahead] / closes[i] - 1) if i + ahead < len(closes) and closes[i] > 0 else None
        for i in range(len(closes))
    ]


def _share_up(values: list[float]) -> float:
    return sum(1 for v in values if v > 0) / len(values) if values else 0.0


def counted_direction(candles: list[Candle], hours: float, bias: str | None, lookback: int = st.DEFAULT_LOOKBACK) -> dict[str, Any]:
    """How often price was higher `hours` later — over everything, and over bars in this structure.

    Both, never one. A conditional hit rate on its own cannot tell an edge from a market that
    simply went up all year; the unconditional rate over the same bars is what it has to beat."""
    ahead = max(1, round(hours * HOUR / BAR_SECONDS))
    forward = _forward_returns(candles, ahead)
    settled = [r for r in forward if r is not None]
    out: dict[str, Any] = {
        "horizon_hours": hours,
        "sample": len(settled),
        "baseline": round(_share_up(settled), 4) if len(settled) >= MIN_SAMPLE else None,
        "conditional": None,
        "conditional_sample": 0,
        "edge": None,
        "bias": bias,
    }
    if len(settled) < MIN_SAMPLE:
        out["note"] = f"{len(settled)} outcomes stored — needs {MIN_SAMPLE}. Run `backfill` to count from a year of history."
        return out
    if not bias or bias == "range":
        return out
    series = st.bias_series(candles, lookback)
    matched = [r for r, b in zip(forward, series, strict=False) if r is not None and b == bias]
    out["conditional_sample"] = len(matched)
    if len(matched) >= MIN_SAMPLE:
        out["conditional"] = round(_share_up(matched), 4)
        out["edge"] = round(out["conditional"] - out["baseline"], 4)
    else:
        out["note"] = f"only {len(matched)} bars in this structure — needs {MIN_SAMPLE}"
    return out


def build(
    symbol: str, candles: list[Candle], options: dict[str, Any] | None = None,
    stop_pct: float = 1.0, target_pct: float = 2.0,
) -> dict[str, Any]:
    """The whole view for one symbol, from stored bars. Pure, so the engine, the dashboard and a
    strategy on a worker all read the identical reading rather than three versions of it."""
    if not candles:
        return {
            "symbol": symbol, "bars": 0, "ready": False,
            "hint": "No bars stored yet. The engine writes one every 15 minutes; "
                    "`docker compose run --rm analyst backfill --days 365` fills in the past.",
        }
    price = candles[-1].close
    frames, counted, patterns = [], [], {}
    for spec in TIMEFRAMES:
        folded = st.resample(candles, spec["factor"])
        frame = read_timeframe(folded, spec)
        frames.append(frame)
        # Counted on the 15-minute series, which is the one with enough outcomes to count.
        counted.append(counted_direction(candles, spec["horizon_hours"], frame.structure.get("bias")))
        patterns[spec["interval"]] = pattern_record(folded if spec["factor"] > 1 else candles, spec["horizon_hours"])

    # One volatility for every horizon: the last day of 15-minute bars, which is what the market is
    # doing now rather than what it averaged over a week.
    rv = st.realised_vol([c.close for c in candles], min(96, len(candles) - 1), BAR_SECONDS) or 0.0
    horizons = [horizon_view(h["hours"], h["label"], price, rv, implied_for(h["hours"], options)) for h in HORIZONS]
    view = {
        "symbol": symbol,
        "ready": True,
        "price": price,
        "bar_time": candles[-1].time,
        "bars": len(candles),
        "days": round(len(candles) * BAR_SECONDS / 86_400, 1),
        "realised_vol": round(rv, 4),
        "timeframes": [f.to_dict() for f in frames],
        "alignment": alignment(frames),
        "horizons": horizons,
        # The one place a direction per timescale is decided. Everything else renders this.
        "summary": summarise(frames, horizons, counted),
        "direction": counted,
        "patterns": patterns,
        # The reward-to-risk the Trader would actually use, and what the arithmetic says about it.
        # Volatility cancels: only the ratio of the two distances matters.
        "trade_odds": {
            "stop_pct": stop_pct, "target_pct": target_pct,
            "p_stop_first": round(p_stop_first(stop_pct, target_pct) or 0, 4),
            "reward_risk": round(target_pct / stop_pct, 2) if stop_pct else None,
        },
        "min_sample": MIN_SAMPLE,
        "sources": prov.legend(),
    }
    # The same figures in sentences, from rules. Always present, never costs a token, and cannot
    # drift from the numbers above it. TB-AI's reading is separate and badged.
    view["narrative"] = narrate(view)
    return view


def summarise(frames: list[Timeframe], horizons: list[dict[str, Any]], counted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per timescale: which way, how far it could go, and how likely that is.

    The page had the three answers in three different cards and let the reader join them up, which
    is how it ended up saying "up" in one place and "down" in another. There is one direction per
    timescale and this is where it is decided; everything else on the page either renders this or
    says why it cannot.

    Direction comes from trend and structure — what the market is doing — never from the forecast
    model, which has been measured to have no skill at calling 24-hour direction. "How likely" is
    counted over the stored bars and carries the baseline it has to beat; where there are too few
    outcomes it is None and the row says so rather than showing a number.
    """
    by_hours = {h["hours"]: h for h in horizons}
    by_horizon = {c["horizon_hours"]: c for c in counted}
    scales = [("Short", "a scalp"), ("Medium", "a day trade"), ("Long", "a position")]
    out = []
    for frame, spec, (scale, _) in zip(frames, TIMEFRAMES, scales, strict=False):
        hours = spec["horizon_hours"]
        # The distance is quoted over a horizon that exists on the page; a week has no card of its
        # own, so the long row borrows the day's and says it is per day.
        horizon = by_hours.get(hours) or by_hours.get(24.0) or (horizons[-1] if horizons else {})
        row = by_horizon.get(hours) or {}
        probability = row.get("conditional")
        baseline = row.get("baseline")
        # A probability is about a side. "Higher 46% of the time" is the down case at 54%.
        if probability is not None and frame.direction < 0:
            probability, baseline = 1 - probability, (1 - baseline if baseline is not None else None)
        out.append({
            "scale": scale,
            "interval": frame.interval,
            "label": frame.label,
            "good_for": frame.sense,
            "direction": frame.direction,
            "trend": frame.trend,
            "horizon_hours": hours,
            "horizon_label": horizon.get("label", ""),
            "move_pct": horizon.get("expected_move_pct"),
            "move_usd": horizon.get("expected_move_usd"),
            "band": horizon.get("band_68"),
            "probability": probability,
            "baseline": baseline,
            # Three different kinds of claim in one row, and they are not equally dependable: which
            # way is counted, how far is arithmetic. Saying so is the difference between a reading
            # and a number.
            "direction_source": prov.COUNTED,
            "move_source": prov.MATH,
            "sample": row.get("conditional_sample") or row.get("sample") or 0,
            "edge": None if probability is None or baseline is None else round(probability - baseline, 4),
            "note": row.get("note", "") or frame.note,
        })
    return out


def pattern_edge(view: dict[str, Any], interval: str, kind: str, direction: str) -> dict[str, Any] | None:
    """How often this exact break has gone on the way it broke, against how often any bar did.

    The closest counted answer to the question a breakout trade actually asks. The structure-bias
    rate in `counted_edge` is a weaker proxy — it describes the market the break happened in, not
    the break — so this is what a break should be judged on when the history has enough of them."""
    for row in view.get("patterns", {}).get(interval) or []:
        if row["kind"] == kind and row["direction"] == direction and row.get("followed_through") is not None:
            return {
                "edge": row["edge"], "conditional": row["followed_through"], "baseline": row["baseline"],
                "sample": row["count"], "source": f"{kind} {direction} on {interval}",
            }
    return None


def counted_edge(view: dict[str, Any], side: str, hours: float) -> dict[str, Any] | None:
    """How far the counted odds favour `side` over the baseline at this horizon, or None when
    there is not enough history to say. The sign is flipped for a short: "higher 46% of the time"
    is a 4-point edge to the downside, not a 4-point deficit."""
    row = next((d for d in view.get("direction") or [] if d["horizon_hours"] == hours), None)
    if not row or row.get("conditional") is None or row.get("baseline") is None:
        return None
    edge = row["conditional"] - row["baseline"]
    return {
        "edge": round(edge if side == "buy" else -edge, 4),
        "conditional": row["conditional"], "baseline": row["baseline"], "sample": row["conditional_sample"],
    }


def narrate(view: dict[str, Any]) -> dict[str, str]:
    """The same numbers in sentences. Rules only — no model and no AI writes this.

    It is here because a page of percentages is not a reading: the point of "flat on 15m, up on 1h,
    down on 1d" is what it means for the next trade, and that has to be said out loud. Deterministic,
    so the words can never drift from the figures above them, and free, so it is always there even
    when TB-AI is off or rate-limited. TB-AI's own commentary sits beside this, badged, and may
    disagree — which is useful; one is arithmetic, the other is a reading.
    """
    if not view.get("ready"):
        return {"headline": "Not enough stored history yet.", "structure": "", "move": "", "odds": "", "plan": ""}

    frames = {f["interval"]: f for f in view["timeframes"]}
    align = view["alignment"]
    price = view["price"]

    def trend_words(f: dict[str, Any]) -> str:
        if f["trend"] == "unknown":
            return f"{f['interval']} not readable yet"
        return f"{f['interval']} {f['trend']}"

    headline = f"{', '.join(trend_words(frames[i]) for i in ('15m', '1h', '1d') if i in frames)} — {align['verdict']}."

    # structure: the levels that matter right now
    short = frames.get("15m", {})
    sdata = short.get("structure") or {}
    hi, lo = sdata.get("swing_high"), sdata.get("swing_low")
    bits = []
    if hi and lo:
        bits.append(f"On 15m price sits {100 * (price - lo) / (hi - lo):.0f}% of the way up a {lo:,.0f} to {hi:,.0f} range.")
        bits.append(f"A close above {hi:,.0f} or below {lo:,.0f} is the break that changes the reading.")
    last = (sdata.get("breaks") or [])[-1:]
    if last:
        b = last[0]
        word = "change of character" if b["kind"] == "choch" else "break of structure"
        bits.append(
            f"The last one was a {b['direction']} {word} through {b['level']:,.0f}"
            + (f", on a bar {b['displacement']:.1f}x the usual size" if b["displacement"] >= 1.5 else "")
            + ("; volume was heavy." if b["volume_ratio"] >= 1.5 else ".")
        )
    if sdata.get("squeeze"):
        bits.append("The range is compressed — breakouts tend to come out of this, in either direction.")
    structure_text = " ".join(bits)

    # how far it can go
    moves = []
    for h in view["horizons"]:
        imp = h.get("implied")
        line = f"{h['label'].lower()}: about ±{h['expected_move_pct']:.2f}% (±{h['expected_move_usd']:,.0f})"
        if imp:
            line += f", options price ±{imp['expected_move_pct']:.2f}%"
        moves.append(line)
    move_text = "Typical move " + "; ".join(moves) + "."
    day = next((h for h in view["horizons"] if h["hours"] == 24.0), None)
    if day and day.get("implied"):
        ratio = day["implied"]["sigma_pct"] / day["sigma_pct"] if day["sigma_pct"] else 1
        if ratio >= 1.15:
            move_text += " Options are charging more than the market has been moving — selling premium is paid well, buying it is expensive."
        elif ratio <= 0.87:
            move_text += " Options are charging less than the market has been moving — buying premium is cheap relative to recent moves."

    # which way, counted
    odds = []
    for d in view["direction"]:
        label = f"{d['horizon_hours']:g}h"
        if d.get("conditional") is not None:
            odds.append(
                f"over {label}, price was higher {100 * d['conditional']:.0f}% of the time in this structure "
                f"({d['conditional_sample']:,} bars) against {100 * d['baseline']:.0f}% for any bar"
            )
        elif d.get("baseline") is not None:
            odds.append(f"over {label}, any bar was higher {100 * d['baseline']:.0f}% of the time ({d['sample']:,} counted)")
    odds_text = (
        "Counted on the stored bars: " + "; ".join(odds) + "."
        if odds else
        "There is not enough stored history to count direction yet — run backfill, and until then treat direction as a coin."
    )
    edges = [d for d in view["direction"] if d.get("edge") is not None]
    if edges:
        best = max(edges, key=lambda d: abs(d["edge"]))
        odds_text += (
            f" The structure moves the {best['horizon_hours']:g}h odds by {100 * best['edge']:+.1f} points — "
            + ("worth something, but not a lot." if abs(best["edge"]) < 0.05 else "a real difference.")
        )
    else:
        odds_text += " Nothing here beats the base rate by enough to call it an edge."

    # what to do with it
    to = view["trade_odds"]
    plan = align["advice"]
    if to.get("p_stop_first"):
        plan += (
            f" At the current {to['stop_pct']:g}% stop and {to['target_pct']:g}% target (1:{to['reward_risk']:g}), "
            f"{100 * to['p_stop_first']:.0f}% of trades that reach a level reach the stop first — that is the arithmetic of the "
            f"ratio, not a flaw in the setup. It pays if the win rate beats it."
        )
    return {"headline": headline, "structure": structure_text, "move": move_text, "odds": odds_text, "plan": plan}


def pattern_record(candles: list[Candle], hours: float, lookback: int = st.DEFAULT_LOOKBACK) -> list[dict[str, Any]]:
    """What each kind of break has actually led to, `hours` later.

    A flag with no follow-through rate is noise dressed as a signal. "BOS up" means something only
    once you know that of the 112 of them in the stored history, 41% went on higher — against 38%
    for any bar at all."""
    ahead = max(1, round(hours * HOUR / BAR_SECONDS))
    forward = _forward_returns(candles, ahead)
    settled = [r for r in forward if r is not None]
    if len(settled) < MIN_SAMPLE:
        return []
    baseline = _share_up(settled)
    groups: dict[tuple[str, str], list[float]] = {}
    for b in st.find_breaks(candles, lookback):
        r = forward[b.index] if b.index < len(forward) else None
        if r is not None:
            groups.setdefault((b.kind, b.direction), []).append(r)
    out = []
    for (kind, direction), rows in sorted(groups.items()):
        followed = _share_up(rows) if direction == "up" else 1 - _share_up(rows)
        base = baseline if direction == "up" else 1 - baseline
        out.append({
            "kind": kind, "direction": direction, "count": len(rows),
            "followed_through": round(followed, 4) if len(rows) >= 20 else None,
            "baseline": round(base, 4),
            "edge": round(followed - base, 4) if len(rows) >= 20 else None,
            "mean_move_pct": round(100 * sum(abs(r) for r in rows) / len(rows), 3),
            "enough": len(rows) >= 20,
        })
    return out
