"""EMA(9) crossing session VWAP on the 5m chart (from the v3 engine).

An EMA weights by time, VWAP by size — the price the money actually paid since the session opened
(00:00 UTC). So the cross is short-term price crossing the level most desks are measured against.

    CROSS     EMA(9) closed through VWAP on THIS bar. `ema > vwap` stays true for hours after the
              event; only the fresh cross is a signal.
    SIDE      it agrees with the engine's 15m read — a cross against it is the one that gets faded.
    DISTANCE  price is not already more than an ATR past VWAP: entering at the level, not chasing.
    STOP      the other side of VWAP plus a quarter ATR. Back through it, the cross was noise.
    TARGET    the next level `nearby` names, at least MIN_NET_RR times the stop after costs.
"""

from __future__ import annotations

from app.market.delta import Candle
from app.market.indicators import atr, ema
from app.strategies.base import Context, Decision, Strategy, min_stop_pct, net_rr, next_level, none, way

DAY = 86_400
EMA_PERIOD = 9
MAX_STRETCH_ATR = 1.0
STOP_BUFFER_ATR = 0.25
MIN_STOP_PCT = 0.10
MAX_STOP_PCT = 0.80
MIN_ROOM_ATR = 1.0  # the target at least this many ATRs away
MIN_NET_RR = 1.5
MIN_SESSION_BARS = 6  # before this the session VWAP is two prints and a rounding error


def session_vwap(candles: list[Candle]) -> list[float | None]:
    """Running VWAP within each UTC day, one value per bar; None until the session has enough bars."""
    out: list[float | None] = []
    day, pv, vol, count = None, 0.0, 0.0, 0
    for c in candles:
        if int(c.time) // DAY != day:
            day, pv, vol, count = int(c.time) // DAY, 0.0, 0.0, 0
        pv += (c.high + c.low + c.close) / 3 * (c.volume or 0.0)
        vol += c.volume or 0.0
        count += 1
        out.append(pv / vol if vol > 0 and count >= MIN_SESSION_BARS else None)
    return out


class EmaVwap(Strategy):
    name = "ema_vwap"
    label = "EMA × VWAP"
    summary = "A fresh 9 EMA cross of session VWAP on 5m, with the 15m trend, entered at the level."
    stop_rule = "the other side of VWAP, plus a quarter ATR"
    target_rule = f"the next support/resistance level, at least {MIN_NET_RR:g}:1 after costs"
    interval = "5m"
    lookback = 300  # 25 hours, so the session VWAP always has its whole day
    wants_outlook = True
    wants_structure = True

    def decide(self, ctx: Context) -> Decision:
        candles = ctx.candles
        if len(candles) < EMA_PERIOD + 16:
            return none(f"needs {EMA_PERIOD + 16} bars, has {len(candles)}")
        closes = [c.close for c in candles]
        line = ema(closes, EMA_PERIOD)
        vwaps = session_vwap(candles)
        now_vwap, before_vwap = vwaps[-1], vwaps[-2]
        if now_vwap is None or before_vwap is None or len(line) < 2:
            return none(f"the session has fewer than {MIN_SESSION_BARS} bars — no VWAP to cross yet")
        now_ema, before_ema = line[-1], line[-2]
        price = closes[-1]
        atr_now = (atr([c.high for c in candles], [c.low for c in candles], closes, 14) or [0.0])[-1]
        if atr_now <= 0:
            return none("no ATR yet")

        up = now_ema > now_vwap and before_ema <= before_vwap
        down = now_ema < now_vwap and before_ema >= before_vwap
        if not (up or down):
            return none(f"no cross on this bar — the 9 EMA is {'above' if now_ema > now_vwap else 'below'} VWAP")
        side = 1 if up else -1
        if (trend := way(ctx.outlook, "15m")) and trend != side:
            return none(f"the cross is {'up' if up else 'down'} but the 15m read is {'up' if trend > 0 else 'down'}")
        stretch = abs(price - now_vwap) / atr_now
        if stretch > MAX_STRETCH_ATR:
            return none(f"price is already {stretch:.1f} ATR past VWAP — a chase")

        stop = now_vwap - side * STOP_BUFFER_ATR * atr_now
        stop_pct = 100 * abs(price - stop) / price
        floor = min_stop_pct(ctx.cost_pct, MIN_STOP_PCT)
        if stop_pct < floor:
            return none(f"VWAP is only {stop_pct:.2f}% away, inside the {floor:.2f}% the costs need")
        if stop_pct > MAX_STOP_PCT:
            return none(f"VWAP is {stop_pct:.2f}% away, wider than the {MAX_STOP_PCT:g}% this calls a scalp")

        level, what = next_level(ctx.structure, side, price)
        if level is None:
            return none(f"no level {'above' if side > 0 else 'below'} to aim at")
        target_pct = 100 * abs(level - price) / price
        atr_pct = 100 * atr_now / price
        if target_pct < MIN_ROOM_ATR * atr_pct:
            return none(f"the next level is {target_pct:.2f}% away, inside {MIN_ROOM_ATR:g} ATR ({atr_pct:.2f}%)")
        rr = net_rr(target_pct, stop_pct, ctx.cost_pct)
        if rr < MIN_NET_RR:
            return none(f"{what} is only {rr:.2f}:1 after costs against a {stop_pct:.2f}% stop (needs {MIN_NET_RR:g}:1)")
        return Decision(
            "buy" if up else "sell",
            f"9 EMA crossed {'above' if up else 'below'} session VWAP {now_vwap:,.0f}; stop the other side of VWAP "
            f"({stop_pct:.2f}%), target {what} at {rr:.1f}:1 after costs",
            entry=price, stop=round(stop, 2), target=round(level, 2),
        )
