"""TB Manager: trades the break, in the direction the timeframes allow, at the odds the history counted.

This is the strategy that acts on everything the Market page shows. It reads the engine's own
directional view (`ctx.outlook`, built by `outlook.build`) rather than working anything out for
itself, so what it trades and what a person sees on the page are the same reading. A strategy that
quietly disagreed with the dashboard would be worse than no strategy at all.

On every closed 15-minute bar:

    TRIGGER     the bar that just closed broke structure — a fresh close through the prior 20-bar
                swing high (long) or low (short). Fresh matters: the level stays broken for hours,
                and a condition true on every bar is not an event.
    CONVICTION  the break happened on a bar bigger than usual and on more volume than usual. A
                break on an ordinary bar is a level being brushed, not taken.
    PERMISSION  no higher timeframe is pointing the other way. A 15-minute break up inside a daily
                downtrend is a bounce, and this strategy does not trade bounces.
    ODDS        over the stored bars, breaks of this exact kind and direction have gone on the way
                they broke more often than any bar did. That record is the trade's own history; where
                there are too few of them it falls back to how the market's structure has behaved.
                With neither it refuses rather than guesses — see `require_counted_edge`.
    LEVELS      the stop goes under the bar that broke out: if price trades back through it, the
                break failed and the reason for the trade is gone. Floored and capped by ATR so it
                is never inside the noise or absurdly wide. The target is `reward` times that, and
                an options wall in the way caps it — there is no point aiming through the strike
                with the most open interest on it.
    NO TRADE    on every other bar, which is the overwhelming majority of them.

Long and short, symmetrically: the same rules mirrored, so neither side is accidentally easier.

`decide()` is pure — bars and the view in, a Decision out — so the same code answers the backtest,
the dashboard and a Celery worker, and so every refusal has a reason that can be read back from a
`TradeSkipped` event instead of guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from tradebuddy import structure as mkt
from tradebuddy.delta import Candle
from tradebuddy.strategies.base import Context, Signal, Strategy
from tradebuddy.strategies.indicators import atr
from tradebuddy.strategies.levels import from_stop


@dataclass(frozen=True)
class Decision:
    """What the strategy concluded, including when it concluded nothing.

    `reason` is always filled: a refusal without a reason cannot be read back, and most bars are
    refusals."""

    side: str = ""  # "buy" | "sell" | "" for no trade
    reason: str = ""
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None

    @property
    def trades(self) -> bool:
        return bool(self.side)


def _wall_cap(side: str, price: float, target: float, options: dict[str, Any] | None) -> tuple[float, str]:
    """Pull the target back to an options wall standing in front of it.

    The strike with the most open interest is where the dealers are. Aiming through it is aiming at
    a level that has to be fought for, and a target that is never reached is a trade that gives
    back its profit."""
    day = (options or {}).get("day") or {}
    wall = day.get("call_wall") if side == "buy" else day.get("put_wall")
    if not wall:
        return target, ""
    beyond = target > wall > price if side == "buy" else target < wall < price
    if not beyond:
        return target, ""
    return float(wall), f", target capped at the {'call' if side == 'buy' else 'put'} wall {wall:,.0f}"


def decide(
    bars: list[Candle],
    view: dict[str, Any],
    options: dict[str, Any] | None = None,
    *,
    lookback: int = 20,
    min_displacement: float = 1.3,
    min_volume_ratio: float = 1.1,
    reward: float = 2.0,
    atr_period: int = 14,
    min_stop_atr: float = 0.8,
    max_stop_atr: float = 4.0,
    stop_buffer_atr: float = 0.15,
    require_counted_edge: bool = True,
    min_edge: float = 0.01,
    edge_horizon_hours: float = 1.0,
) -> Decision:
    """Pure. Bars and the engine's view in, a Decision out."""
    if len(bars) < lookback + atr_period + 5:
        return Decision(reason=f"warming up: {len(bars)} bars")

    breaks = mkt.find_breaks(bars, lookback)
    last = bars[-1]
    fresh = breaks[-1] if breaks and breaks[-1].index == len(bars) - 1 else None
    if fresh is None:
        return Decision(reason="no break of structure on this bar")

    side = "buy" if fresh.direction == "up" else "sell"
    what = f"{'BOS' if fresh.kind == 'bos' else 'CHoCH'} {fresh.direction} through {fresh.level:,.2f}"

    # -- conviction: was the break taken, or brushed
    if fresh.displacement < min_displacement:
        return Decision(reason=f"{what} on an ordinary bar ({fresh.displacement:.1f}x usual, needs {min_displacement}x)")
    if fresh.volume_ratio < min_volume_ratio:
        return Decision(reason=f"{what} on thin volume ({fresh.volume_ratio:.1f}x usual, needs {min_volume_ratio}x)")

    # -- permission: nothing bigger pointing the other way
    if not view.get("ready"):
        return Decision(reason=f"{what}, but there is no stored history to read the timeframes from")
    wanted = 1 if side == "buy" else -1
    against = [f for f in view.get("timeframes") or [] if f["interval"] != "15m" and f["direction"] == -wanted]
    if against:
        names = ", ".join(f"{f['interval']} {f['trend']}" for f in against)
        return Decision(reason=f"{what}, but {names} — that is a bounce, not a trend")

    # -- odds: has this break actually gone this way more often than any bar did
    from tradebuddy import outlook as ol

    # The break's own follow-through record first: it is the closest counted answer to the question
    # this trade asks. The structure-bias rate describes the market the break happened in, not the
    # break, so it is only the fallback — used while there are too few of this kind to count.
    counted = (ol.pattern_edge(view, "15m", fresh.kind, fresh.direction)
               or ol.counted_edge(view, side, edge_horizon_hours))
    if counted is None:
        if require_counted_edge:
            return Decision(reason=f"{what}, but there is not enough counted history to say it pays — run backfill")
        edge_note = ", odds not counted yet"
    elif counted["edge"] < min_edge:
        return Decision(
            reason=f"{what}, but the counted odds give it {100 * counted['edge']:+.1f} points over the base rate "
                   f"({counted['sample']:,} bars), under the {100 * min_edge:.0f} it needs",
        )
    else:
        what_counted = counted.get("source") or "this structure"
        edge_note = (f", {what_counted} has followed through {100 * counted['conditional']:.0f}% of "
                     f"{counted['sample']:,} times against a base rate of {100 * counted['baseline']:.0f}% "
                     f"({100 * counted['edge']:+.1f} pts)")

    # -- levels: the stop goes where the break would have failed
    highs, lows, closes = ([c.high for c in bars], [c.low for c in bars], [c.close for c in bars])
    band = atr(highs, lows, closes, atr_period)
    if not band:
        return Decision(reason=f"{what}, but ATR is not available yet")
    a = band[-1]
    price = last.close
    # Back through the breaking bar and the idea is gone. The buffer keeps the stop off the exact
    # extreme, where a wick would take it out without the break having failed.
    raw = (last.low - stop_buffer_atr * a) if side == "buy" else (last.high + stop_buffer_atr * a)
    distance = abs(price - raw)
    if distance > max_stop_atr * a:
        return Decision(reason=f"{what}, but the stop would be {distance / a:.1f}x ATR away — too wide to size")
    atr_pct = 100 * a / price
    target = price + reward * distance if side == "buy" else price - reward * distance
    target, capped = _wall_cap(side, price, target, options)

    levels = from_stop(side, price, raw, reward=abs(target - price) / distance, floor_pct=min_stop_atr * atr_pct)
    if levels is None:
        return Decision(reason=f"{what}, but the stop level sits on the wrong side of the price")
    sl_pct, tp_pct = levels
    if tp_pct <= sl_pct:
        return Decision(reason=f"{what}, but the capped target {tp_pct:.2f}% is inside the stop {sl_pct:.2f}%")

    reason = (
        f"{what} on a bar {fresh.displacement:.1f}x usual and {fresh.volume_ratio:.1f}x volume; "
        f"{view['alignment']['verdict']}{edge_note}; stop under the break at {raw:,.2f} ({sl_pct:.2f}%), "
        f"target {tp_pct:.2f}%{capped}"
    )
    return Decision(side=side, reason=reason, stop_loss_pct=sl_pct, take_profit_pct=tp_pct)


