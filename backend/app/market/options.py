"""Option quotes and the arithmetic on a chain: ATM IV, skew, put/call ratios, max pain, OI walls,
the move the market is pricing. Pure functions over quotes, so they are tested without a network."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

EXPIRY_HOUR_UTC = 12  # Delta India BTC/ETH options settle at 12:00 UTC (17:30 IST)
YEAR_HOURS = 365 * 24
PERPETUALS = {"BTC": "BTCUSD", "ETH": "ETHUSD"}
UNDERLYING_OF = {perp: u for u, perp in PERPETUALS.items()}
PERP_SLIPPAGE = 0.0005  # applied to a perpetual's mark when a side of its book is empty
CHAIN_WIDTH = 0.15  # strikes within ±15% of spot are sent to the dashboard
CHAIN_EXPIRIES = 4  # nearest expiries sent with their chain in a summary


@dataclass(frozen=True)
class OptionQuote:
    symbol: str  # C-BTC-110000-091026
    underlying: str
    kind: str  # call | put
    strike: float
    expiry: float  # settlement, unix seconds
    mark: float | None = None
    bid: float | None = None
    ask: float | None = None
    iv: float | None = None  # mark IV, annualised fraction: 0.45 = 45%
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    oi: float = 0.0  # in the underlying
    oi_usd: float = 0.0
    volume: float = 0.0  # 24h, in the underlying
    turnover_usd: float = 0.0  # 24h
    spot: float | None = None  # the index the option settles on
    contract_value: float = 0.001
    at: float = 0.0  # when it was received

    def hours_left(self, now: float | None = None) -> float:
        return max(0.0, (self.expiry - (time.time() if now is None else now)) / 3600)

    @property
    def spread_pct(self) -> float | None:
        """Bid/ask spread as % of the mark: how much a round trip gives away to the book."""
        if self.bid is None or self.ask is None or not self.mark:
            return None
        return 100 * (self.ask - self.bid) / self.mark


def _f(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_symbol(symbol: str) -> tuple[str, str, float, float] | None:
    """C-BTC-110000-091026 -> ("call", "BTC", 110000.0, settlement unix time)."""
    parts = str(symbol).split("-")
    if len(parts) != 4 or parts[0] not in ("C", "P"):
        return None
    try:
        strike = float(parts[2])
        day = datetime.strptime(parts[3], "%d%m%y").replace(hour=EXPIRY_HOUR_UTC, tzinfo=UTC)
    except ValueError:
        return None
    return ("call" if parts[0] == "C" else "put"), parts[1], strike, day.timestamp()


def parse_ticker(msg: dict[str, Any], now: float | None = None) -> OptionQuote | None:
    """One option ticker, from the WebSocket or REST /v2/tickers (the same shape)."""
    parsed = parse_symbol(msg.get("symbol", ""))
    if parsed is None:
        return None
    kind, underlying, strike, expiry = parsed
    quotes, greeks = msg.get("quotes") or {}, msg.get("greeks") or {}
    bid, ask = _f(quotes.get("best_bid")), _f(quotes.get("best_ask"))
    return OptionQuote(
        symbol=msg["symbol"], underlying=underlying, kind=kind, strike=strike, expiry=expiry,
        mark=_f(msg.get("mark_price")), bid=bid if bid else None, ask=ask if ask else None,
        iv=_f(quotes.get("mark_iv")) or _f(msg.get("mark_vol")),
        delta=_f(greeks.get("delta")), gamma=_f(greeks.get("gamma")), theta=_f(greeks.get("theta")), vega=_f(greeks.get("vega")),
        oi=_f(msg.get("oi")) or 0.0, oi_usd=_f(msg.get("oi_value_usd")) or 0.0,
        volume=_f(msg.get("volume")) or 0.0, turnover_usd=_f(msg.get("turnover_usd")) or 0.0,
        spot=_f(msg.get("spot_price")) or _f(greeks.get("spot")),
        contract_value=_f(msg.get("contract_value")) or (0.01 if underlying == "ETH" else 0.001),
        at=time.time() if now is None else now,
    )


# -- chain analytics ------------------------------------------------------------------------


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 3) if b > 0 else None


def atm_strike(quotes: list[OptionQuote], spot: float) -> float | None:
    strikes = {q.strike for q in quotes}
    return min(strikes, key=lambda k: abs(k - spot)) if strikes else None


def atm_iv(quotes: list[OptionQuote], spot: float) -> float | None:
    """Mean mark IV of the call and put at the strike nearest spot."""
    strike = atm_strike([q for q in quotes if q.iv], spot)
    ivs = [q.iv for q in quotes if q.strike == strike and q.iv]
    return sum(ivs) / len(ivs) if ivs else None


def iv_at_delta(quotes: list[OptionQuote], kind: str, target: float) -> float | None:
    side = [q for q in quotes if q.kind == kind and q.delta is not None and q.iv]
    if not side:
        return None
    best = min(side, key=lambda q: abs(abs(q.delta or 0) - target))
    return best.iv if abs(abs(best.delta or 0) - target) <= 0.12 else None


def max_pain(quotes: list[OptionQuote]) -> float | None:
    """The settlement price at which option holders, in total, are paid the least."""
    strikes = sorted({q.strike for q in quotes})
    if not strikes:
        return None

    def payout(settle: float) -> float:
        return sum((max(0.0, settle - q.strike) if q.kind == "call" else max(0.0, q.strike - settle)) * q.oi for q in quotes)

    return min(strikes, key=payout)


def expiry_summary(quotes: list[OptionQuote], spot: float, now: float) -> dict[str, Any]:
    """One expiry's numbers. The implied move is the 1-sigma move to expiry the ATM IV prices."""
    calls = [q for q in quotes if q.kind == "call"]
    puts = [q for q in quotes if q.kind == "put"]
    expiry = quotes[0].expiry
    hours = max(0.0, (expiry - now) / 3600)
    iv = atm_iv(quotes, spot)
    strike = atm_strike(quotes, spot)
    legs = [q.mark for q in quotes if q.strike == strike and q.mark is not None]
    straddle = sum(legs) if len(legs) == 2 else None
    move = spot * iv * math.sqrt(hours / YEAR_HOURS) if iv and hours > 0 else None
    put25, call25 = iv_at_delta(quotes, "put", 0.25), iv_at_delta(quotes, "call", 0.25)
    call_oi, put_oi = sum(q.oi for q in calls), sum(q.oi for q in puts)
    return {
        "expiry": expiry,
        "label": datetime.fromtimestamp(expiry, UTC).strftime("%d %b"),
        "hours": round(hours, 2),
        "contracts": len(quotes),
        "atm_strike": strike,
        "atm_iv": round(iv, 4) if iv else None,
        "skew_25d": round(put25 - call25, 4) if put25 and call25 else None,  # + = puts dearer than calls
        "call_oi": round(call_oi, 4),
        "put_oi": round(put_oi, 4),
        "pcr_oi": _ratio(put_oi, call_oi),
        "pcr_volume": _ratio(sum(q.volume for q in puts), sum(q.volume for q in calls)),
        "max_pain": max_pain(quotes),
        "call_wall": max(calls, key=lambda q: q.oi).strike if calls and max(q.oi for q in calls) > 0 else None,
        "put_wall": max(puts, key=lambda q: q.oi).strike if puts and max(q.oi for q in puts) > 0 else None,
        "straddle": round(straddle, 2) if straddle else None,
        "implied_move": round(move, 2) if move else None,
        "implied_move_pct": round(100 * move / spot, 3) if move else None,
    }


