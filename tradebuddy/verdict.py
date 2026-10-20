"""One short, checkable claim per card on the Market page.

Every chart on that page explained itself and then stopped. A paragraph about what term structure
means is not a reading of *this* term structure, and a person still has to do the work. So each
card now ends in a verdict: four words that say what it is saying, in a shape that can be written
down and marked later.

The marking is the point. A verdict carries the horizon it is a claim about and what to measure
it against, so the engine can settle it once that time has passed (`store.predictions`) and the
page can show how often each card has actually been right — beside the baseline that needed no
chart at all. A verdict nobody scores is decoration.

Rules only. No model, no network: the same numbers always give the same verdict, so the badge on
the chart can never drift from the chart.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from tradebuddy import provenance as prov

# What a verdict is a claim about, and so how it gets marked.
#   direction   price higher or lower after the horizon
#   volatility  realised volatility over the horizon, against what came before
#   range       price stays inside a stated band
MEASURES = ("direction", "volatility", "range")

STANCES = {
    "bullish": "direction", "bearish": "direction", "neutral": "direction",
    "vol_up": "volatility", "vol_down": "volatility",
    "range": "range",
    "unclear": "",  # says nothing, so nothing to mark
}
# Shown on the badge. Short on purpose — it sits on a chart, not in a paragraph.
STANCE_LABEL = {
    "bullish": "Leans up", "bearish": "Leans down", "neutral": "No lean",
    "vol_up": "Expect movement", "vol_down": "Expect quiet",
    "range": "Rangebound", "unclear": "Not enough to say",
}


@dataclass(frozen=True)
class Verdict:
    key: str  # which card it belongs to
    stance: str
    label: str  # four words, on the badge
    detail: str  # one sentence under it
    confidence: float  # 0..1 — how strongly the rule fired, not a probability of being right
    horizon_hours: float
    numbers: dict[str, Any]  # the inputs, so a claim can always be traced back

    @property
    def measure(self) -> str:
        return STANCES.get(self.stance, "")

    @property
    def scorable(self) -> bool:
        return bool(self.measure)

    @property
    def source(self) -> dict[str, Any]:
        """Which kind of evidence this card rests on. An options card and a counted card can reach
        the same verdict and still not deserve the same weight."""
        return prov.of(self.key)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"measure": self.measure, "scorable": self.scorable, "source": self.source["key"]}


# `numbers` is an explicit dict, not **kwargs: the inputs it carries are whatever the card
# measures, and one of them is called "hours" — which silently collided with the horizon
# argument and raised on the one card that had an expiry.
def _v(key: str, stance: str, label: str, detail: str, confidence: float, hours: float,
       numbers: dict[str, Any] | None = None) -> Verdict:
    return Verdict(key=key, stance=stance, label=label, detail=detail, confidence=round(min(1.0, max(0.0, confidence)), 3),
                   horizon_hours=hours, numbers={k: v for k, v in (numbers or {}).items() if v is not None})


def nothing(key: str, why: str, hours: float = 24.0) -> Verdict:
    return _v(key, "unclear", STANCE_LABEL["unclear"], why, 0.0, hours)


# -- the cards ------------------------------------------------------------------------


def term_structure(options: dict[str, Any] | None) -> Verdict:
    """ATM IV by expiry. Normally it slopes up; when the front sits above the back, the market is
    paying for something inside that window."""
    term = (options or {}).get("term") or []
    priced = [e for e in term if e.get("atm_iv")]
    if len(priced) < 2:
        return nothing("term_structure", "Fewer than two expiries are priced.")
    front, back = priced[0], priced[-1]
    slope = back["atm_iv"] - front["atm_iv"]
    nums = {"front_iv": round(front["atm_iv"], 4), "back_iv": round(back["atm_iv"], 4),
            "slope": round(slope, 4), "front_label": front.get("label"), "hours": front.get("hours")}
    hours = float(front.get("hours") or 24)
    if slope <= -0.02:
        return _v("term_structure", "vol_up", f"Event priced into {front.get('label', 'the front')}",
                  f"The nearest expiry costs {100 * -slope:.1f} vol points more than the furthest — the market expects "
                  f"something inside that window. Selling volatility into it is how a quiet-looking trade goes wrong.",
                  min(1.0, -slope / 0.06), hours, nums)
    if slope >= 0.06:
        return _v("term_structure", "vol_down", "Front is calm",
                  f"The curve slopes up steeply ({100 * slope:.1f} vol points front to back): nothing is priced for the "
                  f"near term, and near-dated premium is cheap relative to later expiries.",
                  min(1.0, slope / 0.12), hours, nums)
    return _v("term_structure", "neutral", "Normal curve",
              f"The curve slopes up {100 * slope:.1f} vol points front to back, which is what it normally does. "
              f"Nothing unusual is being priced.", 0.2, hours, nums)


def atm_iv_trend(history: list[dict[str, Any]], hours: float = 24.0) -> Verdict:
    """What a day of movement has been costing, over the stored options history."""
    points = [r for r in history or [] if r.get("atm_iv")]
    if len(points) < 8:
        return nothing("atm_iv", f"Only {len(points)} readings stored — the engine keeps one a minute.", hours)
    first, last = points[0]["atm_iv"], points[-1]["atm_iv"]
    lo, hi = min(p["atm_iv"] for p in points), max(p["atm_iv"] for p in points)
    change = last - first
    where = (last - lo) / (hi - lo) if hi > lo else 0.5
    nums = {"from": round(first, 4), "to": round(last, 4), "change": round(change, 4),
            "low": round(lo, 4), "high": round(hi, 4), "position": round(where, 3), "readings": len(points)}
    if change >= 0.02:
        return _v("atm_iv", "vol_up", "Premium getting dearer",
                  f"ATM IV has risen from {100 * first:.0f}% to {100 * last:.0f}% over the window. Buyers are paying up, "
                  f"usually into uncertainty — options cost more to own and more to be short.",
                  min(1.0, change / 0.06), hours, nums)
    if change <= -0.02:
        return _v("atm_iv", "vol_down", "Premium draining out",
                  f"ATM IV has fallen from {100 * first:.0f}% to {100 * last:.0f}%. The market is calming; long premium "
                  f"bleeds in this, short premium is being paid.", min(1.0, -change / 0.06), hours, nums)
    return _v("atm_iv", "neutral", "Premium steady",
              f"ATM IV has barely moved ({100 * first:.0f}% to {100 * last:.0f}%), sitting {100 * where:.0f}% of the way "
              f"up its own 24-hour range.", 0.2, hours, nums)


def pcr_shift(history: list[dict[str, Any]], hours: float = 4.0, min_shift: float = 0.15) -> Verdict:
    """Put/call open interest. The level sits wherever the book sits; the move is the information.

    Same reading as the pcr_options strategy, and deliberately so — the card should say what the
    strategy acts on, or one of them is wrong."""
    points = [r for r in history or [] if r.get("pcr_oi")]
    if len(points) < 8:
        return nothing("pcr", f"Only {len(points)} readings stored.", hours)
    first, last = points[0]["pcr_oi"], points[-1]["pcr_oi"]
    shift = last - first
    nums = {"from": round(first, 3), "to": round(last, 3), "shift": round(shift, 3), "readings": len(points)}
    if shift >= min_shift:
        return _v("pcr", "bullish", "Puts being written",
                  f"Put/call open interest has risen {first:.2f} to {last:.2f}. Writers are moving into puts, which is "
                  f"support being sold into the book — the move matters, not the level.",
                  min(1.0, shift / 0.4), hours, nums)
    if shift <= -min_shift:
        return _v("pcr", "bearish", "Calls being written",
                  f"Put/call open interest has fallen {first:.2f} to {last:.2f}. Writers are moving into calls: supply "
                  f"above the market, which caps rallies.", min(1.0, -shift / 0.4), hours, nums)
    return _v("pcr", "neutral", "Book has not moved",
              f"Put/call open interest is {last:.2f}, within {abs(shift):.2f} of where it was. The ratio sits where the "
              f"book happens to sit; nothing has changed hands to say otherwise.", 0.2, hours, nums)


def chain_oi(options: dict[str, Any] | None) -> Verdict:
    """Where the open interest actually is, read as levels rather than a wall of numbers.

    The strikes with the most open interest are where dealers are positioned, and price tends to
    be drawn to them into an expiry and to meet resistance at them before it."""
    day = (options or {}).get("day") or {}
    spot = (options or {}).get("spot")
    near = (options or {}).get("nearest") or {}
    call_wall, put_wall, pain = day.get("call_wall"), day.get("put_wall"), near.get("max_pain")
    if not spot or not call_wall or not put_wall:
        return nothing("chain_oi", "The chain has no clear open-interest peaks yet.")
    hours = float(near.get("hours") or day.get("hours") or 24)
    up_gap = 100 * (call_wall / spot - 1)
    down_gap = 100 * (1 - put_wall / spot)
    nums = {"spot": spot, "call_wall": call_wall, "put_wall": put_wall, "max_pain": pain,
            "call_gap_pct": round(up_gap, 2), "put_gap_pct": round(down_gap, 2), "hours": hours}
    width = up_gap + down_gap
    if width <= 1.5:
        return _v("chain_oi", "range", f"Pinned {put_wall:,.0f} to {call_wall:,.0f}",
                  f"The heaviest strikes sit only {width:.1f}% apart around spot, {hours:.0f}h from expiry. Price is "
                  f"boxed between them; fading the edges beats chasing a break until one gives way.",
                  min(1.0, 1.5 / max(width, 0.1) / 2), hours, nums)
    if up_gap <= 0.5:
        return _v("chain_oi", "bearish", f"Into the call wall at {call_wall:,.0f}",
                  f"Spot is {up_gap:.1f}% under the strike with the most call open interest. That is where the supply "
                  f"is: rallies tend to stall there, and getting through it usually needs volume.",
                  0.6, hours, nums)
    if down_gap <= 0.5:
        return _v("chain_oi", "bullish", f"On the put wall at {put_wall:,.0f}",
                  f"Spot is {down_gap:.1f}% above the strike with the most put open interest — the level the book is "
                  f"defending. It holds more often than it breaks, and when it breaks it goes quickly.",
                  0.6, hours, nums)
    side = "bearish" if pain and spot > pain else "bullish" if pain else "neutral"
    detail = (
        f"Room either way: the call wall is {up_gap:.1f}% up at {call_wall:,.0f}, the put wall {down_gap:.1f}% down at "
        f"{put_wall:,.0f}. Those are the two levels worth putting a target or a stop at."
    )
    if pain:
        detail += f" Max pain is {pain:,.0f}, {abs(100 * (spot / pain - 1)):.1f}% {'above' if spot > pain else 'below'} spot."
    return _v("chain_oi", side if pain else "neutral", f"Range {put_wall:,.0f} to {call_wall:,.0f}", detail, 0.3, hours, nums)


def from_outlook(view: dict[str, Any]) -> Verdict:
    """The Outlook card, as one claim: which way, over the horizon the timeframes agree on."""
    if not view.get("ready"):
        return nothing("outlook", "Not enough stored bars yet.")
    align = view["alignment"]
    verdict_text = align["verdict"]
    counted = next((d for d in view["direction"] if d.get("conditional") is not None), None)
    hours = counted["horizon_hours"] if counted else 24.0
    nums = {"verdict": verdict_text, "score": align["score"], "frames": align["frames"]}
    if counted:
        nums |= {"counted": counted["conditional"], "baseline": counted["baseline"], "sample": counted["conditional_sample"]}

    if verdict_text.startswith("aligned up") or verdict_text.startswith("leaning up"):
        stance, label = "bullish", "Timeframes lean up"
    elif verdict_text.startswith("aligned down") or verdict_text.startswith("leaning down"):
        stance, label = "bearish", "Timeframes lean down"
    elif verdict_text == "all flat":
        stance, label = "range", "Nothing is trending"
    else:
        stance, label = "neutral", "Timeframes disagree"
    confidence = abs(align["score"]) / 3
    if counted and counted.get("edge") is not None:
        confidence = min(1.0, confidence + min(0.3, abs(counted["edge"]) * 4))
    return _v("outlook", stance, label, align["advice"], confidence, hours, nums)


def from_insights(found: list[dict[str, Any]], lean: dict[str, Any] | None) -> Verdict:
    """The Insights card, as one claim. A list of fifteen findings is not a reading until someone
    adds them up, so this adds them up the same way every time."""
    if not found:
        return nothing("insights", "No rule has fired on the current numbers.")
    lean = lean or {}
    bull, bear = float(lean.get("bullish") or 0), float(lean.get("bearish") or 0)
    alerts = [i for i in found if i["severity"] == "alert"]
    nums = {"bullish": bull, "bearish": bear, "findings": len(found), "alerts": len(alerts),
            "top": [i["key"] for i in found[:3]]}
    gap = bull - bear
    lead = f"{len(found)} findings, {len(alerts)} of them alerts. " if alerts else f"{len(found)} findings. "
    strongest = found[0]["title"] if found else ""
    if gap >= 1.5:
        return _v("insights", "bullish", "Findings lean up",
                  f"{lead}Bullish {bull:.1f} against bearish {bear:.1f}. Strongest: {strongest}.",
                  min(1.0, gap / 5), 24.0, nums)
    if gap <= -1.5:
        return _v("insights", "bearish", "Findings lean down",
                  f"{lead}Bearish {bear:.1f} against bullish {bull:.1f}. Strongest: {strongest}.",
                  min(1.0, -gap / 5), 24.0, nums)
    return _v("insights", "neutral", "Findings cancel out",
              f"{lead}Bullish {bull:.1f} against bearish {bear:.1f} — no side carries it. Strongest: {strongest}.",
              0.2, 24.0, nums)


# -- marking them ---------------------------------------------------------------------


def settle(
    row: dict[str, Any], ret: float, vol: float, prior_vol: float, sigma: float,
    claimed: Any = None, naive: Any = None,
) -> tuple[bool, bool] | None:
    """Did the claim hold, and did the null claim hold over the same bars?

    `correct` is the card's claim. `baseline_correct` is what happened with no reading at all, so
    the scoreboard can divide the two: a card that is right 56% of the time in a quarter where
    price rose 56% of the time has told you nothing.

      direction   when a bracketed outcome is given, the claim holds if a trade taken on it
                  reached its target before its stop, net of costs — the thing the Trader
                  actually does. Scoring direction on "did the close end higher" marked a
                  question nobody trades: a call can be right while its trade was stopped out
                  hours earlier. Without the bars to walk, it falls back to the close.
      volatility  vol_up holds if realised vol over the horizon beat the same span before it;
                  the null is "volatility rose"
      range       the card names its own band (the option walls); the null is the ±1 sigma band
                  plain volatility would have drawn. Asking whether the walls contained price
                  better than arithmetic did is the only version of this question worth asking.
    """
    measure, stance = row.get("measure"), row.get("stance")
    if measure == "direction":
        if stance in ("bullish", "bearish") and claimed is not None and naive is not None:
            return bool(claimed.won), bool(naive.won)
        if stance == "bullish":
            return ret > 0, ret > 0
        if stance == "bearish":
            return ret < 0, ret > 0
        if stance == "neutral":
            # "No lean" is a claim that nothing much happens: it holds when the move stayed inside
            # what volatility alone implied.
            return abs(ret) <= sigma, ret > 0
        return None
    if measure == "volatility":
        rose = vol > prior_vol
        if stance == "vol_up":
            return rose, rose
        if stance == "vol_down":
            return not rose, rose
        if stance == "neutral":
            return abs(vol - prior_vol) <= 0.15 * prior_vol if prior_vol else False, rose
        return None
    if measure == "range":
        numbers = row.get("numbers") or {}
        price, lo, hi = row.get("price"), numbers.get("put_wall"), numbers.get("call_wall")
        inside_band = abs(ret) <= sigma
        if not price or lo is None or hi is None:
            return inside_band, inside_band
        end = price * (1 + ret)
        return bool(lo <= end <= hi), inside_band
    return None


MIN_MARKED = 30  # fewer settled claims than this and no edge is reported, however large it looks


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def score(rows: list[dict[str, Any]], min_marked: int = MIN_MARKED) -> dict[str, Any]:
    """Per card: how often it held, against the best anyone could do without it.

    The baseline is the best CONSTANT call over the same outcomes — always say the thing that
    happened more often — not the raw event rate. Getting that wrong produced the worst number
    this page has shown: over a stretch where volatility fell in 66 of 68 windows, a card that
    said "calm" every time scored 65/68 and was reported as **+91 points of edge**. Against
    "always say it falls", which needs no card at all, it is a point and a half behind.

    The two outcomes are measured on the same rows, so the comparison is paired and the
    disagreements are what carry the information: of the times the card and the naive call
    differed, how often was the card the one that was right. That is reported too.

    Below `min_marked` settled claims nothing is called an edge. A 91-point lead over 34 outcomes
    is a coin that landed the same way twice in a row, written in a bigger font.
    """
    by_source: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("correct") is not None and r.get("baseline_correct") is not None:
            by_source.setdefault(r["source"], []).append(r)
    out = []
    for source, group in sorted(by_source.items()):
        n = len(group)
        hit = sum(r["correct"] for r in group) / n
        # How often the thing the claim is about actually happened, and so the best you could do
        # by always calling the commoner side.
        happened = sum(r["baseline_correct"] for r in group) / n
        baseline = max(happened, 1 - happened)
        edge = hit - baseline
        # Paired: where the card and the naive call disagreed, who was right.
        card_won = sum(1 for r in group if r["correct"] and not r["baseline_correct"])
        naive_won = sum(1 for r in group if not r["correct"] and r["baseline_correct"])
        # Is the hit rate above the baseline by more than sampling noise would give?
        spread = (baseline * (1 - baseline) / n) ** 0.5
        z = edge / spread if spread else 0.0
        enough = n >= min_marked
        stances: dict[str, dict[str, Any]] = {}
        for r in group:
            st_ = stances.setdefault(r["stance"], {"stance": r["stance"], "n": 0, "hit": 0})
            st_["n"] += 1
            st_["hit"] += r["correct"]
        out.append({
            "source": source, "n": n, "enough": enough, "min_marked": min_marked,
            "hit_rate": round(hit, 4),
            "baseline": round(baseline, 4),
            "always": "the commoner outcome",
            "edge": round(edge, 4) if enough else None,
            "z": round(z, 2) if enough else None,
            # A rate that clears sampling noise. Not proof — a reason to keep watching.
            "reliable": bool(enough and abs(z) >= 1.96),
            "better_than_chance": bool(enough and z >= 1.96),
            "p_value": round(2 * (1 - _normal_cdf(abs(z))), 4) if enough else None,
            "disagreed": card_won + naive_won,
            "card_won": card_won,
            "naive_won": naive_won,
            "measure": group[0].get("measure"),
            "by_stance": [s | {"hit_rate": round(s["hit"] / s["n"], 4)} for s in sorted(stances.values(), key=lambda x: -x["n"])],
            "from": group[0]["bar_time"], "to": group[-1]["bar_time"],
        })
    # Nine cards, each asked at 95%, means one of them clears by luck about every other reading.
    # Benjamini-Hochberg keeps the share of false finds among those called reliable near 5%
    # instead of near 100% — without it this page would celebrate a new discovery most weeks.
    tested = [c for c in out if c["p_value"] is not None]
    for rank, card in enumerate(sorted(tested, key=lambda c: c["p_value"]), start=1):
        card["bh_threshold"] = round(0.05 * rank / len(tested), 4)
    survivors = [c for c in sorted(tested, key=lambda c: c["p_value"]) if c["p_value"] <= c["bh_threshold"]]
    cutoff = max((c["p_value"] for c in survivors), default=0.0)
    for card in out:
        card["tests"] = len(tested)
        card["reliable"] = bool(card.get("p_value") is not None and card["p_value"] <= cutoff)
        card["better_than_chance"] = bool(card["reliable"] and (card["z"] or 0) > 0)
    # Worth looking at first: the ones that survive, then the biggest samples.
    out.sort(key=lambda c: (not c["reliable"], -abs(c["edge"] or 0), -c["n"]))
    return {"cards": out, "total": sum(c["n"] for c in out), "min_marked": min_marked, "tests": len(tested)}


def all_for(symbol: str, options: dict[str, Any] | None, options_history: list[dict[str, Any]],
            found: list[dict[str, Any]], lean: dict[str, Any] | None, view: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every card's verdict, in one list — what the page renders and what the engine stores."""
    verdicts = [
        term_structure(options),
        atm_iv_trend(options_history),
        pcr_shift(options_history),
        chain_oi(options),
        from_insights(found, lean),
    ]
    if view:
        verdicts.append(from_outlook(view))
        if view.get("volume"):
            verdicts.append(volume(view["volume"]))
        if view.get("indicators"):
            verdicts.append(indicators(view["indicators"]))
        shaped = view.get("shapes") or {}
        if shaped.get("latest") is not None:
            verdicts.append(shapes(shaped["latest"], shaped.get("record") or []))
        if view.get("vwap") and view.get("profile"):
            verdicts.append(where_it_traded(view["vwap"], view["profile"], view.get("anatomy") or {}))
        if view.get("levels"):
            verdicts.append(levels(view["levels"]))
    return [v.to_dict() | {"symbol": symbol} for v in verdicts]


