"""Is something happening right now — and if it is, how big, and which way.

The Market page answers "where does price react" well and "is anything going on" not at all. The
volume spike lived as one line inside a findings list sorted by severity, which is where you find
it after you already knew to look. This is the reading that belongs above everything else: one
state word, the numbers it came from, and what the stored bars say usually follows.

THREE QUESTIONS, AND ONLY TWO OF THEM HAVE ANSWERS
--------------------------------------------------
IS IT HAPPENING?   Measured, now, off the bar that just closed: how much size traded against what
                   normally trades, how far the bar ranged against the ATR, how much of that range
                   the body kept, whether volatility is expanding, whether the coil before it had
                   been tightening, and whether the close took out a prior swing. All arithmetic
                   over the bars — nothing here is fitted.

HOW BIG?           Answerable, and honestly. `outlook.p_touch` is the chance price trades a given
                   distance before a horizon, and `expected_abs_move` the typical move over it.
                   Magnitude is a real question with a real answer: volatility is persistent, and
                   the arithmetic is checked against simulation in `tests/test_outlook.py`.

WHICH WAY?         Mostly NOT answerable, and this module says so in the one way that is honest:
                   by counting. Over 35,488 stored 15m bars price was higher an hour later 50.1%
                   of the time, four hours later 50.2%, a day later 50.4% — a coin. So direction
                   here is never asserted. It is COUNTED: of the bars that looked like this one,
                   how many resolved up, against the share of *all* bars that did, with the sample
                   behind both. Under `outlook.MIN_SAMPLE` no percentage is shown at all
                   (invariant 14b). A conditional rate without its baseline is how a market that
                   simply went up all year gets mistaken for a signal.

The asymmetry is the point and the page should carry it: a big move is detectable, its size is
estimable, its direction is usually not. A reading that offered all three with equal confidence
would be lying about the third.

Pure — candles in, numbers out, no clock and no network — like `structure.py`, `outlook.py` and
`verdict.py`, so the dashboard, a strategy and a backtest all get the same answer.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from app.analysis import outlook as ol
from app.analysis import provenance as prov
from app.analysis import structure as st
from app.market.delta import Candle
from app.market.indicators import atr

# How far back "normal" is measured. 96 fifteen-minute bars is a day: long enough that one busy
# hour does not become the baseline, short enough that last month's regime is not the yardstick.
NORMAL_WINDOW = 96
ATR_PERIOD = 14
# What `measure` looks at. The break scan is a rolling min/max over every bar it is handed, and it
# only ever reports a break on the LAST one — so handing it a year of history costs a year of work
# to answer a question about one bar. `which_way` gets the whole series; this does not.
MEASURE_BARS = 400
# Volatility expanding: the last few hours against the last day.
FAST_BARS = 8
SLOW_BARS = 96

# The thresholds that turn measurements into a word. Deliberately round numbers, not fitted ones —
# a sample of a few hundred bars can be made to "prove" any boundary, and these exist to label a
# reading a person is also shown in full, not to decide a trade.
BIG_VOLUME = 2.0  # this bar's volume against the window mean
BUSY_VOLUME = 1.3
BIG_RANGE = 1.5  # this bar's range against the ATR
WIDE_RANGE = 1.0
DECISIVE_BODY = 0.6  # the share of the range the body kept: a push, not a fight
EXPANDING_RV = 1.4  # fast realised vol against slow

# States, loudest first. `breakout` is the only one that needs a structure break in it.
BREAKOUT = "breakout"
EXPANDING = "expanding"
COILED = "coiled"
ACTIVE = "active"
QUIET = "quiet"

# How far ahead "what usually follows" looks, in hours. One bar is noise; a day is a different
# question. These are the horizons the Outlook card already uses.
FOLLOW_HOURS = (1.0, 4.0)
# Distances to price the magnitude question at, as a % of price.
MOVE_STEPS = (0.25, 0.5, 1.0, 2.0)
BAR_SECONDS = 900
HOUR = 3600


@dataclass(frozen=True)
class Measured:
    """What the bar that just closed actually did. Every field is arithmetic over the bars."""

    volume_ratio: float | None = None  # against the window mean
    volume_z: float | None = None  # in standard deviations of the window
    range_atr: float | None = None  # the bar's range in ATRs
    body_share: float | None = None  # |close - open| / range
    direction: int = 0  # +1 up bar, -1 down bar, 0 doji
    rv_fast: float | None = None  # annualised, last FAST_BARS
    rv_slow: float | None = None  # annualised, last SLOW_BARS
    rv_ratio: float | None = None
    squeeze: bool = False  # the coil was tightening into this
    broke: str = ""  # "" | "bos up" | "choch down" ... the break this bar made, if any
    break_level: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Pulse:
    ready: bool
    state: str
    headline: str  # one sentence, always filled — a quiet market is a reading too
    symbol: str = ""
    bar_time: int = 0
    price: float | None = None
    measured: Measured = field(default_factory=Measured)
    reasons: list[str] = field(default_factory=list)  # why this state, in the numbers
    size: dict[str, Any] = field(default_factory=dict)  # how big the move could be — real arithmetic
    way: list[dict[str, Any]] = field(default_factory=list)  # which way, COUNTED, with its baseline
    note: str = ""
    sources: dict[str, str] = field(
        default_factory=lambda: {"measured": prov.MATH, "size": prov.MATH, "way": prov.COUNTED}
    )

    @property
    def loud(self) -> bool:
        """Worth putting in front of someone who did not ask."""
        return self.state in (BREAKOUT, EXPANDING)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"loud": self.loud}


def _rv(closes: list[float], bars: int) -> float | None:
    return st.realised_vol(closes, min(bars, len(closes) - 1), BAR_SECONDS) if len(closes) > 2 else None


def measure(candles: list[Candle]) -> Measured:
    """The bar that just closed, against what normally happens. Pure."""
    if len(candles) < ATR_PERIOD + 2:
        return Measured()
    bar = candles[-1]
    closes = [c.close for c in candles]
    window = candles[-NORMAL_WINDOW - 1 : -1] or candles[:-1]

    volumes = [c.volume for c in window]
    mean_v = sum(volumes) / len(volumes) if volumes else 0.0
    sd_v = statistics.pstdev(volumes) if len(volumes) > 1 else 0.0
    atr_now = (atr([c.high for c in candles], [c.low for c in candles], closes, ATR_PERIOD) or [0.0])[-1]
    span = bar.high - bar.low

    fast, slow = _rv(closes, FAST_BARS), _rv(closes, SLOW_BARS)
    # A bar cannot break a level it set itself (`structure.find_breaks` handles that); this only
    # asks whether the break it reports is on the bar that just closed.
    broke, level = "", None
    for b in st.find_breaks(candles)[-3:]:
        if b.bar_time == bar.time:
            broke, level = f"{b.kind} {b.direction}", b.level

    return Measured(
        volume_ratio=round(bar.volume / mean_v, 3) if mean_v > 0 else None,
        volume_z=round((bar.volume - mean_v) / sd_v, 2) if sd_v > 0 else None,
        range_atr=round(span / atr_now, 3) if atr_now > 0 else None,
        body_share=round(abs(bar.close - bar.open) / span, 3) if span > 0 else None,
        direction=1 if bar.close > bar.open else -1 if bar.close < bar.open else 0,
        rv_fast=round(fast, 4) if fast else None,
        rv_slow=round(slow, 4) if slow else None,
        rv_ratio=round(fast / slow, 3) if fast and slow else None,
        squeeze=st.in_squeeze(closes[:-1]),  # the coil BEFORE this bar, not including it
        broke=broke,
        break_level=level,
    )


def classify(m: Measured) -> tuple[str, list[str]]:
    """A state word, and the numbers that earned it.

    The reasons matter more than the word. A label with its working shown can be disagreed with;
    a label on its own has to be trusted, and nothing on this page asks to be trusted."""
    why: list[str] = []
    vol, rng, body = m.volume_ratio, m.range_atr, m.body_share

    big_vol = vol is not None and vol >= BIG_VOLUME
    busy_vol = vol is not None and vol >= BUSY_VOLUME
    big_rng = rng is not None and rng >= BIG_RANGE
    wide_rng = rng is not None and rng >= WIDE_RANGE
    decisive = body is not None and body >= DECISIVE_BODY

    if vol is not None:
        why.append(f"volume {vol:.2f}x the {NORMAL_WINDOW}-bar average" + (f" ({m.volume_z:+.1f} sd)" if m.volume_z is not None else ""))
    if rng is not None:
        why.append(f"range {rng:.2f} ATR")
    if body is not None:
        why.append(f"body kept {body:.0%} of the range")
    if m.rv_ratio is not None:
        why.append(f"volatility {m.rv_ratio:.2f}x its daily level")

    if m.broke and (busy_vol or wide_rng):
        why.insert(0, f"closed through a prior swing ({m.broke})")
        return BREAKOUT, why
    if (big_vol and wide_rng) or (big_rng and busy_vol and decisive):
        return EXPANDING, why
    if m.squeeze and not wide_rng:
        why.insert(0, "the coil going in was tighter than usual")
        return COILED, why
    if busy_vol or wide_rng:
        return ACTIVE, why
    return QUIET, why


def how_big(candles: list[Candle], hours: float = 1.0) -> dict[str, Any]:
    """How far price could travel before `hours` is up. Real arithmetic, not a forecast of side.

    Volatility is persistent in a way direction is not — that is why this question has an answer
    and "which way" does not. The figures are `outlook`'s, not a second implementation of them."""
    closes = [c.close for c in candles]
    rv = _rv(closes, SLOW_BARS) or 0.0
    if rv <= 0 or not closes:
        return {"ready": False, "note": "not enough bars to measure volatility"}
    price = closes[-1]
    sigma = ol.sigma_over(rv, hours)
    return {
        "ready": True,
        "hours": hours,
        "source": prov.MATH,
        "realised_vol": round(rv, 4),
        "typical_move_pct": round(100 * ol.expected_abs_move(sigma), 3),
        "typical_move_usd": round(price * ol.expected_abs_move(sigma), 2),
        "band_68": [round(price * (1 - sigma), 2), round(price * (1 + sigma), 2)],
        "touch": [
            {
                "move_pct": step,
                "either_way": round(ol.p_touch_either(step / 100, sigma), 4),
                "one_way": round(ol.p_touch(step / 100, sigma), 4),
                "up": round(price * (1 + step / 100), 2),
                "down": round(price * (1 - step / 100), 2),
            }
            for step in MOVE_STEPS
        ],
    }


