"""Option structures from the live chain: straddle, strangle, iron condor and debit spreads.

Pure arithmetic on an options snapshot (options.summarize), so the ticket preview, the order and
the tests all run the same code. Only defined-risk structures: every one has a known max loss,
which is what the paper broker reserves as margin. Short straddles and strangles (unlimited
risk) are left out on purpose.

    straddle        buy ATM call + buy ATM put                  big move either way, IV cheap
    strangle        buy ~25-delta call + buy ~25-delta put      big move, cheaper than a straddle
    iron_condor     sell ~20-delta put & call, buy ~8-delta     range-bound, IV rich (credit)
                    wings beyond them
    call_spread     buy ATM call, sell ~25-delta call           moderately up
    put_spread      buy ATM put, sell ~25-delta put             moderately down

Prices are per one unit of the underlying (Delta quotes options that way); USD amounts multiply by
the contract value and the quantity. Buys fill at the ask, sells at the bid; without a quote, the
mark with the paper slippage.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

KINDS = {
    "straddle": "Long straddle",
    "strangle": "Long strangle",
    "iron_condor": "Iron condor",
    "call_spread": "Bull call spread",
    "put_spread": "Bear put spread",
}
FROM_PLAYBOOK = {"LONG_STRADDLE": "straddle", "LONG_STRANGLE": "strangle", "IRON_CONDOR": "iron_condor", "LONG_CALL_SPREAD": "call_spread", "LONG_PUT_SPREAD": "put_spread"}
CREDIT_KINDS = ("iron_condor",)
MIN_HOURS = 20.0  # same expiry rule as the playbook: the nearest that covers most of a day
FRESH_SECONDS = 60.0  # snapshots arrive every ~15s; older than this is no price (absence over staleness)
DEFAULT_SL_PCT = 50.0  # of the max loss
DEFAULT_TP_PCT = {"debit": 100.0, "credit": 50.0}  # of the premium paid / received
EVENT_IV_RATIO = 1.15  # nearest-expiry IV this far above the day's: an event is priced in, don't sell volatility


def leg_order_id(structure_id: str, leg: int) -> str:
    """Each leg of a structure is its own order, with an id derived from the structure's."""
    return f"tbo{hashlib.sha256(f'{structure_id}|{leg}'.encode()).hexdigest()[:29]}"


class StructureError(Exception):
    """The chain cannot give this structure right now. The message says why."""