def volume(block: dict[str, Any], hours: float = 1.0) -> Verdict:
    """Is anyone behind the move. Volume never says which way — only whether to believe the way."""
    if not block.get("ready"):
        return nothing("volume", block.get("note", "Not enough bars yet."), hours)
    ratio = block.get("hour_ratio") or 0
    up, down = block.get("up_share"), block.get("down_share")
    obv_slope = block.get("obv_slope_20")
    nums = {"hour_ratio": ratio, "last_ratio": block.get("last_ratio"), "z": block.get("z"),
            "up_share": up, "obv_slope_20": obv_slope}
    lean = None
    if up is not None and abs(up - 0.5) >= 0.06:
        lean = "bullish" if up > 0.5 else "bearish"
    if ratio >= 1.8:
        side = lean or "neutral"
        who = f"{100 * up:.0f}% of it on up closes" if up is not None else "no clear side"
        return _v("volume", side, "Heavy, and someone is paying",
                  f"The last hour traded {ratio:.1f}x a normal hour, {who}. Moves made on volume like this follow "
                  f"through more often than quiet ones — it is the difference between a level being taken and brushed.",
                  min(1.0, ratio / 3), hours, nums)
    if ratio <= 0.5:
        return _v("volume", "range", "Nobody is here",
                  f"The last hour traded {ratio:.1f}x a normal hour. Breaks made on volume this thin are the ones that "
                  f"get given back; ranges hold until somebody turns up.", min(1.0, (0.5 - ratio) * 2), hours, nums)
    if lean and obv_slope is not None and (obv_slope > 0) == (lean == "bullish"):
        return _v("volume", lean, f"Size is leaning {'up' if lean == 'bullish' else 'down'}",
                  f"Volume is ordinary ({ratio:.1f}x), but {100 * (up if lean == 'bullish' else down):.0f}% of the last "
                  f"day's size went through on {'up' if lean == 'bullish' else 'down'} closes and on-balance volume agrees. "
                  f"Quiet accumulation reads like this.", 0.45, hours, nums)
    return _v("volume", "neutral", "Ordinary volume",
              f"The last hour traded {ratio:.1f}x normal with size split {100 * (up or 0.5):.0f}/{100 * (down or 0.5):.0f} "
              f"between up and down closes. Nothing here argues for or against a move.", 0.2, hours, nums)


