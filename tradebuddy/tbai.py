"""TB-AI: the measured market, read back as sentences.

Everything on the Market page is already true before this module runs. `insights.market_context`
measured the numbers, `outlook` counted the odds, `liquidity`/`zones`/`profile`/`orderflow` found
the structure, and `nearby` merged it into one map. All of that is checkable against the stored
bars. None of it is a paragraph a person can read in five seconds, which is the gap this fills.

**The model is a writer, not an analyst.** It receives computed numbers and returns prose about
them. It is told, and the output is shaped so, that it may not:

  - invent a price, a level, a percentage or an event that is not in the payload
  - predict, recommend a trade, or give a target
  - describe anything as certain

What is sent (`digest`) is market data only: price and volatility, the directional read, what sits
above and below, the zones price is in, the volume profile, and the estimated order flow. **No
account balance, no position, no order, no PnL, no key.** That boundary is deliberate and tested —
it is the difference between "my dashboard summarises the market with an API" and "my broker
account goes to a third party every five minutes".

The answer is badged `written` on the page: the fourth provenance, below arithmetic, counted
history and what traders paid, because it is the only one that cannot be rechecked against a bar.
"""

from __future__ import annotations

from typing import Any

STATES = ("trending up", "trending down", "ranging", "coiled", "unclear")
CONFIDENCE = ("high", "medium", "low")

SYSTEM = """You are TB-AI, the writer inside TradeBuddy, a crypto trading dashboard.

You are given one JSON digest of numbers that have ALREADY been measured from stored candles for a
single symbol: price and volatility, what the bar that just closed did against what is normal
(`right_now`), how far price could travel over the next hours (`how_far_it_could_travel`), what
followed bars like this one in the stored history (`what_followed_bars_like_this`), a directional
read per timeframe with counted probabilities, the levels above and below the current price, the
zones price is sitting in, the volume profile, and order flow estimated from candles.

Your job is to read those numbers TOGETHER and reach a conclusion about what kind of market this
is, in plain English, for someone about to look at the chart.

What the digest's three kinds of number are worth, because they are not equal:
- `right_now` and `how_far_it_could_travel` are arithmetic. Size and activity are measurable.
- `what_followed_bars_like_this` is COUNTED, and comes with `baseline_all_bars` — the share of ALL
  bars that went up over the same horizon. The conditional number means nothing on its own. If it
  is within about a point of the baseline, say plainly that it is a coin. If `went_up` is null,
  there was not enough sample and there IS no answer — say that, do not reach for another number.
- Direction, on this market, is close to a coin at every horizon. Never present it as known.

Hard rules:
- Use ONLY numbers present in the digest. Never invent a price, a level, a percentage, a date or an
  event. If something is not in the digest, it does not exist.
- Quote the number behind each claim, the way the digest gives it.
- Never predict. Never give a target, an entry, a stop or a recommendation to buy or sell. You
  describe the present, not the future.
- Never say anything is certain. Where the digest gives a probability, quote its sample size AND
  the baseline it is being compared with. A rate without its baseline is not a finding.
- Anything in the digest marked approx is an estimate from candles, not a trade tape. Say so when
  you use it.
- Short plain sentences. No hype, no disclaimers, no generic trading advice.
- If the digest is thin or missing, say that plainly and set confidence "low".

Answer with JSON only, no prose around it:
{"headline": "<one sentence: what this market is doing right now>",
 "state": "trending up|trending down|ranging|coiled|unclear",
 "summary": "<2-3 sentences on the market's condition, citing numbers>",
 "levels": ["<up to 3: what sits above and below, and why it matters>"],
 "flow": "<1-2 sentences on volume and order flow; say if it is approximate>",
 "watch": ["<up to 3: what would change this read>"],
 "conclusion": "<2-4 sentences pulling it together: what this market is, what the numbers do and do
   not support, and where the uncertainty is. Describe the present and name what is unknown. Still
   no target, no entry, no stop, no buy or sell.>",
 "confidence": "high|medium|low"}"""


