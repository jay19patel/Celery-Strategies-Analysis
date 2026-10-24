"""Fill a database with a market that never happened, so the pages can be read before the real one arrives.

Everything on the Market and System pages is counted from stored history. On a fresh install there
is none, so every card correctly says "not enough yet" — which is honest and completely unhelpful
when you are trying to see whether the thing works.

This writes a plausible market instead: bars with trends, ranges and breakouts in them rather than
pure noise (noise would make every card say "no edge", which is true of noise and tells you nothing
about the page), a minute-by-minute options history, the analyst's readings, card verdicts with
most of them already marked, and process load.

Two rules, because fake market data in a system that places real orders is a genuine hazard:

  * it writes to its own database file by default (`data/demo.db`), not the one the engine trades from
  * pointed at a database that has ever had an order in it, it refuses unless forced

It never touches orders, trades or toggles. Nothing it writes is money; all of it is market and
analysis data, which is the part that needs history to be worth looking at. The one exception is
a `demo_data` marker in the settings table — a page showing a made-up market must say so, or
sooner or later someone reads a verdict's record off a market that never happened and believes it.
"""

# Every random number in this module invents a candle or a quote. None of it is a secret, and
# the seeds are fixed on purpose so the same demo market comes back twice.
# ruff: noqa: S311

from __future__ import annotations

import bisect
import math
import random
import time
from typing import Any

from tradebuddy import outlook, reading, verdict
from tradebuddy import structure as mkt
from tradebuddy.delta import Candle
from tradebuddy.store import Store

DEFAULT_PATH = "data/demo.db"
SYMBOLS = ("BTCUSD", "ETHUSD")
START = {"BTCUSD": 82_000.0, "ETHUSD": 3_100.0}
UNDERLYING = {"BTCUSD": "BTC", "ETHUSD": "ETH"}
BAR = 900


class Refused(RuntimeError):
    pass


def _regimes(rng: random.Random, bars: int) -> list[tuple[float, float]]:
    """A sequence of (drift per bar, volatility per bar) stretches.

    A market that is one regime for a year teaches the page nothing: the whole point of the trend,
    structure and base-rate cards is that they read differently in a trend than in a range, so the
    demo market has to contain both."""
    out: list[tuple[float, float]] = []
    while len(out) < bars:
        kind = rng.choices(["trend_up", "trend_down", "range", "squeeze"], weights=[3, 2, 4, 1])[0]
        span = rng.randint(200, 900)
        drift, vol = {
            "trend_up": (rng.uniform(0.00004, 0.00012), rng.uniform(0.0016, 0.0026)),
            "trend_down": (-rng.uniform(0.00004, 0.00012), rng.uniform(0.0018, 0.0030)),
            "range": (0.0, rng.uniform(0.0014, 0.0022)),
            "squeeze": (0.0, rng.uniform(0.0006, 0.0011)),
        }[kind]
        out += [(drift, vol)] * span
    return out[:bars]


def candles(symbol: str, bars: int, end: int, seed: int) -> list[Candle]:
    """A walk through several regimes, with outsized bars and rejection wicks in it.

    The big bars matter: a break of structure on an ordinary candle and one on a bar three times
    the usual size are different events, and the strategy and the pattern scoreboard both read
    that difference. A market with no large bars in it would never exercise either.

    The wicks matter for the same reason. Drawing them symmetrically — the obvious thing — produces
    a market with no hammers and no shooting stars in it at all, so the two shapes anyone looks for
    first were missing from the demo entirely. A rejection is a bar that travelled one way and
    closed back: a long wick on one side with the body at the other end."""
    rng = random.Random(seed)
    price = START.get(symbol, 1_000.0)
    plan = _regimes(rng, bars)
    start = (end - bars * BAR) // BAR * BAR
    out: list[Candle] = []
    for i, (drift, vol) in enumerate(plan):
        shock = rng.random() < 0.012
        step = rng.gauss(drift, vol * (3.2 if shock else 1.0))
        o = price
        price = max(1.0, price * (1 + step))
        volume = abs(rng.gauss(140, 40)) * (3.5 if shock else 1.0)
        top, bottom = max(o, price), min(o, price)
        upper = abs(rng.gauss(0, vol / 2.2))
        lower = abs(rng.gauss(0, vol / 2.2))
        if rng.random() < 0.05:
            # A rejection: price pushed one way, failed, and closed back. The body shrinks towards
            # the far end, which is exactly what makes a hammer a hammer.
            reach = abs(rng.gauss(0, vol * 2.2)) + vol
            if rng.random() < 0.5:
                lower, upper = reach, upper * 0.2
                price = max(1.0, bottom + (top - bottom) * 0.8) if top > bottom else price
            else:
                upper, lower = reach, lower * 0.2
                price = max(1.0, top - (top - bottom) * 0.8) if top > bottom else price
            top, bottom = max(o, price), min(o, price)
            volume *= 1.8  # someone was defending the level
        out.append(Candle(start + i * BAR, o, top * (1 + upper), bottom * (1 - lower), price, round(volume, 2)))
    return out


