"""Iron Condor and non-directional options strategy.

Harvests daily theta decay and volatility risk premium on crypto options.
Focuses on defined-risk setups (Iron Condor) where max loss is strictly bounded,
allowing safe position sizing without black swan blowout risk.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from tradebuddy.options import OptionQuote
from tradebuddy.strategies.base import Context, Signal, Strategy


@dataclass(frozen=True)
class OptionLeg:
    symbol: str
    kind: str  # "call" | "put"
    action: str  # "buy" | "sell"
    strike: float
    expiry: float
    mark: float
    delta: float | None = None
    iv: float | None = None
    oi: float = 0.0


@dataclass(frozen=True)
class IronCondorSetup:
    underlying: str
    spot: float
    expiry: float
    short_call: OptionLeg
    long_call: OptionLeg
    short_put: OptionLeg
    long_put: OptionLeg
    net_credit: float
    max_loss: float
    call_spread_width: float
    put_spread_width: float
    lower_breakeven: float
    upper_breakeven: float
    buffer_pct: float  # distance to nearest short strike as % of spot


@dataclass(frozen=True)
class StraddleSetup:
    underlying: str
    spot: float
    expiry: float
    atm_strike: float
    call_leg: OptionLeg
    put_leg: OptionLeg
    total_credit: float
    max_loss: float
    lower_breakeven: float
    upper_breakeven: float


@dataclass(frozen=True)
class StrangleSetup:
    underlying: str
    spot: float
    expiry: float
    short_call: OptionLeg
    short_put: OptionLeg
    total_credit: float
    max_loss: float
    lower_breakeven: float
    upper_breakeven: float


@dataclass(frozen=True)
class CallSpreadSetup:
    underlying: str
    spot: float
    expiry: float
    long_call: OptionLeg
    short_call: OptionLeg
    net_debit: float
    max_profit: float
    max_loss: float
    breakeven: float
    spread_width: float


@dataclass(frozen=True)
class PutSpreadSetup:
    underlying: str
    spot: float
    expiry: float
    long_put: OptionLeg
    short_put: OptionLeg
    net_debit: float
    max_profit: float
    max_loss: float
    breakeven: float
    spread_width: float


def select_iron_condor(
    quotes: list[OptionQuote],
    spot: float,
    target_delta: float = 0.18,
    wing_distance_pct: float = 0.015,
) -> IronCondorSetup | None:
    """Finds optimal 15-20 delta short legs and protective outer wings."""
    if not quotes or spot <= 0:
        return None

    calls = [q for q in quotes if q.kind == "call" and q.strike >= spot and q.mark and q.mark > 0]
    puts = [q for q in quotes if q.kind == "put" and q.strike <= spot and q.mark and q.mark > 0]
    if not calls or not puts:
        return None

    def pick_short(side_quotes: list[OptionQuote], delta_target: float) -> OptionQuote | None:
        valid = [q for q in side_quotes if q.delta is not None]
        if not valid:
            return None
        return min(valid, key=lambda q: abs(abs(q.delta or 0.0) - delta_target))

    short_c = pick_short(calls, target_delta)
    short_p = pick_short(puts, target_delta)
    if not short_c or not short_p:
        return None

    # Wing selection: outer strikes
    wing_offset = spot * wing_distance_pct
    outer_calls = [q for q in calls if q.strike > short_c.strike and q.mark]
    outer_puts = [q for q in puts if q.strike < short_p.strike and q.mark]
    if not outer_calls or not outer_puts:
        return None

    long_c = min(outer_calls, key=lambda q: abs((q.strike - short_c.strike) - wing_offset))
    long_p = min(outer_puts, key=lambda q: abs((short_p.strike - q.strike) - wing_offset))

    # Pricing calculations
    net_credit = (short_c.mark + short_p.mark) - (long_c.mark + long_p.mark)
    if net_credit <= 0:
        return None

    call_spread = long_c.strike - short_c.strike
    put_spread = short_p.strike - long_p.strike
    max_risk_width = max(call_spread, put_spread)
    max_loss = max(0.0, max_risk_width - net_credit)

    lower_be = short_p.strike - net_credit
    upper_be = short_c.strike + net_credit
    buffer_pct = min(short_c.strike - spot, spot - short_p.strike) / spot * 100

    def to_leg(q: OptionQuote, action: str) -> OptionLeg:
        return OptionLeg(
            symbol=q.symbol, kind=q.kind, action=action, strike=q.strike,
            expiry=q.expiry, mark=q.mark or 0.0, delta=q.delta, iv=q.mark_iv, oi=q.oi,
        )

    return IronCondorSetup(
        underlying=short_c.underlying,
        spot=spot,
        expiry=short_c.expiry,
        short_call=to_leg(short_c, "sell"),
        long_call=to_leg(long_c, "buy"),
        short_put=to_leg(short_p, "sell"),
        long_put=to_leg(long_p, "buy"),
        net_credit=round(net_credit, 2),
        max_loss=round(max_loss, 2),
        call_spread_width=round(call_spread, 2),
        put_spread_width=round(put_spread, 2),
        lower_breakeven=round(lower_be, 2),
        upper_breakeven=round(upper_be, 2),
        buffer_pct=round(buffer_pct, 2),
    )


def select_straddle(quotes: list[OptionQuote], spot: float) -> StraddleSetup | None:
    """Finds ATM Call and ATM Put for the selected expiry."""
    if not quotes or spot <= 0:
        return None
    strikes = sorted({q.strike for q in quotes if q.mark and q.mark > 0})
    if not strikes:
        return None
    atm_strike = min(strikes, key=lambda s: abs(s - spot))
    call = next((q for q in quotes if q.kind == "call" and q.strike == atm_strike and q.mark), None)
    put = next((q for q in quotes if q.kind == "put" and q.strike == atm_strike and q.mark), None)
    if not call or not put:
        return None

    credit = (call.mark or 0.0) + (put.mark or 0.0)

    def to_leg(q: OptionQuote, action: str) -> OptionLeg:
        return OptionLeg(
            symbol=q.symbol, kind=q.kind, action=action, strike=q.strike,
            expiry=q.expiry, mark=q.mark or 0.0, delta=q.delta, iv=q.mark_iv, oi=q.oi,
        )

    return StraddleSetup(
        underlying=call.underlying,
        spot=spot,
        expiry=call.expiry,
        atm_strike=atm_strike,
        call_leg=to_leg(call, "sell"),
        put_leg=to_leg(put, "sell"),
        total_credit=round(credit, 2),
        max_loss=round(credit * 1.5, 2),
        lower_breakeven=round(atm_strike - credit, 2),
        upper_breakeven=round(atm_strike + credit, 2),
    )


def select_strangle(
    quotes: list[OptionQuote],
    spot: float,
    target_delta: float = 0.20,
) -> StrangleSetup | None:
    """Finds OTM Call and OTM Put at target delta."""
    if not quotes or spot <= 0:
        return None
    calls = [q for q in quotes if q.kind == "call" and q.strike >= spot and q.mark and q.delta is not None]
    puts = [q for q in quotes if q.kind == "put" and q.strike <= spot and q.mark and q.delta is not None]
    if not calls or not puts:
        return None

    short_c = min(calls, key=lambda q: abs(abs(q.delta or 0.0) - target_delta))
    short_p = min(puts, key=lambda q: abs(abs(q.delta or 0.0) - target_delta))
    credit = (short_c.mark or 0.0) + (short_p.mark or 0.0)

    def to_leg(q: OptionQuote, action: str) -> OptionLeg:
        return OptionLeg(
            symbol=q.symbol, kind=q.kind, action=action, strike=q.strike,
            expiry=q.expiry, mark=q.mark or 0.0, delta=q.delta, iv=q.mark_iv, oi=q.oi,
        )

    return StrangleSetup(
        underlying=short_c.underlying,
        spot=spot,
        expiry=short_c.expiry,
        short_call=to_leg(short_c, "sell"),
        short_put=to_leg(short_p, "sell"),
        total_credit=round(credit, 2),
        max_loss=round(credit * 1.5, 2),
        lower_breakeven=round(short_p.strike - credit, 2),
        upper_breakeven=round(short_c.strike + credit, 2),
    )


def select_call_spread(
    quotes: list[OptionQuote],
    spot: float,
    short_delta_target: float = 0.25,
) -> CallSpreadSetup | None:
    """Finds ATM long call and ~25-delta short call (bull call spread)."""
    if not quotes or spot <= 0:
        return None
    calls = [q for q in quotes if q.kind == "call" and q.mark and q.mark > 0]
    if not calls:
        return None
    strikes = sorted({q.strike for q in calls})
    atm_strike = min(strikes, key=lambda s: abs(s - spot))
    long_c = next((q for q in calls if q.strike == atm_strike), None)
    if not long_c:
        return None
    otm_calls = [q for q in calls if q.strike > long_c.strike and q.delta is not None]
    if not otm_calls:
        return None
    short_c = min(otm_calls, key=lambda q: abs(abs(q.delta or 0.0) - short_delta_target))
    net_debit = (long_c.mark or 0.0) - (short_c.mark or 0.0)
    if net_debit <= 0:
        return None
    spread_width = short_c.strike - long_c.strike
    max_profit = max(0.0, spread_width - net_debit)
    max_loss = net_debit
    breakeven = long_c.strike + net_debit

    def to_leg(q: OptionQuote, action: str) -> OptionLeg:
        return OptionLeg(
            symbol=q.symbol, kind=q.kind, action=action, strike=q.strike,
            expiry=q.expiry, mark=q.mark or 0.0, delta=q.delta, iv=q.mark_iv, oi=q.oi,
        )

    return CallSpreadSetup(
        underlying=long_c.underlying,
        spot=spot,
        expiry=long_c.expiry,
        long_call=to_leg(long_c, "buy"),
        short_call=to_leg(short_c, "sell"),
        net_debit=round(net_debit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven=round(breakeven, 2),
        spread_width=round(spread_width, 2),
    )


def select_put_spread(
    quotes: list[OptionQuote],
    spot: float,
    short_delta_target: float = 0.25,
) -> PutSpreadSetup | None:
    """Finds ATM long put and ~25-delta short put (bear put spread)."""
    if not quotes or spot <= 0:
        return None
    puts = [q for q in quotes if q.kind == "put" and q.mark and q.mark > 0]
    if not puts:
        return None
    strikes = sorted({q.strike for q in puts})
    atm_strike = min(strikes, key=lambda s: abs(s - spot))
    long_p = next((q for q in puts if q.strike == atm_strike), None)
    if not long_p:
        return None
    otm_puts = [q for q in puts if q.strike < long_p.strike and q.delta is not None]
    if not otm_puts:
        return None
    short_p = min(otm_puts, key=lambda q: abs(abs(q.delta or 0.0) - short_delta_target))
    net_debit = (long_p.mark or 0.0) - (short_p.mark or 0.0)
    if net_debit <= 0:
        return None
    spread_width = long_p.strike - short_p.strike
    max_profit = max(0.0, spread_width - net_debit)
    max_loss = net_debit
    breakeven = long_p.strike - net_debit

    def to_leg(q: OptionQuote, action: str) -> OptionLeg:
        return OptionLeg(
            symbol=q.symbol, kind=q.kind, action=action, strike=q.strike,
            expiry=q.expiry, mark=q.mark or 0.0, delta=q.delta, iv=q.mark_iv, oi=q.oi,
        )

    return PutSpreadSetup(
        underlying=long_p.underlying,
        spot=spot,
        expiry=long_p.expiry,
        long_put=to_leg(long_p, "buy"),
        short_put=to_leg(short_p, "sell"),
        net_debit=round(net_debit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven=round(breakeven, 2),
        spread_width=round(spread_width, 2),
    )


class IronCondorStrategy(Strategy):
    """Monitors options chain and highlights daily theta-decay setups."""

    name = "iron_condor_daily"
    symbols = ["BTCUSD"]
    interval = "15m"
    underlying = "BTC"
    is_default_sl_tp = True

    async def on_candle(self, context: Context) -> Signal | None:
        # Non-directional options setups are executed through the Options Desk
        # rather than single directional perp signals.
        return None