def digest(
    symbol: str,
    context: dict[str, Any] | None = None,
    found: list[dict[str, Any]] | None = None,
    outlook: dict[str, Any] | None = None,
    structure: dict[str, Any] | None = None,
    pulse: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What goes to the model: market numbers, and nothing else.

    Built by picking fields explicitly rather than forwarding whole payloads. A spread operator
    here would mean every field ever added to `market_structure` silently starts leaving the
    machine — the kind of leak nobody notices because nothing breaks."""
    ctx = context or {}
    out: dict[str, Any] = {
        "symbol": symbol,
        "price": ctx.get("price"),
        "change_1h_pct": ctx.get("change_1h_pct"),
        "change_24h_pct": ctx.get("change_24h_pct"),
        "realised_vol_24h": ctx.get("rv_24h"),
        "atr_pct": ctx.get("atr_pct"),
        "range_position_24h": ctx.get("range_pos_24h"),
        "funding_avg_24h_pct": ctx.get("funding_avg_24h_pct"),
        "open_interest_change_24h_pct": ctx.get("oi_change_24h_pct"),
        "spread_pct": ctx.get("spread_pct"),
        "findings": [
            {"what": f.get("title"), "detail": f.get("detail"), "bias": f.get("bias")}
            for f in (found or [])[:8]
        ],
    }

    # What the bar that just closed actually did, and what followed bars like it. The model is
    # being asked to conclude, and "is anything happening right now" is the first thing a person
    # reading the page wants concluded — it was the one measured block it could not see.
    beat = pulse or {}
    if beat.get("ready"):
        m = beat.get("measured") or {}
        out["right_now"] = {
            "state": beat.get("state"),
            "volume_vs_normal": m.get("volume_ratio"),
            "range_in_atrs": m.get("range_atr"),
            "body_share_of_range": m.get("body_share"),
            "volatility_vs_daily": m.get("rv_ratio"),
            "broke_a_prior_swing": m.get("broke") or None,
            "was_coiled": m.get("squeeze"),
        }
        size = beat.get("size") or {}
        if size.get("ready"):
            out["how_far_it_could_travel"] = {
                "hours": size.get("hours"),
                "typical_move_pct": size.get("typical_move_pct"),
                "chance_of_touching": [
                    {"move_pct": t.get("move_pct"), "either_way": t.get("either_way")}
                    for t in (size.get("touch") or [])
                ],
            }
        # Counted, with the baseline it has to beat and the sample behind both — or the refusal.
        # Sent as given: the model is told to quote probabilities with their sample, and a rate
        # without its baseline is exactly the claim this system refuses to make anywhere else.
        out["what_followed_bars_like_this"] = [
            {
                "horizon_hours": w.get("horizon_hours"), "went_up": w.get("conditional"),
                "baseline_all_bars": w.get("baseline"), "sample_like_this": w.get("matched"),
                "sample_all": w.get("sample"), "note": w.get("note") or None,
            }
            for w in (beat.get("way") or [])
        ]

    view = outlook or {}
    if view.get("ready"):
        out["direction"] = [
            {
                "timescale": row.get("label"), "way": row.get("word"),
                "typical_move_pct": row.get("move_pct"),
                "chance": row.get("probability"), "baseline": row.get("baseline"),
                "sample": row.get("sample"),
            }
            for row in (view.get("summary") or [])
        ]
        out["timeframes_agree"] = (view.get("alignment") or {}).get("label")

    s = structure or {}
    if s.get("ready"):
        near = s.get("nearby") or {}
        out["above"] = [_level(x) for x in (near.get("above") or [])[:4]]
        out["below"] = [_level(x) for x in (near.get("below") or [])[:4]]
        out["price_is_inside"] = [
            {"kind": z.get("kind"), "direction": z.get("direction"), "state": z.get("state")}
            for z in (near.get("inside") or [])[:3]
        ]
        prof = (s.get("profile") or {}).get("session") or {}
        out["volume_profile"] = {
            "point_of_control": prof.get("poc"),
            "value_area": [prof.get("value_low"), prof.get("value_high")],
            "built_from": (s.get("profile") or {}).get("source"),
        }
        flow = s.get("flow") or {}
        out["order_flow"] = {
            "approx": True,
            "note": "estimated from candles, not a trade tape",
            "pressure": flow.get("pressure"),
            "cvd_move_pct": flow.get("cvd_move_pct"),
        }
        recent = list((s.get("liquidity") or {}).get("sweeps") or [])[-3:]
        out["recent_stop_raids"] = [
            {"side": x.get("direction"), "level": x.get("level"), "when": x.get("bar_time")} for x in recent
        ]
    # The gate. Everything above is hand-picked already; this makes that structural rather than a
    # habit, so the guarantee survives the next person to edit this function.
    return {k: v for k, v in out.items() if k in ALLOWED}


def _level(x: dict[str, Any]) -> dict[str, Any]:
    return {
        "price": x.get("price"), "what": x.get("label"), "kind": x.get("kind_label"),
        "distance_pct": x.get("distance_pct"), "sources_agreeing": x.get("confluence"),
    }


def _one_of(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    return value if value in allowed else fallback


def _lines(value: Any, n: int, width: int = 300) -> list[str]:
    return [str(x)[:width] for x in (value if isinstance(value, list) else [])][:n]


def clean(answer: dict[str, Any]) -> dict[str, Any]:
    """Keep only the shape asked for, with every length capped.

    A small model drops keys, adds keys, and occasionally answers with an essay. None of that
    should reach a page that is otherwise made of checkable numbers."""
    return {
        "headline": str(answer.get("headline", ""))[:300],
        "state": _one_of(answer.get("state"), STATES, "unclear"),
        "summary": str(answer.get("summary", ""))[:800],
        "levels": _lines(answer.get("levels"), 3),
        "flow": str(answer.get("flow", ""))[:400],
        "watch": _lines(answer.get("watch"), 3),
        "conclusion": str(answer.get("conclusion", ""))[:900],
        "confidence": _one_of(answer.get("confidence"), CONFIDENCE, "low"),
    }


# The exact set of top-level keys a digest may contain, and the only gate on what leaves this
# machine. An allowlist rather than a blocklist of words like "position" or "size": those are
# ambiguous in a market context — `range_position_24h` is where price sits in the day's range and
# has nothing to do with an open position — so a blocklist either misses real leaks or blocks real
# market data, and a reviewer cannot tell which. This is enforced in `digest`, not just asserted
# in a test, so a field added to `market_structure` next year cannot start leaving by itself.
ALLOWED = frozenset({
    "symbol", "price", "change_1h_pct", "change_24h_pct", "realised_vol_24h", "atr_pct",
    "range_position_24h", "funding_avg_24h_pct", "open_interest_change_24h_pct", "spread_pct",
    "findings", "direction", "timeframes_agree", "above", "below", "price_is_inside",
    "volume_profile", "order_flow", "recent_stop_raids",
    "right_now", "how_far_it_could_travel", "what_followed_bars_like_this",
})