def expiry_label(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%d %b")


def fresh(summary: dict[str, Any] | None, now: float) -> bool:
    return bool(summary) and now - float(summary.get("at") or 0) <= FRESH_SECONDS


def pick_expiry(summary: dict[str, Any], expiry: float | None = None) -> dict[str, Any]:
    expiries = [e for e in summary.get("expiries") or [] if str(int(e["expiry"])) in (summary.get("chains") or {})]
    if expiry:
        found = next((e for e in expiries if int(e["expiry"]) == int(expiry)), None)
        if found is None:
            raise StructureError(f"expiry {expiry_label(expiry)} is not in the live chain")
        return found
    found = next((e for e in expiries if e["hours"] >= MIN_HOURS), None)
    if found is None:
        raise StructureError(f"no expiry at least {MIN_HOURS:.0f}h out in the chain")
    return found


def fill_price(side: dict[str, Any] | None, action: str, slippage_pct: float) -> float | None:
    """What a buy (ask) or sell (bid) would fill at; the mark with slippage when there is no quote."""
    if not side:
        return None
    quote = side.get("ask") if action == "buy" else side.get("bid")
    if quote and quote > 0:
        return float(quote)
    mark = side.get("mark")
    if not mark or mark <= 0:
        return None
    return float(mark) * (1 + slippage_pct / 100 if action == "buy" else 1 - slippage_pct / 100)


def _priced(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [r for r in rows if r.get(kind) and (r[kind].get("mark") or r[kind].get("bid") or r[kind].get("ask"))]


def _atm(rows: list[dict[str, Any]], spot: float, kinds: tuple[str, ...]) -> dict[str, Any]:
    both = [r for r in rows if all(r in _priced(rows, k) for k in kinds)]
    if not both:
        raise StructureError("no priced at-the-money strike in the chain")
    return min(both, key=lambda r: abs(r["strike"] - spot))


def _by_delta(rows: list[dict[str, Any]], kind: str, target: float, spot: float, otm: bool = True) -> dict[str, Any]:
    """The strike whose |delta| is nearest target; out of the money only, unless otm is False."""
    side = [r for r in _priced(rows, kind) if r[kind].get("delta") is not None]
    if otm:
        side = [r for r in side if (r["strike"] > spot if kind == "call" else r["strike"] < spot)]
    if not side:
        raise StructureError(f"no priced out-of-the-money {kind} with a delta in the chain")
    return min(side, key=lambda r: abs(abs(r[kind]["delta"]) - target))


def _beyond(rows: list[dict[str, Any]], kind: str, strike: float, target: float, spot: float) -> dict[str, Any]:
    """A wing further out than `strike` (higher call, lower put), nearest the target delta."""
    side = [r for r in _priced(rows, kind) if (r["strike"] > strike if kind == "call" else r["strike"] < strike)]
    if not side:
        raise StructureError(f"no priced {kind} beyond {strike:g} for the wing")
    with_delta = [r for r in side if r[kind].get("delta") is not None]
    if with_delta:
        return min(with_delta, key=lambda r: abs(abs(r[kind]["delta"]) - target))
    return min(side, key=lambda r: abs(r["strike"] - strike))  # the next strike out


def _choose(kind: str, rows: list[dict[str, Any]], spot: float) -> list[tuple[str, str, dict[str, Any]]]:
    if kind == "straddle":
        atm = _atm(rows, spot, ("call", "put"))
        return [("buy", "call", atm), ("buy", "put", atm)]
    if kind == "strangle":
        return [("buy", "call", _by_delta(rows, "call", 0.25, spot)), ("buy", "put", _by_delta(rows, "put", 0.25, spot))]
    if kind == "iron_condor":
        sp, sc = _by_delta(rows, "put", 0.20, spot), _by_delta(rows, "call", 0.20, spot)
        lp, lc = _beyond(rows, "put", sp["strike"], 0.08, spot), _beyond(rows, "call", sc["strike"], 0.08, spot)
        return [("sell", "put", sp), ("buy", "put", lp), ("sell", "call", sc), ("buy", "call", lc)]
    if kind == "call_spread":
        buy = _atm(rows, spot, ("call",))
        return [("buy", "call", buy), ("sell", "call", _beyond(rows, "call", buy["strike"], 0.25, spot))]
    if kind == "put_spread":
        buy = _atm(rows, spot, ("put",))
        return [("buy", "put", buy), ("sell", "put", _beyond(rows, "put", buy["strike"], 0.25, spot))]
    raise StructureError(f"unknown structure {kind!r}; choose one of {', '.join(KINDS)}")


def payoff(legs: list[dict[str, Any]], settle: float) -> float:
    """Value at expiry per unit of the underlying, of what is held (buys +, sells -)."""
    total = 0.0
    for leg in legs:
        intrinsic = max(settle - leg["strike"], 0.0) if leg["kind"] == "call" else max(leg["strike"] - settle, 0.0)
        total += intrinsic if leg["action"] == "buy" else -intrinsic
    return total


def profile(legs: list[dict[str, Any]], net: float, kind: str) -> tuple[float, float | None, list[float]]:
    """(max loss, max profit or None when unlimited, breakevens) per unit of the underlying, at expiry.
    The payoff is piecewise linear with kinks at the strikes, so checking the strikes (and 0, and far
    out) finds every extreme and every zero crossing."""
    strikes = sorted({leg["strike"] for leg in legs})
    grid = sorted({*strikes, 0.0, strikes[-1] * 3})
    values = [payoff(legs, s) - net for s in grid]
    breakevens = []
    for a, b, va, vb in zip(grid, grid[1:], values, values[1:], strict=False):
        if va == 0:
            breakevens.append(a)
        elif va * vb < 0:
            breakevens.append(a + (b - a) * (-va) / (vb - va))
    return -min(values), None if kind in ("straddle", "strangle") else max(values), breakevens


def build(
    kind: str, summary: dict[str, Any], qty: int = 1, contract_value: float = 0.001, expiry: float | None = None,
    slippage_pct: float = 0.05, picks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The structure with real contracts and fill prices, and its risk: net, max loss/profit, breakevens.

    `picks` ([{action, kind, strike}]) uses those strikes instead of choosing (e.g. the playbook's legs)."""
    if kind not in KINDS:
        raise StructureError(f"unknown structure {kind!r}; choose one of {', '.join(KINDS)}")
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
        raise StructureError("quantity must be a whole number of contracts per leg, at least 1")
    spot = float(summary.get("spot") or 0)
    if spot <= 0:
        raise StructureError("no spot price in the options snapshot")
    exp = pick_expiry(summary, expiry)
    rows = summary["chains"][str(int(exp["expiry"]))]

    if picks:
        by_strike = {r["strike"]: r for r in rows}
        chosen = []
        for p in picks:
            row = by_strike.get(float(p["strike"]))
            if row is None:
                raise StructureError(f"strike {p['strike']:g} is no longer in the chain")
            chosen.append((p["action"], p["kind"], row))
    else:
        chosen = _choose(kind, rows, spot)

    legs = []
    for action, opt, row in chosen:
        side = row.get(opt) or {}
        fill = fill_price(side, action, slippage_pct)
        if fill is None:
            raise StructureError(f"no price for the {row['strike']:g} {opt}")
        legs.append({
            "action": action, "kind": opt, "strike": row["strike"], "symbol": side.get("symbol", ""), "price": round(fill, 4),
            "mark": side.get("mark"), "bid": side.get("bid"), "ask": side.get("ask"), "iv": side.get("iv"), "delta": side.get("delta"),
            "oi": side.get("oi"),
        })

    # net > 0: premium paid (debit); net < 0: premium received (credit). Per unit of the underlying.
    net = sum(leg["price"] if leg["action"] == "buy" else -leg["price"] for leg in legs)
    strikes = sorted({leg["strike"] for leg in legs})
    max_loss, max_profit, breakevens = profile(legs, net, kind)
    if max_loss <= 0:
        raise StructureError("prices in the chain give no risk at all: quotes look wrong, try again")

    units = contract_value * qty
    debit = net > 0
    return {
        "kind": kind, "name": KINDS[kind], "underlying": summary.get("underlying", ""), "spot": spot,
        "expiry": exp["expiry"], "expiry_label": exp["label"], "hours": exp["hours"], "atm_iv": exp.get("atm_iv"),
        "qty": qty, "contract_value": contract_value, "legs": legs,
        "net": round(net, 4), "type": "debit" if debit else "credit",
        "premium_usd": round(abs(net) * units, 4),
        "max_loss_usd": round(max_loss * units, 4),
        "max_profit_usd": None if max_profit is None else round(max_profit * units, 4),
        "breakevens": [round(b, 2) for b in breakevens],
        "label": f"{summary.get('underlying', '')} {KINDS[kind].upper()} {exp['label']} {'/'.join(f'{s:g}' for s in strikes)}",
        "snapshot_at": summary.get("at"),
    }


def quotes(legs: list[dict[str, Any]], summary: dict[str, Any] | None) -> list[dict[str, Any] | None]:
    """Each leg's side of the chain (mark, bid, ask, iv, greeks), or None for a leg not in the snapshot."""
    by_symbol = {r[k]["symbol"]: r[k] for chain in ((summary or {}).get("chains") or {}).values() for r in chain for k in ("call", "put") if r.get(k)}
    return [by_symbol.get(leg["symbol"]) for leg in legs]


def exit_prices(legs: list[dict[str, Any]], summary: dict[str, Any] | None, slippage_pct: float) -> list[float] | None:
    """What closing each leg now would fill at (buys sold at the bid, sells bought back at the ask), or
    None when any leg has no price in the snapshot."""
    if not summary:
        return None
    by_symbol = {r[k]["symbol"]: r[k] for chain in (summary.get("chains") or {}).values() for r in chain for k in ("call", "put") if r.get(k)}
    prices = []
    for leg in legs:
        px = fill_price(by_symbol.get(leg["symbol"]), "sell" if leg["action"] == "buy" else "buy", slippage_pct)
        if px is None:
            return None
        prices.append(px)
    return prices


def mark_prices(legs: list[dict[str, Any]], summary: dict[str, Any] | None) -> list[float] | None:
    """Each leg's mark (the mid of bid and ask without one), or None when any leg has neither. Delta values
    positions, and so unrealised PnL, at the mark; a close fills at the bid / ask (exit_prices)."""
    marks = [mark_of(side) for side in quotes(legs, summary)]
    return None if any(m is None for m in marks) else marks


def mark_of(side: dict[str, Any] | None) -> float | None:
    side = side or {}
    mark = side.get("mark")
    if not mark and side.get("bid") and side.get("ask"):
        mark = (side["bid"] + side["ask"]) / 2
    return float(mark) if mark and mark > 0 else None


def held_value(legs: list[dict[str, Any]], prices: list[float]) -> float:
    """Net value per unit of what is held at these prices: buys count +, sells -."""
    return sum(px if leg["action"] == "buy" else -px for leg, px in zip(legs, prices, strict=True))


def iv_rank(history: list[dict[str, Any]], now_iv: float | None) -> float | None:
    """Where today's ATM IV sits in the stored range, 0 (lowest) to 100 (highest)."""
    ivs = [r["atm_iv"] for r in history if r.get("atm_iv")]
    if now_iv is None or len(ivs) < 60:  # an hour of minutes at least
        return None
    lo, hi = min(ivs), max(ivs)
    return None if hi - lo < 1e-9 else round(100 * (now_iv - lo) / (hi - lo), 1)


def suggest(summary: dict[str, Any] | None, playbook: dict[str, Any] | None, history: list[dict[str, Any]]) -> dict[str, Any]:
    """Which structure fits now, and why. On the ticket it is advisory; the options auto trader
    (trading.AutoStructures) acts on it with playbook=None, i.e. on the options market alone.

    1. The playbook (trained forecast + IV), when it found a trade.
    2. Otherwise the options market alone: IV rank over the stored history with the put/call OI ratio.
       Cheap volatility -> buy it (straddle, or strangle when the straddle is dear); rich volatility ->
       sell it with defined risk (iron condor), its short strikes inside the OI walls when they fit.
    """
    if playbook and playbook.get("strategy") in FROM_PLAYBOOK and playbook.get("legs"):
        return {
            "kind": FROM_PLAYBOOK[playbook["strategy"]], "source": "playbook", "reason": playbook.get("reason", ""),
            "picks": [{"action": leg["action"], "kind": leg["kind"], "strike": leg["strike"]} for leg in playbook["legs"]],
        }
    if not summary:
        return {"kind": None, "source": "none", "reason": "No options data yet."}
    day = summary.get("day") or {}
    iv, pcr = day.get("atm_iv"), summary.get("pcr_oi")
    rank = iv_rank(history, iv)
    why_pb = f"Playbook: {playbook['reason']} " if playbook and playbook.get("reason") else ""
    if rank is None:
        return {"kind": None, "source": "options", "reason": why_pb + "Not enough IV history yet to judge whether options are cheap or dear (needs an hour of data)."}
    walls = f"put wall {day.get('put_wall'):,.0f}, call wall {day.get('call_wall'):,.0f}" if day.get("put_wall") and day.get("call_wall") else "no clear OI walls"
    tone = "" if pcr is None else (" Put/call OI {:.2f}: {}.".format(pcr, "puts dominate, hedging demand" if pcr > 1.1 else ("calls dominate" if pcr < 0.7 else "balanced")))
    base = f"{why_pb}ATM IV {100 * iv:.1f}% sits at {rank:.0f}% of its stored range;{tone} {walls}."
    if rank <= 25:
        cheap_straddle = (day.get("straddle_pct") or 0) <= (day.get("implied_move_pct") or 0) * 1.25
        kind = "straddle" if cheap_straddle else "strangle"
        return {"kind": kind, "source": "options", "reason": base + " Volatility is cheap: buying it risks only the premium."}
    if rank >= 75:
        near = (summary.get("nearest") or {}).get("atm_iv")
        if near and iv and near > iv * EVENT_IV_RATIO:
            return {"kind": None, "source": "options", "reason": base + f" But the nearest expiry's IV {100 * near:.1f}% is well above it: the market is pricing an event, so no condor."}
        return {"kind": "iron_condor", "source": "options", "reason": base + " Volatility is rich: sell it with both wings bought, so the loss is capped."}
    return {"kind": None, "source": "options", "reason": base + " IV is mid-range: no structure has an edge on IV alone."}


# Delta Exchange India options fees (help centre, "Fees on options and futures trading"): maker and taker
# 0.010% of the notional (spot x quantity), capped at 3.5% of the premium, plus 18% GST on the fee.
# An option that expires worthless pays nothing at settlement.
OPTIONS_FEE_PCT = 0.010
PREMIUM_CAP = 0.035
GST = 0.18


def fee(price: float, spot: float, units: float) -> float:
    """The fee, GST included, for one leg of `units` (contracts x contract value) filled at `price`."""
    return min(spot * units * OPTIONS_FEE_PCT / 100, PREMIUM_CAP * price * units) * (1 + GST)


def execution_order(legs: list[dict[str, Any]], closing: bool = False) -> list[int]:
    """The order to send legs in, as on a real exchange where each leg is its own order: open buys first,
    so a short leg is never sent without its cover; close the shorts first for the same reason."""
    first = "sell" if closing else "buy"
    return sorted(range(len(legs)), key=lambda i: legs[i]["action"] != first)
