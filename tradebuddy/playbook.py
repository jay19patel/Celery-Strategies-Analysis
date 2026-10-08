"""Options playbook: a forecast plus today's option prices -> one structure, or NO_TRADE.

Deterministic, so the same numbers always give the same answer, and every choice is explained in
`reason`. A forecast the model showed no skill at (see forecast.ModelCard.skill) is not used.

    directional, model has direction skill:   up >= 62%  -> LONG_CALL_SPREAD
                                               down >= 62% -> LONG_PUT_SPREAD
    volatility, model has realised-vol skill:  forecast RV >= 1.15 x ATM IV -> LONG_STRADDLE, or
                                               LONG_STRANGLE when the straddle costs more than the
                                               expected move
                                               ATM IV >= 1.15 x forecast RV, breakout unlikely
                                               -> IRON_CONDOR
    otherwise                                  NO_TRADE

Legs are real contracts from the snapshot's chain, priced at mark. Advisory only: the Positions
page can load its pick into the options ticket (structures.suggest), and a person places it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STRATEGIES = ("NO_TRADE", "LONG_CALL_SPREAD", "LONG_PUT_SPREAD", "LONG_STRADDLE", "LONG_STRANGLE", "IRON_CONDOR")


@dataclass(frozen=True)
class PlaybookConfig:
    min_direction_confidence: float = 0.62
    min_breakout_probability: float = 0.62
    max_range_breakout_probability: float = 0.35
    vol_edge_ratio: float = 1.15
    min_hours: float = 20.0  # the expiry used must cover most of the 24h forecast


def _nearest(rows: list[dict[str, Any]], target: float, kind: str) -> dict[str, Any] | None:
    priced = [r for r in rows if r.get(kind) and r[kind].get("mark")]
    return min(priced, key=lambda r: abs(r["strike"] - target)) if priced else None


def _leg(action: str, kind: str, row: dict[str, Any]) -> dict[str, Any]:
    side = row[kind]
    return {"action": action, "kind": kind, "strike": row["strike"], "symbol": side["symbol"], "mark": side["mark"], "iv": side.get("iv"), "delta": side.get("delta")}


def _by_delta(rows: list[dict[str, Any]], kind: str, target: float) -> dict[str, Any] | None:
    priced = [r for r in rows if r.get(kind) and r[kind].get("mark") and r[kind].get("delta") is not None]
    return min(priced, key=lambda r: abs(abs(r[kind]["delta"]) - target)) if priced else None


def decide(forecast: dict[str, Any] | None, options: dict[str, Any] | None, cfg: PlaybookConfig | None = None) -> dict[str, Any]:
    cfg = cfg or PlaybookConfig()

    def no_trade(reason: str, **extra: Any) -> dict[str, Any]:
        return {"strategy": "NO_TRADE", "score": 0.0, "reason": reason, "legs": [], **extra}

    if not forecast:
        return no_trade("No trained model for this symbol yet (python -m tradebuddy train).")
    if not options:
        return no_trade("No options data yet.")
    expiry = next((e for e in options["expiries"] if e["hours"] >= cfg.min_hours), None)
    if expiry is None or not expiry.get("atm_iv"):
        return no_trade(f"No expiry at least {cfg.min_hours:.0f}h out with an ATM IV in the chain.")
    rows = options["chains"].get(str(int(expiry["expiry"]))) or []
    spot, iv = options["spot"], expiry["atm_iv"]
    skill = forecast.get("skill", {})
    up, down = forecast["up_probability"], forecast["down_probability"]
    move = forecast["expected_abs_move"]
    rv = forecast["predicted_realized_vol"]
    breakout = forecast["breakout_probability"]
    edge = rv / iv if iv else 0.0
    context = {"expiry": expiry["label"], "hours": expiry["hours"], "atm_iv": iv, "vol_edge": round(edge, 3)}

    # -- directional
    if skill.get("direction") and max(up, down) >= cfg.min_direction_confidence:
        bull = up >= down
        kind = "call" if bull else "put"
        target = spot * (1 + move) if bull else spot * (1 - move)
        buy, sell = _nearest(rows, spot, kind), _nearest(rows, target, kind)
        if buy and sell and buy["strike"] != sell["strike"]:
            debit = buy[kind]["mark"] - sell[kind]["mark"]
            width = abs(sell["strike"] - buy["strike"])
            if debit > 0:
                return {
                    "strategy": "LONG_CALL_SPREAD" if bull else "LONG_PUT_SPREAD",
                    "score": round(max(up, down), 3),
                    "reason": f"Model gives {100 * max(up, down):.0f}% for {'up' if bull else 'down'} over 24h (direction has skill on the holdout); "
                    f"short leg at the expected move {100 * move:.1f}%.",
                    "legs": [_leg("buy", kind, buy), _leg("sell", kind, sell)],
                    "net": round(-debit, 2), "max_loss": round(debit, 2), "max_profit": round(width - debit, 2),
                    "breakevens": [round(buy["strike"] + debit if bull else buy["strike"] - debit, 2)],
                    **context,
                }

    if not skill.get("realized_vol"):
        why = "realised-vol model has no skill on the holdout" if not skill.get("direction") else f"direction confidence {100 * max(up, down):.0f}% is under {100 * cfg.min_direction_confidence:.0f}%"
        return no_trade(f"Nothing to act on: {why}.", **context)

    # -- long volatility
    if edge >= cfg.vol_edge_ratio and (breakout >= cfg.min_breakout_probability or not skill.get("breakout")):
        call, put = _nearest(rows, spot, "call"), _nearest(rows, spot, "put")
        if call and put:
            cost = call["call"]["mark"] + put["put"]["mark"]
            expected = move * spot
            if expected >= cost:
                return {
                    "strategy": "LONG_STRADDLE", "score": round(min(1.0, edge / 2), 3),
                    "reason": f"Forecast realised vol {100 * rv:.0f}% is {edge:.2f}x ATM IV {100 * iv:.0f}%, and the expected move {expected:,.0f} covers the straddle {cost:,.0f}.",
                    "legs": [_leg("buy", "call", call), _leg("buy", "put", put)],
                    "net": round(-cost, 2), "max_loss": round(cost, 2), "max_profit": None,
                    "breakevens": [round(put["strike"] - cost, 2), round(call["strike"] + cost, 2)], **context,
                }
            wing_c, wing_p = _by_delta(rows, "call", 0.25), _by_delta(rows, "put", 0.25)
            if wing_c and wing_p:
                cost = wing_c["call"]["mark"] + wing_p["put"]["mark"]
                return {
                    "strategy": "LONG_STRANGLE", "score": round(min(1.0, edge / 2), 3),
                    "reason": f"Forecast realised vol {100 * rv:.0f}% is {edge:.2f}x ATM IV, but the straddle costs more than the expected move: 25-delta wings instead.",
                    "legs": [_leg("buy", "call", wing_c), _leg("buy", "put", wing_p)],
                    "net": round(-cost, 2), "max_loss": round(cost, 2), "max_profit": None,
                    "breakevens": [round(wing_p["strike"] - cost, 2), round(wing_c["strike"] + cost, 2)], **context,
                }

    # -- short volatility, defined risk
    if iv >= cfg.vol_edge_ratio * rv and (breakout <= cfg.max_range_breakout_probability or not skill.get("breakout")):
        implied = (expiry.get("implied_move_pct") or 100 * move) / 100 * spot
        sc, sp = _nearest(rows, spot + implied, "call"), _nearest(rows, spot - implied, "put")
        if sc and sp:
            lc, lp = _nearest(rows, sc["strike"] + implied / 2, "call"), _nearest(rows, sp["strike"] - implied / 2, "put")
            if lc and lp and lc["strike"] > sc["strike"] and lp["strike"] < sp["strike"]:
                credit = sc["call"]["mark"] + sp["put"]["mark"] - lc["call"]["mark"] - lp["put"]["mark"]
                width = max(lc["strike"] - sc["strike"], sp["strike"] - lp["strike"])
                if credit > 0:
                    return {
                        "strategy": "IRON_CONDOR", "score": round(min(1.0, iv / rv / 2), 3),
                        "reason": f"ATM IV {100 * iv:.0f}% is {iv / rv:.2f}x the forecast realised vol {100 * rv:.0f}%, breakout odds {100 * breakout:.0f}%: sell the implied move, wings half as far again.",
                        "legs": [_leg("sell", "put", sp), _leg("buy", "put", lp), _leg("sell", "call", sc), _leg("buy", "call", lc)],
                        "net": round(credit, 2), "max_profit": round(credit, 2), "max_loss": round(width - credit, 2),
                        "breakevens": [round(sp["strike"] - credit, 2), round(sc["strike"] + credit, 2)], **context,
                    }

    return no_trade(f"No edge: forecast realised vol {100 * rv:.0f}% vs ATM IV {100 * iv:.0f}% (ratio {edge:.2f}), breakout odds {100 * breakout:.0f}%.", **context)