def indicators(block: dict[str, Any], hours: float = 4.0) -> Verdict:
    """The classical readings, added up — with ADX deciding whether any of them count.

    Trend readings in a range are the most reliably wrong thing on a chart, so the regime is read
    first and everything else is reported inside it."""
    if not block.get("ready"):
        return nothing("indicators", block.get("note", "Not enough bars yet."), hours)
    adx_v, adx_state = block["adx"]["value"], block["adx"]["state"]
    rsi_v, rsi_state = block["rsi"]["value"], block["rsi"]["state"]
    hist = block["macd"]["hist"]
    ema_up, ema_down = block["ema"]["stacked_up"], block["ema"]["stacked_down"]
    pos = block["bollinger"]["position"]
    nums = {"adx": adx_v, "rsi": rsi_v, "macd_hist": hist, "bb_position": pos,
            "stacked_up": ema_up, "stacked_down": ema_down, "squeeze": block["bollinger"]["squeeze"]}

    if adx_state == "ranging":
        if block["bollinger"]["squeeze"]:
            return _v("indicators", "vol_up", "Coiled inside a range",
                      f"ADX {adx_v:.0f} says there is no trend, and the bands are tighter than they have been. "
                      f"Ranges like this end in an expansion; which way it goes is not something these readings know.",
                      0.5, hours, nums)
        edge = "" if pos is None else (" Price is at the top of its band." if pos > 0.85 else
                                       " Price is at the bottom of its band." if pos < 0.15 else "")
        return _v("indicators", "range", "No trend to follow",
                  f"ADX {adx_v:.0f} is below 20: this is a range, and trend readings misfire in one.{edge} "
                  f"RSI {rsi_v:.0f} ({rsi_state}), MACD histogram {hist:+.2f}.", 0.4, hours, nums)

    votes = sum([1 if ema_up else -1 if ema_down else 0,
                 1 if (hist or 0) > 0 else -1 if (hist or 0) < 0 else 0,
                 1 if (rsi_v or 50) > 55 else -1 if (rsi_v or 50) < 45 else 0])
    if votes >= 2:
        return _v("indicators", "bullish", f"Trending up, ADX {adx_v:.0f}",
                  f"The EMA stack, MACD and RSI {rsi_v:.0f} agree, inside a market ADX calls {adx_state}. "
                  f"This is the regime trend rules are built for.", min(1.0, votes / 3), hours, nums)
    if votes <= -2:
        return _v("indicators", "bearish", f"Trending down, ADX {adx_v:.0f}",
                  f"The EMA stack, MACD and RSI {rsi_v:.0f} agree to the downside, inside a market ADX calls {adx_state}.",
                  min(1.0, -votes / 3), hours, nums)
    return _v("indicators", "neutral", "Trending, but not together",
              f"ADX {adx_v:.0f} says something is moving, but the readings disagree: RSI {rsi_v:.0f} ({rsi_state}), "
              f"MACD histogram {hist:+.2f}, EMA stack {'up' if ema_up else 'down' if ema_down else 'mixed'}.",
              0.25, hours, nums)