def worth_counting(m: Measured) -> bool:
    """Is this bar unusual enough that "bars like it" is a smaller set than "bars"?

    It has to be asked. The match below is "at least as busy and as wide as this one", which on an
    ORDINARY bar matches most of the history — 785 of 2996 on a bar at 0.45x volume — and returns
    the baseline wearing a conditional's clothes. A number that always equals the baseline is worse
    than no number: it reads as a measurement when it is a tautology."""
    return (m.volume_ratio is not None and m.volume_ratio >= BUSY_VOLUME) or (
        m.range_atr is not None and m.range_atr >= WIDE_RANGE
    )


def looks_like(m: Measured, other: Measured) -> bool:
    """Was that bar the same kind of bar as this one? Volume and range, both, same direction.

    Both because either alone is a different question: a wide quiet bar is a gap, a busy narrow
    one is a fight. Same direction because "what follows a big up bar" and "what follows a big
    down bar" are not one statistic, and averaging them is how an edge disappears."""
    if m.volume_ratio is None or other.volume_ratio is None:
        return False
    if m.range_atr is None or other.range_atr is None:
        return False
    return (
        other.volume_ratio >= min(m.volume_ratio, BIG_VOLUME)
        and other.range_atr >= min(m.range_atr, BIG_RANGE)
        and other.direction == m.direction
    )