class TradeBuddyManager(Strategy):
    name = "trade_buddy_manager_15m"
    version = 1
    interval = "15m"
    symbols = ("BTCUSD", "ETHUSD")
    lookback = 300  # 20-bar swings, ATR(14), and room for the break history to mean something
    is_default_sl_tp = False  # the stop goes where the idea fails, not at a percentage
    wants_outlook = True

    swing_lookback: ClassVar[int] = 20
    min_displacement: ClassVar[float] = 1.3
    min_volume_ratio: ClassVar[float] = 1.1
    reward: ClassVar[float] = 2.0
    atr_period: ClassVar[int] = 14
    min_stop_atr: ClassVar[float] = 0.8
    max_stop_atr: ClassVar[float] = 4.0
    stop_buffer_atr: ClassVar[float] = 0.15
    # Off, and nothing trades, until the counted history says the setup pays. `backfill` fills it;
    # until then every refusal says so. Fail closed: a strategy that trades on a hunch because the
    # evidence has not arrived yet is the thing this whole system was built not to be.
    require_counted_edge: ClassVar[bool] = True
    min_edge: ClassVar[float] = 0.01
    edge_horizon_hours: ClassVar[float] = 1.0

    async def on_candle(self, ctx: Context) -> Signal | None:
        options = None
        try:
            options = await ctx.market.option_summary(ctx.symbol.removesuffix("USD"))
        except Exception:
            options = None  # the walls only cap a target; without them the trade is still valid
        d = decide(
            ctx.candles, ctx.outlook, options,
            lookback=self.swing_lookback, min_displacement=self.min_displacement,
            min_volume_ratio=self.min_volume_ratio, reward=self.reward, atr_period=self.atr_period,
            min_stop_atr=self.min_stop_atr, max_stop_atr=self.max_stop_atr, stop_buffer_atr=self.stop_buffer_atr,
            require_counted_edge=self.require_counted_edge, min_edge=self.min_edge,
            edge_horizon_hours=self.edge_horizon_hours,
        )
        if not d.trades:
            return None
        return Signal(d.side, d.reason, d.stop_loss_pct, d.take_profit_pct)