def options_rows(symbol: str, bars: list[Candle], minute_days: int = 2) -> list[dict[str, Any]]:
    """An options history whose IV follows what price actually did.

    Implied volatility that wandered independently of the bars would make the IV/RV tile and the
    term-structure verdict meaningless — they are both comparisons between the two.

    Dense where it is read and sparse where it is not, which is also what the real store keeps: a
    row a minute over the last `minute_days` (what the 24-hour ATM IV and put/call charts draw),
    and one per 15-minute bar before that (what a strategy reads back). A minute a row for three
    months would be two million rows nobody looks at.
    """
    rng = random.Random(hash(symbol) & 0xFFFF)
    closes = [c.close for c in bars]
    rows = []
    dense_from = len(bars) - minute_days * 96
    for i in range(len(bars)):
        window = closes[max(0, i - 96) : i + 1]
        rv = mkt.realised_vol(window, min(95, len(window) - 1), BAR) if len(window) > 8 else 0.4
        # Options normally charge a little more than the market has been doing, and the premium
        # over realised wanders.
        iv = max(0.08, (rv or 0.4) * rng.uniform(1.0, 1.35))
        spot = closes[i]
        pcr = max(0.3, min(2.2, 0.9 + 0.45 * math.sin(i / 260) + rng.gauss(0, 0.04)))
        for minute in range(0, BAR, 60) if i >= dense_from else (0,):
            ts = bars[i].time + minute
            rows.append({
                "underlying": UNDERLYING.get(symbol, symbol[:3]), "ts": ts, "spot": spot,
                "atm_iv": round(iv + rng.gauss(0, 0.004), 5),
                "near_atm_iv": round(iv * 1.02, 5), "skew_25d": round(rng.gauss(0.01, 0.02), 5),
                "pcr_oi": round(pcr, 4), "pcr_volume": round(pcr * rng.uniform(0.85, 1.2), 4),
                "call_oi": round(rng.uniform(4e3, 9e3), 1), "put_oi": round(rng.uniform(4e3, 9e3), 1),
                "oi_usd": round(rng.uniform(5e7, 2e8)), "turnover_usd": round(rng.uniform(5e6, 4e7)),
                "max_pain": round(spot * rng.uniform(0.985, 1.015), 1),
                "implied_move_pct": round(100 * iv * math.sqrt(1 / 365), 3),
            })
    return rows