def which_way(candles: list[Candle], m: Measured, hours: float) -> dict[str, Any]:
    """Of the bars that looked like this one, how many resolved up — against ALL bars.

    Never the conditional rate alone. The unconditional share over the same bars is what it has to
    beat, and on this market that share is a coin: anything close to it is a coin too, however
    confident the conditional number looks. Under `outlook.MIN_SAMPLE` no percentage is returned.
    """
    ahead = max(1, round(hours * HOUR / BAR_SECONDS))
    closes = [c.close for c in candles]
    out: dict[str, Any] = {
        "horizon_hours": hours, "source": prov.COUNTED, "min_sample": ol.MIN_SAMPLE,
        "baseline": None, "conditional": None, "edge": None, "sample": 0, "matched": 0, "note": "",
    }
    if m.volume_ratio is None or len(candles) < NORMAL_WINDOW + ahead + 2:
        out["note"] = "not enough stored bars to count anything yet"
        return out
    if not worth_counting(m):
        out["note"] = "this bar is not unusual — there is nothing here to count that is not just the market"
        return out

    forward = [
        (closes[i + ahead] / closes[i] - 1) if i + ahead < len(closes) and closes[i] > 0 else None
        for i in range(len(closes))
    ]
    settled = [r for r in forward if r is not None]
    out["sample"] = len(settled)
    if len(settled) < ol.MIN_SAMPLE:
        out["note"] = f"{len(settled)} settled outcomes — needs {ol.MIN_SAMPLE}. Run `backfill`."
        return out
    out["baseline"] = round(sum(1 for r in settled if r > 0) / len(settled), 4)

    # Rebuild the same measurements bar by bar. Done here rather than stored because it has to
    # apply to every bar already on disk, not only to the ones recorded after this shipped.
    matched: list[float] = []
    volumes = [c.volume for c in candles]
    ranges = [c.high - c.low for c in candles]
    atrs = atr([c.high for c in candles], [c.low for c in candles], closes, ATR_PERIOD) or []
    off = len(candles) - len(atrs)
    for i in range(NORMAL_WINDOW, len(candles) - ahead):
        if forward[i] is None or i - off < 0 or i - off >= len(atrs):
            continue
        a = atrs[i - off]
        win = volumes[i - NORMAL_WINDOW : i]
        mean_v = sum(win) / len(win) if win else 0.0
        if mean_v <= 0 or not a:
            continue
        bar = candles[i]
        past = Measured(
            volume_ratio=volumes[i] / mean_v, range_atr=ranges[i] / a,
            direction=1 if bar.close > bar.open else -1 if bar.close < bar.open else 0,
        )
        if looks_like(m, past):
            matched.append(forward[i])

    out["matched"] = len(matched)
    if len(matched) < ol.MIN_SAMPLE:
        out["note"] = (
            f"only {len(matched)} bars like this one in {len(settled)} stored — needs "
            f"{ol.MIN_SAMPLE} before a percentage means anything"
        )
        return out
    out["conditional"] = round(sum(1 for r in matched if r > 0) / len(matched), 4)
    out["edge"] = round(out["conditional"] - out["baseline"], 4)
    return out


