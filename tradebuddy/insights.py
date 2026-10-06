"""Rule-based market insights: numbers in, short findings out. No model and no network, so it runs
in milliseconds and every finding can be traced to the numbers that produced it.

`market_context` turns candles, open interest, funding and the options summary into one flat dict
of named numbers. `insights` applies plain rules to that dict. Each insight says how much it
matters (severity) and which way it leans (bias).
"""

from __future__ import annotations

import itertools
import math
from typing import Any

from tradebuddy.delta import Candle
from tradebuddy.strategies.indicators import atr, ema

BARS_PER_DAY = 96  # 15-minute bars
ANNUAL = math.sqrt(365 * BARS_PER_DAY)

SEVERITY_RANK = {"alert": 0, "watch": 1, "info": 2}


def _pct(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else round(100 * (a / b - 1), 3)


def _rv(closes: list[float], bars: int) -> float | None:
    """Annualised realised volatility of the last `bars` 15-minute log returns, as a fraction."""
    if len(closes) < bars + 1:
        return None
    window = closes[-bars - 1 :]
    rets = [math.log(b / a) for a, b in itertools.pairwise(window) if a > 0 and b > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) * ANNUAL


def market_context(
    symbol: str, candles: list[Candle], oi: list[Candle] | None = None, funding: list[Candle] | None = None,
    stats: dict[str, Any] | None = None, options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Named numbers for one symbol. Candles are closed 15-minute bars, oldest first."""
    closes = [c.close for c in candles]
    price = (stats or {}).get("last") or (closes[-1] if closes else None)
    ctx: dict[str, Any] = {"symbol": symbol, "price": price, "bars": len(candles)}
    if closes:
        ctx |= {
            "change_1h_pct": _pct(closes[-1], closes[-5]) if len(closes) > 4 else None,
            "change_4h_pct": _pct(closes[-1], closes[-17]) if len(closes) > 16 else None,
            "change_24h_pct": _pct(closes[-1], closes[-97]) if len(closes) > 96 else None,
            "rv_24h": _rv(closes, BARS_PER_DAY),
            "rv_7d": _rv(closes, 7 * BARS_PER_DAY),
        }
        day = candles[-BARS_PER_DAY:]
        hi, lo = max(c.high for c in day), min(c.low for c in day)
        ctx |= {"high_24h": hi, "low_24h": lo, "range_pos_24h": round((closes[-1] - lo) / (hi - lo), 3) if hi > lo else None}
        atrs = atr([c.high for c in candles], [c.low for c in candles], closes, 14)
        ctx["atr_pct"] = round(100 * atrs[-1] / closes[-1], 3) if atrs else None
        for n in (20, 50, 200):
            line = ema(closes, n)
            ctx[f"ema{n}"] = round(line[-1], 2) if line else None
        vols = [c.volume for c in candles]
        if len(vols) >= BARS_PER_DAY + 4:
            base = vols[-BARS_PER_DAY - 4 : -4]
            mean = sum(base) / len(base)
            sd = math.sqrt(sum((v - mean) ** 2 for v in base) / len(base))
            ctx["volume_z_1h"] = round((sum(vols[-4:]) / 4 - mean) / sd, 2) if sd else None
    if oi:
        oi_close = [c.close for c in oi]
        ctx |= {
            "oi": oi_close[-1],
            "oi_change_1h_pct": _pct(oi_close[-1], oi_close[-5]) if len(oi_close) > 4 else None,
            "oi_change_24h_pct": _pct(oi_close[-1], oi_close[-97]) if len(oi_close) > 96 else None,
        }
    if funding:
        rates = [c.close for c in funding]
        ctx |= {"funding_pct": rates[-1], "funding_avg_24h_pct": round(sum(rates[-BARS_PER_DAY:]) / len(rates[-BARS_PER_DAY:]), 5)}
    if stats:
        ctx |= {"oi_usd": stats.get("oi_usd"), "turnover_24h_usd": stats.get("turnover_24h_usd"), "basis_pct": _pct(stats.get("mark"), stats.get("index"))}
    if options:
        day, near = options["day"], options["nearest"]
        spot = options["spot"]
        front, back = (options["term"][0]["atm_iv"], options["term"][-1]["atm_iv"]) if options.get("term") else (None, None)
        ctx |= {
            "atm_iv": day["atm_iv"],
            "iv_rv_ratio": round(day["atm_iv"] / ctx["rv_7d"], 3) if day["atm_iv"] and ctx.get("rv_7d") else None,
            "skew_25d": day["skew_25d"],
            "pcr_oi": options["pcr_oi"],
            "pcr_volume": options["pcr_volume"],
            "options_oi_usd": options["oi_usd"],
            "options_turnover_usd": options["turnover_usd"],
            "implied_move_pct": day["implied_move_pct"],
            "implied_move_label": day["label"],
            "implied_move_hours": day["hours"],
            "max_pain": near["max_pain"],
            "max_pain_hours": near["hours"],
            "max_pain_gap_pct": _pct(spot, near["max_pain"]),
            "call_wall": day["call_wall"],
            "put_wall": day["put_wall"],
            "call_wall_gap_pct": _pct(day["call_wall"], spot),
            "put_wall_gap_pct": _pct(day["put_wall"], spot),
            "term_slope": round(back - front, 4) if front and back else None,  # + = contango (normal)
        }
    return ctx


def _fmt_pct(v: float, digits: int = 1) -> str:
    return f"{v:+.{digits}f}%"


def _money(v: float | None) -> str:
    return "—" if v is None else f"{v:,.0f}"


def insights(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Plain rules over market_context. Ordered by severity, alerts first."""
    out: list[dict[str, Any]] = []

    def add(key: str, severity: str, bias: str, title: str, detail: str) -> None:
        out.append({"key": key, "severity": severity, "bias": bias, "title": title, "detail": detail})

    g = ctx.get
    # -- volatility: what options charge vs what the market has been doing
    if g("implied_move_pct") is not None:
        move = g("price", 0) * g("implied_move_pct") / 100
        add("implied_move", "info", "volatility", f"Options price a ±{g('implied_move_pct'):.2f}% move by {g('implied_move_label')}",
            f"About ±{_money(move)} in {g('implied_move_hours'):.0f}h, one standard deviation, from ATM IV {100 * g('atm_iv'):.1f}%.")
    ratio = g("iv_rv_ratio")
    if ratio is not None and g("rv_7d"):
        iv, rv = 100 * g("atm_iv"), 100 * g("rv_7d")
        if ratio >= 1.3:
            add("iv_rich", "watch", "volatility", f"Options look expensive: IV {iv:.0f}% vs 7-day realised {rv:.0f}%",
                f"IV/RV {ratio:.2f}. Sellers of premium (condors, covered calls) are paid well unless a move is coming.")
        elif ratio <= 0.8:
            add("iv_cheap", "watch", "volatility", f"Options look cheap: IV {iv:.0f}% vs 7-day realised {rv:.0f}%",
                f"IV/RV {ratio:.2f}. Buying straddles or spreads costs less than recent moves have been worth.")
    if g("rv_24h") and g("rv_7d"):
        r = g("rv_24h") / g("rv_7d")
        if r >= 1.5:
            add("vol_expanding", "watch", "volatility", "Volatility is expanding",
                f"Last 24h realised {100 * g('rv_24h'):.0f}% vs {100 * g('rv_7d'):.0f}% over 7 days ({r:.1f}x).")
        elif r <= 0.6:
            add("vol_compressing", "watch", "volatility", "Volatility is compressing: a breakout often follows",
                f"Last 24h realised {100 * g('rv_24h'):.0f}% vs {100 * g('rv_7d'):.0f}% over 7 days ({r:.1f}x).")
    if g("term_slope") is not None and g("term_slope") <= -0.05:
        add("term_inverted", "alert", "volatility", "Near-term options cost more than later ones",
            f"Front ATM IV is {100 * -g('term_slope'):.1f} vol points above the far expiry: the market expects something soon.")

    # -- positioning in options
    skew = g("skew_25d")
    if skew is not None:
        if skew >= 0.05:
            add("put_skew", "watch", "bearish", f"Puts are bid: 25-delta skew +{100 * skew:.1f} vol",
                "Traders pay up for downside protection more than for upside.")
        elif skew <= -0.03:
            add("call_skew", "watch", "bullish", f"Calls are bid: 25-delta skew {100 * skew:.1f} vol", "Upside calls cost more than equivalent puts: demand for upside.")
    pcr = g("pcr_oi")
    if pcr is not None:
        if pcr >= 1.3:
            add("pcr_high", "watch", "bearish", f"Put-heavy open interest: put/call {pcr:.2f}",
                "More puts than calls are open. Often hedging; at extremes it can mark a low.")
        elif pcr <= 0.6:
            add("pcr_low", "watch", "bullish", f"Call-heavy open interest: put/call {pcr:.2f}",
                "Far more calls than puts are open: optimism, which at extremes can mark a top.")
    gap, hours = g("max_pain_gap_pct"), g("max_pain_hours")
    if gap is not None and hours is not None and hours <= 24 and abs(gap) >= 1:
        add("max_pain", "info", "bearish" if gap > 0 else "bullish",
            f"Spot is {abs(gap):.1f}% {'above' if gap > 0 else 'below'} max pain {_money(g('max_pain'))}",
            f"{hours:.0f}h to the nearest expiry. Prices sometimes drift towards max pain into settlement.")
    for side, bias, word in (("call_wall", "bearish", "resistance"), ("put_wall", "bullish", "support")):
        wgap = g(f"{side}_gap_pct")
        if wgap is not None and abs(wgap) <= 1.0:
            add(side, "watch", bias, f"Price is within {abs(wgap):.1f}% of the {side.replace('_', ' ')} at {_money(g(side))}",
                f"The strike with the most {'call' if side == 'call_wall' else 'put'} open interest often acts as {word}.")

    # -- futures positioning
    funding = g("funding_avg_24h_pct")
    if funding is not None:
        if funding >= 0.03:
            add("funding_high", "alert" if funding >= 0.06 else "watch", "bearish", f"Longs are paying: funding {funding:.3f}% (24h average)",
                "Crowded longs. Squeezes lower get sharper when funding runs this hot.")
        elif funding <= -0.03:
            add("funding_low", "alert" if funding <= -0.06 else "watch", "bullish", f"Shorts are paying: funding {funding:.3f}% (24h average)",
                "Crowded shorts: fuel for a squeeze higher.")
    oi_chg, px_chg = g("oi_change_24h_pct"), g("change_24h_pct")
    if oi_chg is not None and px_chg is not None and abs(oi_chg) >= 5:
        if oi_chg > 0:
            reading = ("New longs are opening", "bullish") if px_chg > 0 else ("New shorts are opening", "bearish")
        else:
            reading = ("Shorts are covering", "bullish") if px_chg > 0 else ("Longs are being closed out", "bearish")
        add("oi_flow", "watch", reading[1], f"{reading[0]}: open interest {_fmt_pct(oi_chg)} in 24h",
            f"Price {_fmt_pct(px_chg, 2)} over the same 24h.")
    if g("volume_z_1h") is not None and g("volume_z_1h") >= 3:
        add("volume_spike", "alert", "neutral", f"Volume spike: last hour {g('volume_z_1h'):.1f} standard deviations above normal",
            "Unusual activity. Moves on heavy volume tend to follow through more than quiet ones.")

    # -- trend and range
    price, e20, e50, e200 = g("price"), g("ema20"), g("ema50"), g("ema200")
    if price and e20 and e50 and e200:
        if price > e50 > e200 and e20 > e50:
            add("trend", "info", "bullish", "Uptrend on 15m: price above EMA 20, 50 and 200", f"EMA50 {_money(e50)}, EMA200 {_money(e200)}.")
        elif price < e50 < e200 and e20 < e50:
            add("trend", "info", "bearish", "Downtrend on 15m: price below EMA 20, 50 and 200", f"EMA50 {_money(e50)}, EMA200 {_money(e200)}.")
        else:
            add("trend", "info", "neutral", "No clear trend on 15m", f"Price {_money(price)} between EMA50 {_money(e50)} and EMA200 {_money(e200)}.")
    pos = g("range_pos_24h")
    if pos is not None and (pos >= 0.95 or pos <= 0.05):
        top = pos >= 0.95
        add("range_edge", "watch", "bullish" if top else "bearish", f"Trading at the 24h {'high' if top else 'low'}",
            f"24h range {_money(g('low_24h'))} - {_money(g('high_24h'))}.")

    out.sort(key=lambda i: SEVERITY_RANK[i["severity"]])
    return out


def lean(found: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts of bullish and bearish findings, weighted by severity. A tally, not a forecast."""
    weight = {"alert": 2, "watch": 1, "info": 0.5}
    bull = sum(weight[i["severity"]] for i in found if i["bias"] == "bullish")
    bear = sum(weight[i["severity"]] for i in found if i["bias"] == "bearish")
    side = "bullish" if bull - bear >= 1 else "bearish" if bear - bull >= 1 else "mixed"
    return {"bullish": bull, "bearish": bear, "lean": side}