def by_expiry(quotes: list[OptionQuote], now: float | None = None) -> dict[float, list[OptionQuote]]:
    now = time.time() if now is None else now
    out: dict[float, list[OptionQuote]] = {}
    for q in quotes:
        if q.expiry > now:
            out.setdefault(q.expiry, []).append(q)
    return dict(sorted(out.items()))


def chain_rows(quotes: list[OptionQuote], spot: float, width: float = CHAIN_WIDTH) -> list[dict[str, Any]]:
    """Strikes near spot with the call and the put side by side."""
    rows: dict[float, dict[str, Any]] = {}
    for q in quotes:
        if abs(q.strike - spot) > width * spot:
            continue
        rows.setdefault(q.strike, {"strike": q.strike})[q.kind] = {
            "symbol": q.symbol, "mark": q.mark, "bid": q.bid, "ask": q.ask, "iv": q.iv, "delta": q.delta,
            "gamma": q.gamma, "theta": q.theta, "vega": q.vega, "oi": q.oi, "volume": q.volume,
        }
    return [rows[k] for k in sorted(rows)]


def pick_expiry(quotes: list[OptionQuote], min_hours: float, max_hours: float, now: float | None = None) -> float | None:
    """The nearest expiry at least `min_hours` away and at most `max_hours`."""
    now = time.time() if now is None else now
    for expiry in by_expiry(quotes, now):
        hours = (expiry - now) / 3600
        if min_hours <= hours <= max_hours:
            return expiry
    return None


