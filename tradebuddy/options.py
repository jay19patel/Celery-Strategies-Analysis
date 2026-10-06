"""Options chain -> one snapshot per underlying.

Delta pushes every option's ticker on the WebSocket (v2/ticker, symbols "call_options" and
"put_options": ~900 contracts, ~200 messages a second). The feed keeps the latest quote per
contract in an `OptionsBook` and publishes one `OptionsSnapshot` per underlying every few
seconds; nothing downstream sees the raw firehose. When the socket has sent nothing for an
underlying for a minute, the same snapshot is built from one REST call instead.

Everything here is pure arithmetic on quotes, so it is tested without a network.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

EXPIRY_HOUR_UTC = 12  # Delta India BTC/ETH options settle at 12:00 UTC (17:30 IST)
YEAR_SECONDS = 365 * 86_400
UNDERLYINGS = {"BTCUSD": "BTC", "ETHUSD": "ETH"}  # perpetual -> option underlying
CHAIN_WIDTH = 0.12  # strikes within +/-12% of spot go to the dashboard chain
CHAIN_EXPIRIES = 4  # nearest expiries sent with their chain


@dataclass(frozen=True)
class OptionQuote:
    symbol: str  # C-BTC-110000-091026
    underlying: str  # BTC
    kind: str  # "call" | "put"
    strike: float
    expiry: float  # unix seconds of settlement
    mark: float | None = None
    bid: float | None = None
    ask: float | None = None
    mark_iv: float | None = None  # annualised, as a fraction: 0.45 = 45%
    bid_iv: float | None = None
    ask_iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    oi: float = 0.0  # in the underlying (BTC)
    oi_usd: float = 0.0
    volume: float = 0.0  # 24h, in the underlying
    turnover_usd: float = 0.0  # 24h
    spot: float | None = None
    at: float = 0.0  # when we received it


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
    """One option ticker, from the WebSocket or REST /v2/tickers (same shape)."""
    parsed = parse_symbol(msg.get("symbol", ""))
    if parsed is None:
        return None
    kind, underlying, strike, expiry = parsed
    quotes, greeks = msg.get("quotes") or {}, msg.get("greeks") or {}
    return OptionQuote(
        symbol=msg["symbol"], underlying=underlying, kind=kind, strike=strike, expiry=expiry,
        mark=_f(msg.get("mark_price")), bid=_f(quotes.get("best_bid")), ask=_f(quotes.get("best_ask")),
        mark_iv=_f(quotes.get("mark_iv")), bid_iv=_f(quotes.get("bid_iv")), ask_iv=_f(quotes.get("ask_iv")),
        delta=_f(greeks.get("delta")), gamma=_f(greeks.get("gamma")), vega=_f(greeks.get("vega")), theta=_f(greeks.get("theta")),
        oi=_f(msg.get("oi")) or 0.0, oi_usd=_f(msg.get("oi_value_usd")) or 0.0,
        volume=_f(msg.get("volume")) or 0.0, turnover_usd=_f(msg.get("turnover_usd")) or 0.0,
        spot=_f(msg.get("spot_price")) or _f(greeks.get("spot")), at=time.time() if now is None else now,
    )


# -- analytics ------------------------------------------------------------------


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 3) if b > 0 else None


def _atm_iv(quotes: list[OptionQuote], spot: float) -> tuple[float | None, float | None]:
    """IV at the strike nearest spot: the mean of its call and put mark IVs. Returns (strike, iv)."""
    by_strike: dict[float, list[float]] = {}
    for q in quotes:
        if q.mark_iv and q.mark_iv > 0:
            by_strike.setdefault(q.strike, []).append(q.mark_iv)
    if not by_strike:
        return None, None
    strike = min(by_strike, key=lambda k: abs(k - spot))
    ivs = by_strike[strike]
    return strike, sum(ivs) / len(ivs)


def _iv_at_delta(quotes: list[OptionQuote], kind: str, target: float) -> float | None:
    """Mark IV of the option whose |delta| is nearest `target` (0.25 -> the 25-delta wing)."""
    side = [q for q in quotes if q.kind == kind and q.delta is not None and q.mark_iv]
    if not side:
        return None
    best = min(side, key=lambda q: abs(abs(q.delta or 0) - target))
    return best.mark_iv if abs(abs(best.delta or 0) - target) <= 0.12 else None


def max_pain(quotes: list[OptionQuote]) -> float | None:
    """The settlement price at which option holders, in total, are paid the least."""
    strikes = sorted({q.strike for q in quotes})
    if not strikes:
        return None

    def payout(settle: float) -> float:
        total = 0.0
        for q in quotes:
            intrinsic = max(0.0, settle - q.strike) if q.kind == "call" else max(0.0, q.strike - settle)
            total += intrinsic * q.oi
        return total

    return min(strikes, key=payout)


def expiry_summary(quotes: list[OptionQuote], spot: float, now: float) -> dict[str, Any]:
    """One expiry's numbers: ATM IV, skew, put/call ratios, max pain, walls, expected move."""
    calls = [q for q in quotes if q.kind == "call"]
    puts = [q for q in quotes if q.kind == "put"]
    expiry = quotes[0].expiry
    hours = max(0.0, (expiry - now) / 3600)
    atm_strike, atm_iv = _atm_iv(quotes, spot)
    call_oi, put_oi = sum(q.oi for q in calls), sum(q.oi for q in puts)
    call_vol, put_vol = sum(q.volume for q in calls), sum(q.volume for q in puts)
    put25, call25 = _iv_at_delta(quotes, "put", 0.25), _iv_at_delta(quotes, "call", 0.25)
    straddle = None
    if atm_strike is not None:
        legs = [q.mark for q in quotes if q.strike == atm_strike and q.mark is not None]
        straddle = sum(legs) if len(legs) == 2 else None
    # 1-sigma move to expiry implied by the ATM IV
    implied_move = spot * atm_iv * math.sqrt(hours * 3600 / YEAR_SECONDS) if atm_iv and hours > 0 else None
    call_wall = max(calls, key=lambda q: q.oi).strike if calls and max(q.oi for q in calls) > 0 else None
    put_wall = max(puts, key=lambda q: q.oi).strike if puts and max(q.oi for q in puts) > 0 else None
    return {
        "expiry": expiry,
        "label": datetime.fromtimestamp(expiry, UTC).strftime("%d %b"),
        "hours": round(hours, 2),
        "contracts": len(quotes),
        "atm_strike": atm_strike,
        "atm_iv": round(atm_iv, 4) if atm_iv else None,
        "skew_25d": round(put25 - call25, 4) if put25 and call25 else None,  # + = puts dearer than calls
        "call_oi": round(call_oi, 4), "put_oi": round(put_oi, 4),
        "pcr_oi": _ratio(put_oi, call_oi), "pcr_volume": _ratio(put_vol, call_vol),
        "call_volume": round(call_vol, 4), "put_volume": round(put_vol, 4),
        "max_pain": max_pain(quotes),
        "call_wall": call_wall, "put_wall": put_wall,
        "straddle": round(straddle, 2) if straddle else None,
        "straddle_pct": round(100 * straddle / spot, 3) if straddle else None,
        "implied_move": round(implied_move, 2) if implied_move else None,
        "implied_move_pct": round(100 * implied_move / spot, 3) if implied_move else None,
    }


