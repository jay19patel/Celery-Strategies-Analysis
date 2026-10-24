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
class LongStraddleSetup:
    underlying: str
    spot: float
    expiry: float
    atm_strike: float
    call_leg: OptionLeg
    put_leg: OptionLeg
    net_debit: float
    max_profit: str  # "Unlimited"
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


@dataclass(frozen=True)
class DailyRegimeAnalysis:
    """1-Day Big Timeframe market regime analysis and strategy suitability ranking."""

    timeframe: str  # "1-Day (24h)"
    spot: float
    trend: str  # "Range-Bound Consolidation" | "Bullish Momentum" | "Bearish Momentum"
    daily_rsi: float
    daily_ema20: float
    daily_atr: float
    daily_atr_pct: float
    change_24h_pct: float
    atm_iv: float
    realized_vol_24h: float
    vol_spread: float  # (atm_iv - realized_vol_24h) in % points
    vol_verdict: str  # "High Volatility Premium (Seller Edge)" | "Fairly Priced" | "Low Volatility Premium (Buyer Edge)"
    expected_move: float
    expected_low: float
    expected_high: float
    pcr_oi: float | None
    max_pain: float | None
    strategy_scores: dict[str, int]  # percentage suitability 0-100
    top_strategy: str  # strategy with highest score
    top_score: int  # highest percentage score
    decision_rationale: str


def _filter_single_expiry(quotes: list[OptionQuote]) -> list[OptionQuote]:
    """Helper to ensure all quotes belong strictly to a single expiry."""
    if not quotes:
        return []
    expiries = sorted({q.expiry for q in quotes if q.expiry and q.mark and q.mark > 0})
    if not expiries:
        expiries = sorted({q.expiry for q in quotes if q.expiry})
    if not expiries:
        return quotes
    target_exp = expiries[0]
    return [q for q in quotes if q.expiry == target_exp]


def select_iron_condor(
    quotes: list[OptionQuote],
    spot: float,
    target_delta: float = 0.18,
    wing_distance_pct: float = 0.015,
) -> IronCondorSetup | None:
    """Finds optimal 15-20 delta short legs and protective outer wings for a single expiry."""
    quotes = _filter_single_expiry(quotes)
    if not quotes or spot <= 0:
        return None

    calls = [q for q in quotes if q.kind == "call" and q.strike >= spot and q.mark and q.mark > 0]
    puts = [q for q in quotes if q.kind == "put" and q.strike <= spot and q.mark and q.mark > 0]
    if not calls or not puts:
        return None

    def pick_short(side_quotes: list[OptionQuote], delta_target: float) -> OptionQuote | None:
        valid = [q for q in side_quotes if q.delta is not None]
        if not valid:
            return side_quotes[0]
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
    """Finds ATM Call and ATM Put to SELL (Short Straddle) for a single expiry."""
    quotes = _filter_single_expiry(quotes)
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


def select_long_straddle(quotes: list[OptionQuote], spot: float) -> LongStraddleSetup | None:
    """Finds ATM Call and ATM Put to BUY (Long Straddle - Volatility Breakout) for a single expiry."""
    quotes = _filter_single_expiry(quotes)
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

    net_debit = (call.mark or 0.0) + (put.mark or 0.0)
    if net_debit <= 0:
        return None

    def to_leg(q: OptionQuote, action: str) -> OptionLeg:
        return OptionLeg(
            symbol=q.symbol, kind=q.kind, action=action, strike=q.strike,
            expiry=q.expiry, mark=q.mark or 0.0, delta=q.delta, iv=q.mark_iv, oi=q.oi,
        )

    return LongStraddleSetup(
        underlying=call.underlying,
        spot=spot,
        expiry=call.expiry,
        atm_strike=atm_strike,
        call_leg=to_leg(call, "buy"),
        put_leg=to_leg(put, "buy"),
        net_debit=round(net_debit, 2),
        max_profit="Unlimited",
        max_loss=round(net_debit, 2),
        lower_breakeven=round(atm_strike - net_debit, 2),
        upper_breakeven=round(atm_strike + net_debit, 2),
    )