def summary_for(symbol: str, bars: list[Candle], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """An options summary shaped like the real one, so the verdict cards have walls and a curve."""
    last = rows[-1] if rows else {"atm_iv": 0.4, "pcr_oi": 0.9}
    spot = bars[-1].close
    iv = float(last["atm_iv"])
    term = [
        {"label": "today", "hours": 8, "atm_iv": round(iv * 0.94, 4), "expiry": bars[-1].time + 8 * 3600},
        {"label": "tomorrow", "hours": 30, "atm_iv": round(iv, 4), "expiry": bars[-1].time + 30 * 3600},
        {"label": "next week", "hours": 170, "atm_iv": round(iv * 1.12, 4), "expiry": bars[-1].time + 170 * 3600},
    ]
    return {
        "spot": spot,
        "day": {
            "atm_iv": round(iv, 4), "skew_25d": float(last.get("skew_25d") or 0.01),
            "call_wall": round(spot * 1.013, 1), "put_wall": round(spot * 0.986, 1),
            "implied_move_pct": round(100 * iv * math.sqrt(1 / 365), 3), "label": "tomorrow", "hours": 30,
        },
        "nearest": {"max_pain": round(spot * 0.997, 1), "hours": 8},
        "term": term, "expiries": term, "chains": {},
        "pcr_oi": float(last.get("pcr_oi") or 0.9), "pcr_volume": float(last.get("pcr_volume") or 1.0),
        "oi_usd": float(last.get("oi_usd") or 1e8), "turnover_usd": float(last.get("turnover_usd") or 1e7),
    }


def _settle(store: Store, symbol: str, bars: list[Candle], now: float,
            stop_pct: float = 1.0, target_pct: float = 2.0, cost_pct: float = 0.14) -> int:
    """Mark every verdict whose horizon has passed, the way the engine does.

    Including the bracket: a directional claim is marked on what a trade from that bar would have
    done, not on where the close ended. Settling it any other way would leave the Scorecard's exit
    reasons and R multiples empty on a demo whose whole job is to show them."""
    from tradebuddy.insights import outcome

    by_time = {int(c.time): i for i, c in enumerate(bars)}
    marked = 0
    for row in store.unsettled_verdicts(now, limit=100_000):
        hours, bar_time = float(row["horizon_hours"]), int(row["bar_time"])
        actual = outcome(bars, bar_time, hours, 0.0)
        if actual is None:
            continue
        before = outcome(bars, bar_time - int(hours * 3600), hours, 0.0)
        prior = float(before["realized_vol"]) if before else float(actual["realized_vol"])

        claimed = naive = None
        index = by_time.get(bar_time)
        if index is not None and row["stance"] in ("bullish", "bearish"):
            ahead = max(1, round(hours * 3600 / BAR))
            side = "buy" if row["stance"] == "bullish" else "sell"
            claimed = reading.barrier_outcome(bars, index, side, stop_pct, target_pct, ahead, cost_pct)
            naive = reading.barrier_outcome(bars, index, "buy", stop_pct, target_pct, ahead, cost_pct)
            if claimed is None or naive is None:
                claimed = naive = None
        judged = verdict.settle(
            row | {"numbers": {}}, float(actual["ret"]), float(actual["realized_vol"]), prior,
            outlook.sigma_over(prior, hours), claimed, naive,
        )
        if judged is None:
            continue
        store.settle_verdict(
            symbol, row["source"], bar_time, now, float(actual["ret"]), float(actual["realized_vol"]),
            prior, float(actual["excursion"]), *judged,
            regime=reading.regime_at(bars, index) if index is not None else "unknown",
            exit_reason=claimed.exit if claimed else "",
            r_multiple=claimed.r_multiple if claimed else None,
        )
        marked += 1
    return marked


def seed(path: str = DEFAULT_PATH, days: int = 120, force: bool = False, now: float | None = None) -> dict[str, Any]:
    """Write a demo market. Returns what it wrote, per table."""
    now = time.time() if now is None else now
    store = Store(path)
    orders = store.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    trades = store.db.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
    if (orders or trades) and not force:
        raise Refused(
            f"{path} already holds {orders} orders and {trades} trades — this looks like a real database. "
            f"Demo data would put a market that never happened under decisions that did. "
            f"Use --db data/demo.db, or --force if you are certain.",
        )

    # One transaction for the lot. The store runs in autocommit (isolation_level=None), which is
    # right for an engine writing a row at a time and wrong for this: 20,000 single-row commits is
    # 20,000 fsyncs, and the wall clock goes to minutes while the CPU sits idle.
    store.db.execute("BEGIN")
    bars_per_symbol = days * 96
    written: dict[str, int] = {"bars": 0, "options_history": 0, "market_snapshots": 0, "verdicts": 0, "verdicts marked": 0}
    for n, symbol in enumerate(SYMBOLS):
        bars = candles(symbol, bars_per_symbol, int(now), seed=1234 + n)
        written["bars"] += store.record_bars(
            symbol, "15m", bars,
            {c.time: 1e9 * (1 + 0.1 * math.sin(i / 200)) for i, c in enumerate(bars)},
            {c.time: round(0.01 * math.sin(i / 90), 5) for i, c in enumerate(bars)},
        )
        rows = options_rows(symbol, bars)
        row_times = [r["ts"] for r in rows]  # sorted; the verdict cards slice it by time per reading
        for row in rows:
            store.record_options(row)
        written["options_history"] += len(rows)
        summary = summary_for(symbol, bars, rows)

        # The analyst's reading and the cards' verdicts, every 15 minutes over the last stretch.
        # Not the whole history: a verdict is cheap but the scan behind each one is not, and a
        # month of them is already more than any page shows.
        biases = mkt.bias_series(bars)
        recent = bars[-96 * min(days, 30) :]
        view_rows = recent[::4]  # one reading an hour is plenty to draw and to score
        for c in view_rows:
            upto = bars[: bars.index(c) + 1]
            if len(upto) < 300:
                continue
            ctx = {"price": c.close, "atm_iv": summary["day"]["atm_iv"], "rv_24h": mkt.realised_vol([b.close for b in upto], 96, BAR)}
            store.record_snapshot(symbol, c.time, float(c.time), ctx, "trend,iv_rich", round(random.Random(c.time).uniform(-3, 3), 2))
            written["market_snapshots"] += 1
            lo = bisect.bisect_left(row_times, c.time - 86_400)
            hi = bisect.bisect_right(row_times, c.time)
            history = rows[lo:hi]
            # A minimal view, enough for from_outlook: the demo cannot afford a full
            # outlook.build per reading (each is a scan over every bar so far) and does not
            # need one — the bias at this bar is what the verdict is about.
            bias = biases[bars.index(c)] or "range"
            lean = {"bullish": 1, "bearish": -1, "range": 0}[bias]
            view = {
                "ready": True,
                "alignment": {"verdict": f"leaning {'up' if lean > 0 else 'down'}" if lean else "all flat",
                              "score": lean * 2, "frames": "", "advice": ""},
                "direction": [],
            }
            for v in verdict.all_for(symbol, summary, history, [], None, view):
                store.record_verdict(symbol, c.time, float(c.time), c.close, v)
                written["verdicts"] += 1
        written["verdicts marked"] += _settle(store, symbol, bars, now)

    # commit(), not execute("COMMIT"): a store method that manages its own transaction
    # (save_settings does, with `with self.db:`) may already have closed the one opened above, and
    # an explicit COMMIT then raises "no transaction is active". commit() is a no-op in that case.
    store.db.commit()
    # Say so on every page. A made-up market that does not announce itself is how a demo number
    # ends up in a real decision.
    store.save_settings({"demo_data": "1"})
    store.db.close()
    return {"path": path, "days": days, "symbols": list(SYMBOLS), "written": written}