def headline_for(state: str, m: Measured, symbol: str) -> str:
    """One sentence a person can read without the numbers underneath."""
    who = symbol or "this market"
    way = "up" if m.direction > 0 else "down" if m.direction < 0 else "sideways"
    if state == BREAKOUT:
        return f"{who} just closed through a level on heavy trade, {way}."
    if state == EXPANDING:
        return f"{who} is moving {way} on unusual size — the range is well past normal."
    if state == COILED:
        return f"{who} is coiled: the range has been tightening, which often precedes an expansion."
    if state == ACTIVE:
        return f"{who} is busier than usual, but not yet out of the ordinary."
    return f"{who} is quiet — volume and range are both around normal."


def read(symbol: str, candles: list[Candle], follow_hours: tuple[float, ...] = FOLLOW_HOURS) -> Pulse:
    """The whole reading for one symbol. Pure, and the only entry point anything else should use."""
    if len(candles) < NORMAL_WINDOW + ATR_PERIOD:
        return Pulse(
            ready=False, state=QUIET, symbol=symbol,
            headline=f"Not enough stored bars for {symbol or 'this market'} yet.",
            note=f"{len(candles)} bars — needs {NORMAL_WINDOW + ATR_PERIOD}. Run `backfill`.",
        )
    m = measure(candles[-MEASURE_BARS:])
    state, why = classify(m)
    return Pulse(
        ready=True,
        state=state,
        headline=headline_for(state, m, symbol),
        symbol=symbol,
        bar_time=candles[-1].time,
        price=candles[-1].close,
        measured=m,
        reasons=why,
        size=how_big(candles, follow_hours[0] if follow_hours else 1.0),
        way=[which_way(candles, m, h) for h in follow_hours],
    )
