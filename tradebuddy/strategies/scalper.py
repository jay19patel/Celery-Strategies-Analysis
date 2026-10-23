"""Scalper: many small trades with the trend, on the five-minute bar.

Same cadence as the written summary — a decision every five minutes about whether this bar is
worth taking — but the decision itself is **measured, not written**. The model that writes the
summary computes nothing and decides nothing (CLAUDE.md 15b), and that does not stop at prose: a
sentence cannot be rechecked against a bar, so it must not be the reason an order exists. What
this reads is `ctx.outlook`, the engine's own counted view, which the dashboard shows and a
backtest can reproduce. If the summary and this strategy ever disagree, the summary is the one
with no evidence behind it.

On every closed 5-minute bar:

    PERMISSION  the 15m and 1h reads agree on a direction. A scalp against the hour is the trade
                that works four times and gives it all back on the fifth.
    TRIGGER     the bar closed in the top (or bottom) `PUSH` of its own range, on more volume than
                the recent average. That is a bar being taken, not drifting.
    LOCATION    price is not already at the level it would be aiming for — the point of control or
                a value-area edge, from the profile the outlook carries. Entering a scalp into the
                price the size itself made is paying for the last point of a move.
    LEVELS      a stop a fraction of a percent away — `STOP_ATR` of the 5m ATR, floored and capped
                so it is never inside the spread nor a swing trade by accident — and a target
                `REWARD` times it.
    NO TRADE    otherwise, which is most bars.

**Why the size is the interesting part.** The stop is tiny, so the risk rule
(`risk_per_trade_pct` of equity behind the stop) asks for a large position — which is the point of
scalping, and correct: the same money is at risk per trade as any other strategy, just behind a
closer stop. But `trade_margin_pct` caps every order at a share of available margin, and at a 0.15%
stop that cap binds long before the risk rule does, so the position would be decided by the cap
instead of by what is being risked. So this declares `margin_pct`, which raises **only the cap**.
Sizing stays `min(by_risk, by_margin)`: it can never risk more than `risk_per_trade_pct`, it just
stops the cap from answering first.

`decide()` is pure — bars and the view in, a Decision out — so the backtest, a Celery worker and
the dashboard all get the same answer, and every refusal carries a reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tradebuddy.delta import Candle
from tradebuddy.strategies.base import Context, Signal, Strategy
from tradebuddy.strategies.indicators import atr

LOOKBACK = 60  # 5 hours of 5m bars: enough for an ATR and a volume average
VOLUME_WINDOW = 20
MIN_VOLUME_RATIO = 1.15  # the bar carried more than its recent average
PUSH = 0.65  # the close sat this far up (or down) its own range
STOP_ATR = 0.75  # the stop, as a share of the 5m ATR
REWARD = 1.6  # target = REWARD x stop, before costs
# A scalper's stop has to clear the spread and the fees or the target is unreachable by
# construction; it also has to stay small enough that this is still a scalp.
MIN_STOP_PCT = 0.12
MAX_STOP_PCT = 0.60
# How close to the next level is too close to bother. In ATRs of the same 5m bar.
MIN_ROOM_ATR = 1.0


@dataclass(frozen=True)
class Decision:
    side: str | None  # "buy" | "sell" | None
    reason: str
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None

    def to_signal(self) -> Signal | None:
        if not self.side:
            return None
        return Signal(side=self.side, reason=self.reason,
                      stop_loss_pct=self.stop_loss_pct, take_profit_pct=self.take_profit_pct)


def _way(view: dict[str, Any], interval: str) -> int:
    """+1, -1 or 0 for one timeframe's read, from the engine's own view."""
    for row in view.get("summary") or []:
        if str(row.get("interval")) == interval:
            return int(row.get("direction") or 0)
    for frame in view.get("timeframes") or []:
        if str(frame.get("interval")) == interval:
            bias = (frame.get("structure") or {}).get("bias")
            return {"bullish": 1, "bearish": -1}.get(str(bias), 0)
    return 0


def _room(view: dict[str, Any], side: int) -> float | None:
    """How far to the level this trade would be heading into, in percent of price.

    From the volume profile the outlook already carries — the point of control and the edges of
    the value area, which are the prices the traded size itself made. Not from `nearby`: that is
    built in `market_structure`, which a strategy never receives, so reading it here would have
    returned None on every bar and quietly disabled this check for good. A safeguard that cannot
    fire is worse than no safeguard, because it reads like one."""
    prof = view.get("profile") or {}
    price = prof.get("price")
    if not prof.get("ready") or not isinstance(price, int | float) or price <= 0:
        return None
    ahead = [
        level for key in ("poc", "value_low", "value_high")
        if isinstance(level := prof.get(key), int | float)
        and (level > price if side > 0 else level < price)
    ]
    return min(abs(100 * (level / price - 1)) for level in ahead) if ahead else None


def decide(candles: list[Candle], view: dict[str, Any]) -> Decision:
    """Pure. The whole rule, so a backtest runs exactly what trades."""
    if len(candles) < VOLUME_WINDOW + 2:
        return Decision(None, f"needs {VOLUME_WINDOW + 2} bars, has {len(candles)}")

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    atr_now = (atr(highs, lows, closes, 14) or [0.0])[-1]
    price = closes[-1]
    if atr_now <= 0 or price <= 0:
        return Decision(None, "no ATR on this timeframe yet")
    atr_pct = 100 * atr_now / price

    # Permission: the two timeframes above this one have to agree.
    short, medium = _way(view, "15m"), _way(view, "1h")
    if short == 0 or short != medium:
        return Decision(None, f"15m and 1h do not agree ({short:+d} vs {medium:+d}) — a scalp against the hour is not worth taking")
    side = short

    # Trigger: this bar was taken, not drifted.
    bar = candles[-1]
    span = bar.high - bar.low
    if span <= 0:
        return Decision(None, "the bar had no range")
    push = (bar.close - bar.low) / span if side > 0 else (bar.high - bar.close) / span
    if push < PUSH:
        return Decision(None, f"the close sat {push:.0%} of the way {'up' if side > 0 else 'down'} the bar, under the {PUSH:.0%} this needs")

    volumes = [c.volume for c in candles[-VOLUME_WINDOW - 1 : -1]]
    average = sum(volumes) / len(volumes) if volumes else 0.0
    ratio = bar.volume / average if average > 0 else 0.0
    if ratio < MIN_VOLUME_RATIO:
        return Decision(None, f"volume was {ratio:.2f}x its recent average, under the {MIN_VOLUME_RATIO:.2f}x this needs")

    # Location: do not buy into the level the trade is aiming at.
    room = _room(view, side)
    if room is not None and room < MIN_ROOM_ATR * atr_pct:
        return Decision(None, f"only {room:.2f}% to the next level, inside the {MIN_ROOM_ATR:g} ATR ({atr_pct:.2f}%) this wants")

    stop = min(max(STOP_ATR * atr_pct, MIN_STOP_PCT), MAX_STOP_PCT)
    return Decision(
        side="buy" if side > 0 else "sell",
        reason=(f"5m close {push:.0%} {'up' if side > 0 else 'down'} its range on {ratio:.2f}x volume, "
                f"15m and 1h both {'up' if side > 0 else 'down'}; stop {stop:.2f}% ({STOP_ATR:g} ATR)"),
        stop_loss_pct=round(stop, 3),
        take_profit_pct=round(stop * REWARD, 3),
    )


class Scalper(Strategy):
    name = "scalper"
    version = 1
    interval = "5m"
    symbols = ("BTCUSD", "ETHUSD")
    lookback = LOOKBACK
    is_default_sl_tp = False  # its own small stop; Settings' percentages are a swing trade here
    wants_outlook = True
    # Only the cap, never the risk. See the module docstring: at a 0.15% stop the global
    # trade_margin_pct decides the size before the risk rule gets a say, and raising the global one
    # would widen every other strategy's positions too.
    margin_pct = 60.0
    # Off until switched on. It trades every five minutes and sizes against a much looser cap
    # than anything else here; arriving switched on in a release is not a thing it should do.
    default_on = False

    async def on_candle(self, ctx: Context) -> Signal | None:
        return decide(ctx.candles, ctx.outlook or {}).to_signal()