def shapes(found: list[dict[str, Any]], record: list[dict[str, Any]], hours: float = 1.0) -> Verdict:
    """The candle shapes on the latest bars, weighed by what each has actually led to.

    A hammer is a shape, not a signal. The only thing that earns it a mention is its own record,
    so a shape with no measured edge is reported as what it is: a shape."""
    if not found:
        return nothing("shapes", "No named shape on the last few bars.", hours)
    by_name = {r["name"]: r for r in record}
    scored = [(s, by_name.get(s["name"])) for s in found]
    best = max(scored, key=lambda pair: abs((pair[1] or {}).get("edge") or 0))
    shape, rec = best
    label = shape.get("label", shape["name"])
    nums = {"shapes": [s["name"] for s in found], "strength": shape.get("strength")}
    if rec:
        nums |= {"count": rec["count"], "followed_through": rec["followed_through"], "edge": rec["edge"]}
    if not rec or rec.get("edge") is None:
        return _v("shapes", "unclear", f"{label}, unmeasured",
                  f"A {label.lower()} on the latest bars, but there are too few of them in the stored history to say "
                  f"whether it has ever led anywhere. Run backfill and it will have a record.", 0.1, hours, nums)
    edge = rec["edge"]
    if abs(edge) < 0.02:
        return _v("shapes", "neutral", f"{label}, no edge",
                  f"A {label.lower()}. Over {rec['count']:,} of them it {rec['asks']} {100 * rec['followed_through']:.0f}% "
                  f"of the time against {100 * rec['baseline']:.0f}% for any bar — which is to say it is a shape, not a signal.",
                  0.15, hours, nums)
    direction = shape["direction"]
    stance = "bullish" if (direction == "bull") == (edge > 0) and direction != "neutral" else (
        "bearish" if direction != "neutral" else "vol_up")
    return _v("shapes", stance, f"{label}, {100 * edge:+.0f} pts",
              f"A {label.lower()}. Over {rec['count']:,} of them it {rec['asks']} {100 * rec['followed_through']:.0f}% of "
              f"the time against {100 * rec['baseline']:.0f}% for any bar.", min(1.0, abs(edge) * 10), hours, nums)


