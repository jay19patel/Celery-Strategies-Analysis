"""EMA(9) crossing session VWAP: the simplest scalp there is, with an honest stop.

Two lines, and they measure different things. An EMA weights by **time** — the last nine bars,
most recent heaviest. VWAP weights by **size** — the price the money actually paid since the
session opened. So the cross is not one average overtaking another; it is the short-term price
crossing the level most desks are measured against, and that is why it behaves like an event
rather than a coincidence.

Session VWAP, reset at 00:00 UTC. Not a rolling window: a rolling VWAP drifts with whatever
happens to be inside it and has no meaning anyone trades against. A crypto perpetual has no close,
so the UTC day is the only session boundary there is.

On every closed 5-minute bar:

    CROSS       EMA(9) closed through VWAP on THIS bar — above for a long, below for a short.
                Fresh matters: `ema > vwap` is true for hours after the event, and a condition
                true on every bar is not a signal. Tested as "through now, not through before".
    SIDE        the cross has to agree with the 15m read. A VWAP cross against the higher
                timeframe is the one that gets faded.
    DISTANCE    price is not already far past VWAP. The whole idea is entering AT the level; a
                cross you notice when price is already 0.4% away is a chase.
    STOP        the other side of VWAP, plus a buffer. If price closes back through it the cross
                was noise and the reason for the trade is gone — which is the only thing a stop
                should ever mean.
    TARGET      the next level `nearby` names, and it must pay at least `MIN_RR` times the stop.

`decide()` is pure — bars and the readings in, a Decision out — so a backtest, a Celery worker and
the dashboard all get the same answer, and every refusal carries a reason you can read back from
the event log.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tradebuddy.delta import Candle
from tradebuddy.strategies.base import Context, Signal, Strategy
from tradebuddy.strategies.indicators import atr, ema
from tradebuddy.strategies.scalper import MIN_RR, _way, target

DAY = 86_400
EMA_PERIOD = 9
LOOKBACK = 300  # 25 hours of 5m bars, so a session VWAP always has its whole day
MAX_STRETCH_ATR = 1.0  # how far past VWAP price may already be and still be worth entering
STOP_BUFFER_ATR = 0.25  # beyond VWAP, so the wick that made the cross does not take the trade out
MIN_STOP_PCT = 0.10
MAX_STOP_PCT = 0.80
MIN_SESSION_BARS = 6  # before this the session VWAP is two prints and a rounding error


@dataclass(frozen=True)
class Decision:
    side: str | None
    reason: str
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None

    def to_signal(self) -> Signal | None:
        if not self.side:
            return None
        return Signal(side=self.side, reason=self.reason,
                      stop_loss_pct=self.stop_loss_pct, take_profit_pct=self.take_profit_pct)


def session_vwap(candles: list[Candle]) -> list[float | None]:
    """Running VWAP within each UTC day, one value per bar.

    Typical price by volume, accumulated from the session open. `None` until the session has
    `MIN_SESSION_BARS` behind it — a VWAP built from two bars is just those two bars, and treating
    it as a level is how a strategy ends up trading its own rounding."""
    out: list[float | None] = []
    day = None
    pv = vol = 0.0
    count = 0
    for c in candles:
        bar_day = int(c.time) // DAY
        if bar_day != day:
            day, pv, vol, count = bar_day, 0.0, 0.0, 0
        typical = (c.high + c.low + c.close) / 3
        pv += typical * (c.volume or 0.0)
        vol += c.volume or 0.0
        count += 1
        out.append(pv / vol if vol > 0 and count >= MIN_SESSION_BARS else None)
    return out


def decide(candles: list[Candle], view: dict[str, Any], structure: dict[str, Any] | None = None) -> Decision:
    """Pure. The whole rule, so a backtest runs exactly what trades."""
    structure = structure or {}
    if len(candles) < EMA_PERIOD + 2:
        return Decision(None, f"needs {EMA_PERIOD + 2} bars, has {len(candles)}")

    closes = [c.close for c in candles]
    line = ema(closes, EMA_PERIOD)
    if len(line) < 2:
        return Decision(None, "not enough bars for a 9 EMA yet")
    vwaps = session_vwap(candles)
    now_vwap, before_vwap = vwaps[-1], vwaps[-2]
    if now_vwap is None or before_vwap is None:
        return Decision(None, f"the session has fewer than {MIN_SESSION_BARS} bars — no VWAP to cross yet")

    now_ema, before_ema = line[-1], line[-2]
    price = closes[-1]
    atr_now = (atr([c.high for c in candles], [c.low for c in candles], closes, 14) or [0.0])[-1]
    if atr_now <= 0 or price <= 0:
        return Decision(None, "no ATR on this timeframe yet")

    # Fresh cross only. `ema > vwap` stays true for hours after the event.
    up = now_ema > now_vwap and before_ema <= before_vwap
    down = now_ema < now_vwap and before_ema >= before_vwap
    if not (up or down):
        side_now = "above" if now_ema > now_vwap else "below"
        return Decision(None, f"no cross on this bar — the 9 EMA was already {side_now} VWAP")
    side = 1 if up else -1

    if (short := _way(view, "15m")) and short != side:
        return Decision(None, f"the cross is {'up' if up else 'down'} but the 15m read is {'up' if short > 0 else 'down'} — this is the one that gets faded")

    # Entering AT the level, not chasing it.
    stretch = abs(price - now_vwap) / atr_now
    if stretch > MAX_STRETCH_ATR:
        return Decision(None, f"price is already {stretch:.1f} ATR past VWAP — the entry is gone, this would be a chase")

    # The stop: the other side of VWAP. Through it, the cross was noise.
    buffer_ = STOP_BUFFER_ATR * atr_now
    stop_at = (now_vwap - buffer_) if side > 0 else (now_vwap + buffer_)
    stop_pct = 100 * abs(price - stop_at) / price
    if stop_pct < MIN_STOP_PCT:
        return Decision(None, f"VWAP is only {stop_pct:.2f}% away — inside the spread and the fees")
    if stop_pct > MAX_STOP_PCT:
        return Decision(None, f"VWAP is {stop_pct:.2f}% away, wider than the {MAX_STOP_PCT:g}% this calls a scalp")

    level, what = target(structure, side, price)
    if level is None:
        return Decision(None, "no level above to aim at" if side > 0 else "no level below to aim at")
    target_pct = 100 * abs(level - price) / price
    rr = target_pct / stop_pct
    if rr < MIN_RR:
        return Decision(None, f"{what} is only {rr:.2f}:1 against a {stop_pct:.2f}% stop — under {MIN_RR:g}:1 it cannot pay for its costs")

    return Decision(
        side="buy" if side > 0 else "sell",
        reason=(f"9 EMA crossed {'above' if up else 'below'} session VWAP ({now_vwap:,.0f}) on this bar, "
                f"price {stretch:.1f} ATR from it; stop the other side of VWAP ({stop_pct:.2f}%), "
                f"target {what} at {rr:.1f}:1"),
        stop_loss_pct=round(stop_pct, 3),
        take_profit_pct=round(target_pct, 3),
    )


class EmaVwapCross(Strategy):
    name = "ema_vwap_5m"
    version = 1
    interval = "5m"
    symbols = ("BTCUSD", "ETHUSD")
    lookback = LOOKBACK
    is_default_sl_tp = False  # the stop is the other side of VWAP, not a percentage
    wants_outlook = True      # for the 15m read the cross has to agree with
    wants_structure = True    # for the level it aims at
    default_on = False        # it trades every five minutes; switch it on deliberately

    async def on_candle(self, ctx: Context) -> Signal | None:
        return decide(ctx.candles, ctx.outlook or {}, ctx.structure or {}).to_signal()