def chain_rows(quotes: list[OptionQuote], spot: float, width: float = CHAIN_WIDTH) -> list[dict[str, Any]]:
    """Strikes near spot with the call and put side by side, for the dashboard."""
    rows: dict[float, dict[str, Any]] = {}
    for q in quotes:
        if abs(q.strike - spot) > width * spot:
            continue
        side = {
            "symbol": q.symbol, "mark": q.mark, "bid": q.bid, "ask": q.ask, "iv": q.mark_iv, "delta": q.delta,
            "gamma": q.gamma, "theta": q.theta, "vega": q.vega, "oi": q.oi, "oi_usd": q.oi_usd, "volume": q.volume,
        }
        rows.setdefault(q.strike, {"strike": q.strike})[q.kind] = side
    return [rows[k] for k in sorted(rows)]


def summarize(underlying: str, quotes: list[OptionQuote], spot: float | None, now: float | None = None) -> dict[str, Any] | None:
    """The whole book for one underlying: totals, per-expiry numbers, term structure, near-spot chains."""
    now = time.time() if now is None else now
    live = [q for q in quotes if q.underlying == underlying and q.expiry > now]
    if not live:
        return None
    spot = spot or next((q.spot for q in live if q.spot), None)
    if not spot:
        return None
    by_expiry: dict[float, list[OptionQuote]] = {}
    for q in live:
        by_expiry.setdefault(q.expiry, []).append(q)
    expiries = [expiry_summary(by_expiry[e], spot, now) for e in sorted(by_expiry)]
    call_oi = sum(q.oi for q in live if q.kind == "call")
    put_oi = sum(q.oi for q in live if q.kind == "put")
    # The nearest expiry at least 20h out answers "what does the market expect over the next day".
    day = next((e for e in expiries if e["hours"] >= 20), expiries[-1])
    return {
        "underlying": underlying,
        "spot": spot,
        "at": now,
        "contracts": len(live),
        "call_oi": round(call_oi, 4), "put_oi": round(put_oi, 4),
        "pcr_oi": _ratio(put_oi, call_oi),
        "oi_usd": round(sum(q.oi_usd for q in live), 2),
        "turnover_usd": round(sum(q.turnover_usd for q in live), 2),
        "pcr_volume": _ratio(sum(q.volume for q in live if q.kind == "put"), sum(q.volume for q in live if q.kind == "call")),
        "nearest": expiries[0],
        "day": day,
        "term": [{"label": e["label"], "hours": e["hours"], "atm_iv": e["atm_iv"]} for e in expiries],
        "expiries": expiries[:CHAIN_EXPIRIES],
        "chains": {str(int(e["expiry"])): chain_rows(by_expiry[e["expiry"]], spot) for e in expiries[:CHAIN_EXPIRIES]},
    }