def where_it_traded(vwap: dict[str, Any], profile: dict[str, Any], shape: dict[str, Any], hours: float = 4.0) -> Verdict:
    """Price against the levels the money itself made: VWAP and the point of control.

    Both are prices rather than states, which is what makes them worth a verdict — they are levels
    a trade can be placed against. Everything else on the Volume tab is a ratio.

    Read together on purpose: price far from VWAP *and* outside the value area is stretched on
    both measures and tends to come back; stretched on one and not the other is a market that has
    moved its own centre, which is what a trend is.
    """
    if not vwap.get("ready") or not profile.get("ready"):
        return nothing("where", "Not enough bars to build a profile yet.", hours)
    z = float(vwap.get("z") or 0)
    inside = bool(profile.get("inside_value"))
    poc_gap = float(profile.get("poc_distance_pct") or 0)
    nums = {"vwap": vwap["vwap"], "vwap_z": z, "poc": profile["poc"],
            "value_low": profile["value_low"], "value_high": profile["value_high"],
            "inside_value": inside, "poc_distance_pct": poc_gap}
    if shape.get("ready"):
        nums |= {"body_pct": shape["body_pct"], "buy_pressure": shape["buy_pressure"]}

    levels = f"VWAP {vwap['vwap']:,.0f}, point of control {profile['poc']:,.0f}, value {profile['value_low']:,.0f}-{profile['value_high']:,.0f}."
    if abs(z) >= 2 and not inside:
        side = "bullish" if z < 0 else "bearish"
        return _v("where", side, f"Stretched {'below' if z < 0 else 'above'} where it traded",
                  f"Price is {abs(z):.1f} volume-weighted deviations {'below' if z < 0 else 'above'} VWAP and outside the "
                  f"value area — stretched on both measures at once, which usually comes back. {levels}",
                  min(1.0, abs(z) / 3), hours, nums)
    if abs(z) >= 1.5 and inside:
        return _v("where", "neutral", "Moved its own centre",
                  f"Price is {abs(z):.1f} deviations from VWAP but still inside the value area: the market is carrying its "
                  f"own volume with it rather than stretching away from it, which is what a trend looks like here. {levels}",
                  0.4, hours, nums)
    if inside and abs(poc_gap) <= 0.4:
        return _v("where", "range", f"Pinned at the point of control {profile['poc']:,.0f}",
                  f"Price is sitting on the price the most size changed hands at, inside the value area. This is where a "
                  f"market goes to agree; the edges of the value area are the levels worth trading against. {levels}",
                  0.5, hours, nums)
    return _v("where", "neutral", "Inside value" if inside else "Outside value",
              f"Price is {abs(z):.1f} deviations from VWAP and {'inside' if inside else 'outside'} the value area. {levels}",
              0.2, hours, nums)