def select_strangle(
    quotes: list[OptionQuote],
    spot: float,
    target_delta: float = 0.20,
) -> StrangleSetup | None:
    """Finds OTM Call and OTM Put to SELL at target delta for a single expiry."""
    quotes = _filter_single_expiry(quotes)
    if not quotes or spot <= 0:
        return None
    calls = [q for q in quotes if q.kind == "call" and q.strike >= spot and q.mark]
    puts = [q for q in quotes if q.kind == "put" and q.strike <= spot and q.mark]
    if not calls or not puts:
        return None

    valid_c = [q for q in calls if q.delta is not None]
    short_c = min(valid_c, key=lambda q: abs(abs(q.delta or 0.0) - target_delta)) if valid_c else min(calls, key=lambda q: abs(q.strike - spot * 1.03))

    valid_p = [q for q in puts if q.delta is not None]
    short_p = min(valid_p, key=lambda q: abs(abs(q.delta or 0.0) - target_delta)) if valid_p else min(puts, key=lambda q: abs(q.strike - spot * 0.97))

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
    """Finds ATM long call and OTM short call (bull call spread) for a single expiry."""
    quotes = _filter_single_expiry(quotes)
    if not quotes or spot <= 0:
        return None
    calls = [q for q in quotes if q.kind == "call" and q.mark and q.mark > 0]
    if not calls:
        return None
    strikes = sorted({q.strike for q in calls})
    atm_strike = min(strikes, key=lambda s: abs(s - spot))
    long_c = next((q for q in calls if q.strike == atm_strike), None)
    if not long_c:
        long_c = calls[0]

    # OTM calls have higher strike
    otm_calls = [q for q in calls if q.strike > long_c.strike]
    if not otm_calls:
        lower_calls = [q for q in calls if q.strike < long_c.strike]
        if lower_calls:
            short_c = long_c
            long_c = max(lower_calls, key=lambda q: q.strike)
            otm_calls = [short_c]
        else:
            return None

    valid_delta = [q for q in otm_calls if q.delta is not None and (q.mark or 0.0) < (long_c.mark or 0.0)]
    if valid_delta:
        short_c = min(valid_delta, key=lambda q: abs(abs(q.delta or 0.0) - short_delta_target))
    else:
        cheaper = [q for q in otm_calls if (q.mark or 0.0) < (long_c.mark or 0.0)]
        short_c = min(cheaper, key=lambda q: q.strike) if cheaper else otm_calls[0]

    net_debit = (long_c.mark or 0.0) - (short_c.mark or 0.0)
    spread_width = short_c.strike - long_c.strike
    if spread_width <= 0 or net_debit <= 0:
        return None

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
    """Finds ATM long put and OTM short put (bear put spread) for a single expiry."""
    quotes = _filter_single_expiry(quotes)
    if not quotes or spot <= 0:
        return None
    puts = [q for q in quotes if q.kind == "put" and q.mark and q.mark > 0]
    if not puts:
        return None
    strikes = sorted({q.strike for q in puts})
    atm_strike = min(strikes, key=lambda s: abs(s - spot))
    long_p = next((q for q in puts if q.strike == atm_strike), None)
    if not long_p:
        long_p = puts[0]

    otm_puts = [q for q in puts if q.strike < long_p.strike]
    if not otm_puts:
        higher_puts = [q for q in puts if q.strike > long_p.strike]
        if higher_puts:
            short_p = long_p
            long_p = min(higher_puts, key=lambda q: q.strike)
            otm_puts = [short_p]
        else:
            return None

    valid_delta = [q for q in otm_puts if q.delta is not None and (q.mark or 0.0) < (long_p.mark or 0.0)]
    if valid_delta:
        short_p = min(valid_delta, key=lambda q: abs(abs(q.delta or 0.0) - short_delta_target))
    else:
        cheaper = [q for q in otm_puts if (q.mark or 0.0) < (long_p.mark or 0.0)]
        short_p = max(cheaper, key=lambda q: q.strike) if cheaper else otm_puts[0]

    net_debit = (long_p.mark or 0.0) - (short_p.mark or 0.0)
    spread_width = long_p.strike - short_p.strike
    if spread_width <= 0 or net_debit <= 0:
        return None

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