def history_row(summary: dict[str, Any]) -> dict[str, Any]:
    """The few numbers worth keeping every minute, for charts and for training models later."""
    day, near = summary["day"], summary["nearest"]
    return {
        "underlying": summary["underlying"], "ts": summary["at"], "spot": summary["spot"],
        "atm_iv": day["atm_iv"], "near_atm_iv": near["atm_iv"], "skew_25d": day["skew_25d"],
        "pcr_oi": summary["pcr_oi"], "pcr_volume": summary["pcr_volume"],
        "call_oi": summary["call_oi"], "put_oi": summary["put_oi"], "oi_usd": summary["oi_usd"],
        "turnover_usd": summary["turnover_usd"], "max_pain": near["max_pain"], "implied_move_pct": day["implied_move_pct"],
    }


# -- the live book ----------------------------------------------------------------


class OptionsBook:
    """Latest quote per option contract, for the underlyings we care about."""

    def __init__(self, underlyings: set[str]) -> None:
        self.underlyings = underlyings
        self.quotes: dict[str, OptionQuote] = {}
        self.updated: dict[str, float] = {}  # underlying -> last WebSocket update
        self.messages = 0

    def observe(self, msg: dict[str, Any]) -> bool:
        """A WebSocket ticker. False if it is not an option on one of our underlyings."""
        quote = parse_ticker(msg)
        if quote is None or quote.underlying not in self.underlyings:
            return False
        self.quotes[quote.symbol] = quote
        self.updated[quote.underlying] = quote.at
        self.messages += 1
        return True

    def replace(self, underlying: str, quotes: list[OptionQuote]) -> None:
        """A full chain from REST: contracts it no longer lists are gone (expired, delisted)."""
        for symbol in [s for s, q in self.quotes.items() if q.underlying == underlying]:
            del self.quotes[symbol]
        for q in quotes:
            self.quotes[q.symbol] = q

    def for_underlying(self, underlying: str) -> list[OptionQuote]:
        return [q for q in self.quotes.values() if q.underlying == underlying]

    def age(self, underlying: str, now: float | None = None) -> float | None:
        at = self.updated.get(underlying)
        return None if at is None else (time.time() if now is None else now) - at


@dataclass
class OptionsFeedStatus:
    source: dict[str, str]  # underlying -> "websocket" | "rest" | "none"
    snapshots: int = 0
    rest_calls: int = 0
    last_error: str = ""
    ws_messages: int = 0


class OptionsFeed:
    """Publishes an OptionsSnapshot per underlying every `every` seconds. The WebSocket book is
    used while it is fresh; after `ws_stale` seconds of silence it falls back to REST, at most
    once per `rest_every` seconds per underlying."""

    def __init__(
        self, book: OptionsBook, publish: Callable[[Any], None], fetch: Callable[[str], Any],
        spot: Callable[[str], float | None], every: float = 15.0, ws_stale: float = 60.0, rest_every: float = 60.0,
    ) -> None:
        self.book = book
        self.publish = publish
        self.fetch = fetch  # async (underlying) -> list[OptionQuote]
        self.spot = spot  # perpetual symbol -> latest price, or None
        self.every, self.ws_stale, self.rest_every = every, ws_stale, rest_every
        self._rest_at: dict[str, float] = {}
        # The socket sends contracts one at a time, so a book it alone has filled may be missing most of
        # the chain. Each underlying is seeded with one full REST chain before the socket is trusted.
        self._seeded: set[str] = set()
        self.status = OptionsFeedStatus(source=dict.fromkeys(book.underlyings, "none"))

    async def run(self) -> None:
        from tradebuddy.jobs import NULL_JOB

        job = getattr(self, "job", NULL_JOB)
        while True:
            try:
                with job.tick():
                    await self.tick()
            except Exception:
                log.exception("options_tick_failed")
            await asyncio.sleep(self.every)

    async def tick(self, now: float | None = None) -> None:
        from tradebuddy.events import OptionsSnapshot  # events imports nothing from here; keep it one-way

        now = time.time() if now is None else now
        for perp, underlying in UNDERLYINGS.items():
            if underlying not in self.book.underlyings:
                continue
            age = self.book.age(underlying, now)
            source = "websocket"
            if underlying not in self._seeded or age is None or age > self.ws_stale:
                if now - self._rest_at.get(underlying, 0) < self.rest_every:
                    continue  # stale socket, REST used recently: the last snapshot still stands
                self._rest_at[underlying] = now
                self.status.rest_calls += 1
                try:
                    self.book.replace(underlying, await self.fetch(underlying))
                    self._seeded.add(underlying)
                except Exception as exc:
                    self.status.last_error = f"{underlying}: {type(exc).__name__}: {exc}"[:300]
                    log.warning("options_rest_failed underlying=%s error=%s", underlying, exc)
                    continue
                source = "rest"
            summary = summarize(underlying, self.book.for_underlying(underlying), self.spot(perp), now)
            if summary is None:
                continue
            self.status.source[underlying] = source
            self.status.snapshots += 1
            self.status.ws_messages = self.book.messages
            self.publish(OptionsSnapshot(symbol=perp, underlying=underlying, source=source, summary=summary))

    def stats(self) -> dict[str, Any]:
        return asdict(self.status)