def summarize(underlying: str, quotes: list[OptionQuote], spot: float | None, now: float | None = None) -> dict[str, Any] | None:
    """The whole book for one underlying: totals, per-expiry numbers, term structure, near-spot
    chains. The shape the analysis modules (insights, nearby, verdict) read."""
    now = time.time() if now is None else now
    live = [q for q in quotes if q.underlying == underlying and q.expiry > now]
    if not live:
        return None
    spot = spot or next((q.spot for q in live if q.spot), None)
    if not spot:
        return None
    grouped = by_expiry(live, now)
    expiries = [expiry_summary(qs, spot, now) for qs in grouped.values()]
    call_oi = sum(q.oi for q in live if q.kind == "call")
    put_oi = sum(q.oi for q in live if q.kind == "put")
    # The nearest expiry at least 20h out answers "what does the market expect over the next day".
    day = next((e for e in expiries if e["hours"] >= 20), expiries[-1])
    return {
        "underlying": underlying,
        "spot": spot,
        "at": now,
        "contracts": len(live),
        "call_oi": round(call_oi, 4),
        "put_oi": round(put_oi, 4),
        "pcr_oi": _ratio(put_oi, call_oi),
        "pcr_volume": _ratio(sum(q.volume for q in live if q.kind == "put"), sum(q.volume for q in live if q.kind == "call")),
        "oi_usd": round(sum(q.oi_usd for q in live), 2),
        "turnover_usd": round(sum(q.turnover_usd for q in live), 2),
        "nearest": expiries[0],
        "day": day,
        "term": [{"label": e["label"], "hours": e["hours"], "atm_iv": e["atm_iv"]} for e in expiries],
        "expiries": expiries[:CHAIN_EXPIRIES],
        "chains": {str(int(e["expiry"])): chain_rows(grouped[e["expiry"]], spot) for e in expiries[:CHAIN_EXPIRIES]},
    }


def history_row(summary: dict[str, Any]) -> dict[str, Any]:
    """The few numbers worth keeping every minute: the ATM IV and put/call charts, and what
    `pcr_options` compares the book with."""
    day, near = summary["day"], summary["nearest"]
    return {
        "underlying": summary["underlying"], "ts": summary["at"], "spot": summary["spot"],
        "atm_iv": day["atm_iv"], "near_atm_iv": near["atm_iv"], "skew_25d": day["skew_25d"],
        "pcr_oi": summary["pcr_oi"], "pcr_volume": summary["pcr_volume"],
        "call_oi": summary["call_oi"], "put_oi": summary["put_oi"],
        "max_pain": near["max_pain"], "implied_move_pct": day["implied_move_pct"],
    }