def build_all_setups(quotes: list[OptionQuote], spot: float) -> dict[str, Any]:
    """Builds setups for all 6 supported strategies from a list of option quotes."""
    from dataclasses import asdict
    condor = select_iron_condor(quotes, spot) if spot and quotes else None
    straddle = select_straddle(quotes, spot) if spot and quotes else None
    long_straddle = select_long_straddle(quotes, spot) if spot and quotes else None
    strangle = select_strangle(quotes, spot) if spot and quotes else None
    call_spread = select_call_spread(quotes, spot) if spot and quotes else None
    put_spread = select_put_spread(quotes, spot) if spot and quotes else None
    return {
        "iron_condor": asdict(condor) if condor else None,
        "straddle": asdict(straddle) if straddle else None,
        "long_straddle": asdict(long_straddle) if long_straddle else None,
        "strangle": asdict(strangle) if strangle else None,
        "call_spread": asdict(call_spread) if call_spread else None,
        "put_spread": asdict(put_spread) if put_spread else None,
    }


def analyze_daily_regime(
    spot: float,
    candles_15m: list[dict[str, Any]] | list[Any] | None = None,
    stats: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    setups: dict[str, Any] | None = None,
) -> DailyRegimeAnalysis:
    """Analyzes market on a 1-Day timeframe and generates suitability scores for all 5 strategies.

    Examines:
    1. Daily Trend & Momentum: 14-day Daily RSI, 20-day Daily EMA, and 24h % change.
    2. Volatility Spread: ATM Implied Volatility vs 24h Realized Volatility (IV - RV edge).
    3. Daily Volatility Bounds: 14-day Daily ATR and Expected Move to Expiry.
    4. Options Desk Positioning: Put/Call Ratio (OI) and Max Pain.

    Produces 0-100% suitability scores for:
    - Iron Condor (Range-bound neutral, high IV seller edge)
    - Short Straddle (Dead-flat market, extreme IV crush potential)
    - Short Strangle (Moderate range, wide OTM high probability of profit)
    - Bull Call Spread (Upward momentum, cheap call premium)
    - Bear Put Spread (Downward momentum, cheap put premium)
    """
    from tradebuddy.insights import _rv
    from tradebuddy.strategies.indicators import atr, ema, rsi

    stats = stats or {}
    summary = summary or {}
    setups = setups or {}

    # 1. 24h Price Change
    change_24h = stats.get("change_24h_pct")
    if change_24h is None and candles_15m and len(candles_15m) > 96:
        c_now = float(candles_15m[-1]["close"] if isinstance(candles_15m[-1], dict) else candles_15m[-1].close)
        c_prev = float(candles_15m[-97]["close"] if isinstance(candles_15m[-97], dict) else candles_15m[-97].close)
        change_24h = round((c_now - c_prev) / c_prev * 100, 2)
    change_24h_pct = float(change_24h or 0.0)

    # 2. Extract closes from 15m candles
    closes_15m: list[float] = []
    if candles_15m:
        for c in candles_15m:
            closes_15m.append(float(c["close"] if isinstance(c, dict) else c.close))

    # 3. 24h Realized Volatility
    rv = _rv(closes_15m, 96) if len(closes_15m) >= 97 else None
    if rv is None or rv <= 0:
        rv = 0.32  # Realistic baseline crypto daily realized vol
    realized_vol_24h = round(rv * 100, 1)

    # 4. ATM Implied Volatility
    raw_iv = summary.get("atm_iv")
    if not raw_iv or float(raw_iv) <= 0:
        raw_iv = 0.38  # Realistic baseline crypto implied vol
    atm_iv = round(float(raw_iv) * 100, 1)

    # 5. Volatility Spread (IV - RV)
    vol_spread = round(atm_iv - realized_vol_24h, 1)
    if vol_spread >= 3.0:
        vol_verdict = "High Volatility Premium (Seller Edge)"
    elif vol_spread <= -2.0:
        vol_verdict = "Low Volatility Premium (Buyer Edge)"
    else:
        vol_verdict = "Fairly Priced Volatility"

    # 6. Aggregate into 1-Day Bars for Big Timeframe Indicators
    daily_bars: list[dict[str, float]] = []
    if candles_15m and len(candles_15m) >= 96:
        for i in range(0, len(candles_15m), 96):
            chunk = candles_15m[i : i + 96]
            if chunk:
                o = float(chunk[0]["open"] if isinstance(chunk[0], dict) else chunk[0].open)
                h = max(float(b["high"] if isinstance(b, dict) else b.high) for b in chunk)
                l = min(float(b["low"] if isinstance(b, dict) else b.low) for b in chunk)
                c = float(chunk[-1]["close"] if isinstance(chunk[-1], dict) else chunk[-1].close)
                daily_bars.append({"open": o, "high": h, "low": l, "close": c})

    daily_closes = [b["close"] for b in daily_bars]
    daily_highs = [b["high"] for b in daily_bars]
    daily_lows = [b["low"] for b in daily_bars]

    # Daily RSI(14)
    if len(daily_closes) >= 15:
        rsi_series = rsi(daily_closes, 14)
        daily_rsi = round(rsi_series[-1], 1) if rsi_series else 50.0
    else:
        daily_rsi = round(max(20.0, min(80.0, 50.0 + change_24h_pct * 2.5)), 1)

    # Daily EMA(20)
    if len(daily_closes) >= 20:
        ema_series = ema(daily_closes, 20)
        daily_ema20 = round(ema_series[-1], 1) if ema_series else spot
    else:
        daily_ema20 = round(spot * (1.0 - change_24h_pct / 200.0), 1)

    # Daily ATR(14)
    if len(daily_closes) >= 15:
        atr_series = atr(daily_highs, daily_lows, daily_closes, 14)
        daily_atr = round(atr_series[-1], 1) if atr_series else round(spot * 0.025, 1)
    else:
        h24 = float(stats.get("high") or (spot * 1.025))
        l24 = float(stats.get("low") or (spot * 0.975))
        daily_atr = round(max(h24 - l24, spot * 0.02), 1)

    daily_atr_pct = round((daily_atr / spot) * 100, 2) if spot > 0 else 2.5

    # 7. Trend Regime on 1-Day Timeframe
    if daily_rsi >= 58.0 and spot > daily_ema20 and change_24h_pct > 0.5:
        trend = "Bullish Momentum"
    elif daily_rsi <= 42.0 and spot < daily_ema20 and change_24h_pct < -0.5:
        trend = "Bearish Momentum"
    else:
        trend = "Range-Bound Consolidation"

    # 8. Expected 1-Day Move
    exp_move = summary.get("expected_move")
    if not exp_move or float(exp_move) <= 0:
        exp_move = spot * (atm_iv / 100.0) * math.sqrt(1.0 / 365.0)
    expected_move = round(float(exp_move), 1)
    expected_low = round(spot - expected_move, 1)
    expected_high = round(spot + expected_move, 1)

    pcr_oi = float(summary["pcr_oi"]) if summary.get("pcr_oi") is not None else None
    max_pain = float(summary["max_pain"]) if summary.get("max_pain") is not None else None

    # 9. Compute Suitability Scores (0 - 100%)
    scores: dict[str, int] = {}

    # Iron Condor (Range-bound neutral, high IV seller edge)
    ic_score = 55
    if trend == "Range-Bound Consolidation":
        ic_score += 22
    if 46.0 <= daily_rsi <= 54.0:
        ic_score += 8
    if vol_spread >= 3.0:
        ic_score += 15
    elif vol_spread < 0:
        ic_score -= 10
    if trend != "Range-Bound Consolidation":
        ic_score -= 20
    scores["iron_condor"] = max(20, min(95, ic_score))

    # Short Strangle (Moderate range, wide OTM wings, high probability)
    st_score = 50
    if trend == "Range-Bound Consolidation":
        st_score += 18
    if atm_iv >= 35.0:
        st_score += 12
    if vol_spread >= 2.0:
        st_score += 10
    if daily_atr_pct > 4.0:
        st_score -= 15
    scores["strangle"] = max(20, min(92, st_score))

    # Short Straddle (ATM maximum theta, pure pin risk)
    sd_score = 40
    if vol_spread >= 6.0:
        sd_score += 25
    if 48.0 <= daily_rsi <= 52.0:
        sd_score += 15
    if daily_atr_pct > 3.0:
        sd_score -= 20
    if trend != "Range-Bound Consolidation":
        sd_score -= 25
    scores["straddle"] = max(15, min(88, sd_score))

    # Long Straddle (High Volatility Breakout Expected, Cheap IV Buyer Edge)
    ls_score = 35
    if vol_spread <= -3.0:
        ls_score += 25
    elif vol_spread < 0:
        ls_score += 12
    if daily_atr_pct > 3.5:
        ls_score += 18
    if atm_iv < 35.0:
        ls_score += 15
    if trend != "Range-Bound Consolidation":
        ls_score += 10
    if vol_spread >= 5.0:
        ls_score -= 20
    scores["long_straddle"] = max(15, min(92, ls_score))

    # Bull Call Spread (Upward momentum, cheap call debit)
    bc_score = 30
    if trend == "Bullish Momentum":
        bc_score += 38
    elif daily_rsi > 54.0:
        bc_score += 15
    if change_24h_pct > 1.0:
        bc_score += 12
    if vol_spread <= 1.0:
        bc_score += 10
    if trend == "Bearish Momentum":
        bc_score -= 25
    scores["call_spread"] = max(10, min(95, bc_score))

    # Bear Put Spread (Downward momentum, cheap put debit)
    bp_score = 30
    if trend == "Bearish Momentum":
        bp_score += 38
    elif daily_rsi < 46.0:
        bp_score += 15
    if change_24h_pct < -1.0:
        bp_score += 12
    if vol_spread <= 1.0:
        bp_score += 10
    if trend == "Bullish Momentum":
        bp_score -= 25
    scores["put_spread"] = max(10, min(95, bp_score))

    # Top Recommended Strategy
    top_strat = max(scores, key=lambda k: scores[k])
    top_score = scores[top_strat]

    strat_names = {
        "iron_condor": "Iron Condor (Defined Risk)",
        "straddle": "Short Straddle (ATM)",
        "long_straddle": "Long Straddle (Vol Breakout)",
        "strangle": "Short Strangle (OTM)",
        "call_spread": "Bull Call Spread (Defined Risk)",
        "put_spread": "Bear Put Spread (Defined Risk)",
    }

    rationale = (
        f"1-Day Regime: BTC is in {trend} with Daily RSI at {daily_rsi} and 24h change of {change_24h_pct:+.2f}%. "
        f"Implied volatility ({atm_iv}%) vs 24h realized volatility ({realized_vol_24h}%) gives a volatility spread of "
        f"{vol_spread:+.1f}% ({vol_verdict}). "
        f"Top recommendation is {strat_names.get(top_strat, top_strat)} with {top_score}% confidence suitability."
    )

    return DailyRegimeAnalysis(
        timeframe="1-Day (24h)",
        spot=spot,
        trend=trend,
        daily_rsi=daily_rsi,
        daily_ema20=daily_ema20,
        daily_atr=daily_atr,
        daily_atr_pct=daily_atr_pct,
        change_24h_pct=change_24h_pct,
        atm_iv=atm_iv,
        realized_vol_24h=realized_vol_24h,
        vol_spread=vol_spread,
        vol_verdict=vol_verdict,
        expected_move=expected_move,
        expected_low=expected_low,
        expected_high=expected_high,
        pcr_oi=pcr_oi,
        max_pain=max_pain,
        strategy_scores=scores,
        top_strategy=top_strat,
        top_score=top_score,
        decision_rationale=rationale,
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