def levels(block: dict[str, Any], hours: float = 24.0) -> Verdict:
    """Price against the highs and lows everyone can see.

    A fresh extreme and a price sitting just under an old one are opposite situations and look
    identical in a number: the first is a market with nothing above it, the second is a market
    being held down by orders at a known price. Which one it is comes first."""
    windows = (block or {}).get("windows") or []
    if not windows:
        return nothing("levels", "Not enough bars to mark a range yet.", hours)
    nums = {w["key"]: {"high": w["high"], "low": w["low"], "position": w["position"]} for w in windows}

    fresh = [w for w in windows if w["fresh_high"] or w["fresh_low"]]
    if fresh:
        widest = max(fresh, key=lambda w: w["bars"])
        up = widest["fresh_high"]
        return _v("levels", "bullish" if up else "bearish",
                  f"New {widest['label'].lower()} {'high' if up else 'low'}",
                  f"Price has just made a new {'high' if up else 'low'} for {widest['label'].lower()} at "
                  f"{(widest['high'] if up else widest['low']):,.0f}. There is nothing overhead from this window to sell "
                  f"into — the levels above are from longer ones." if up else
                  f"Price has just made a new low for {widest['label'].lower()} at {widest['low']:,.0f}. Nothing from this "
                  f"window is holding it up; the next level down is from a longer one.",
                  min(1.0, widest["bars"] / 3000), hours, nums)

    live = [w for w in windows if w["high_in_play"] or w["low_in_play"]]
    if live:
        widest = max(live, key=lambda w: w["bars"])
        at_high = widest["high_in_play"]
        level = widest["high"] if at_high else widest["low"]
        gap = widest["to_high_pct"] if at_high else widest["to_low_pct"]
        return _v("levels", "bearish" if at_high else "bullish",
                  f"{gap:.1f}% under the {widest['label'].lower()} high" if at_high
                  else f"{gap:.1f}% above the {widest['label'].lower()} low",
                  f"Price is within {gap:.1f}% of the {widest['label'].lower()} "
                  f"{'high' if at_high else 'low'} at {level:,.0f}, set {widest['high_hours_ago' if at_high else 'low_hours_ago']:.0f}h ago. "
                  f"A level this well known is where the orders are: it either holds and turns price, or it breaks and "
                  f"the stops behind it go.", 0.6, hours, nums)

    day = next((w for w in windows if w["key"] == "1d"), windows[0])
    pos = day["position"]
    where = "the top of" if pos and pos > 0.7 else "the bottom of" if pos and pos < 0.3 else "the middle of"
    return _v("levels", "neutral", "Mid-range on every window",
              f"Price is in {where} today's {day['low']:,.0f}-{day['high']:,.0f} range and not near any longer window's "
              f"extreme. Nothing well-known is in play, which is when breakout rules misfire most.", 0.2, hours, nums)
