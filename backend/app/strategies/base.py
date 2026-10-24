"""What a strategy is.

A strategy reacts to one closed candle and may return a Signal. It never places
orders and never sees the forming bar. The Trader sizes each order from the
broker's available margin (Settings: trade_margin_pct); `size` is informational.

Stop loss and take profit: `is_default_sl_tp = True` (the default) uses Settings → stop
loss % and take profit % for every order, and ignores any levels on the Signal.
`is_default_sl_tp = False` means the strategy sets its own: every Signal must carry both
stop_loss_pct and take_profit_pct, or the result is an error and nothing is traded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from tradebuddy.delta import Candle, OptionQuote


@dataclass(frozen=True)
class Signal:
    side: str  # "buy" | "sell"
    reason: str = ""
    stop_loss_pct: float | None = None  # % from entry; used only when the strategy's is_default_sl_tp is False
    take_profit_pct: float | None = None


class Market(Protocol):
    def price(self, symbol: str) -> float | None: ...
    async def candles(self, symbol: str, resolution: str, count: int) -> list[Candle]: ...
    async def option_chain(self, underlying: str) -> list[OptionQuote]: ...
    async def option_summary(self, underlying: str) -> dict[str, Any] | None: ...
    async def option_history(self, underlying: str, hours: float) -> list[dict[str, Any]]:
        """The options numbers the engine has recorded for this underlying over the last `hours`,
        oldest first (options_history: ATM IV, skew, put/call, OI, max pain). Empty unless the
        strategy sets `options_history_hours`: the exchange keeps no history, so only what the
        engine has stored since it started running exists."""
        ...


def net_rr(target_pct: float, stop_pct: float, cost_pct: float) -> float:
    """Reward over risk AFTER the round trip — the only ratio that decides anything.

    The gross ratio flatters every short trade and flatters a scalp most of all: at a 0.14% round
    trip, a 0.12% stop against a 0.12% target is 1.0 gross and negative net. Both legs move, and
    they move apart — the cost comes off the reward AND goes on to the risk, because that is what
    the account does.

    A zero cost gives the gross ratio back, so a caller with no cost sent is no worse off than
    before. Returns 0.0 when the risk is zero or the reward cannot cover the trip.
    """
    if stop_pct <= 0:
        return 0.0
    cost = max(0.0, cost_pct)
    reward = target_pct - cost
    risk = stop_pct + cost
    return max(0.0, reward / risk) if risk > 0 else 0.0


def min_stop_pct(cost_pct: float, floor: float, multiple: float = 3.0) -> float:
    """The closest a stop may sit: whichever is wider, the strategy's own floor or `multiple`
    round trips.

    A stop inside the costs is not a stop, it is a guaranteed fee. Deriving the floor from the
    cost rather than hard-coding it is what stops a strategy keeping a 0.10% minimum after
    someone doubles the fee on the Settings page.
    """
    return max(floor, multiple * max(0.0, cost_pct))


@dataclass(frozen=True)
class Context:
    symbol: str
    resolution: str
    bar_time: int
    candles: list[Candle]  # closed bars, oldest first; the last one just closed. Empty when lookback == 0.
    market: Market
    # The engine's directional view for this symbol (outlook.build): trend on 15m/1h/1d, how far
    # price travels by when, and direction counted over every stored bar. Empty unless the strategy
    # sets `wants_outlook`. It arrives with the job rather than being computed here, because it is a
    # scan over a year of bars and because the strategy must read the same reading the dashboard
    # shows — two versions of "which way is this market going" is how a system stops being trusted.
    outlook: dict[str, Any] = field(default_factory=dict)
    # The engine's market-structure read (System.market_structure). Empty unless the strategy sets
    # `wants_structure`. This is what makes a stop go under the swing that actually held rather
    # than a fixed percentage, and a target sit at the level price is really heading for.
    structure: dict[str, Any] = field(default_factory=dict)
    # What one round trip costs on the broker this signal would reach, as a % of the notional:
    # the fee and the slippage, both ways (`Broker.round_trip_cost_pct`). 0.0 when the engine did
    # not send it, which makes `net_rr` fall back to the gross ratio.
    #
    # It arrives with the job because the cost belongs to the broker and the Settings, and a
    # strategy that keeps its own copy is a strategy that goes on using last month's fee. It used
    # to be a constant in `tb_master` and two unused floors in `scalper` and `ema_vwap`, which is
    # three definitions of one number and no way to tell which one was live.
    cost_pct: float = 0.0

    @property
    def round_trip(self) -> float:
        """The round trip, floored at zero — a negative fee is a misconfiguration, not a rebate."""
        return max(0.0, self.cost_pct)


class Strategy(ABC):
    name: ClassVar[str]  # stable: signals and toggles key off it
    version: ClassVar[int] = 1  # bump when the logic changes
    interval: ClassVar[str]  # the candle whose close triggers this strategy: "1m", "5m", "15m", ...
    symbols: ClassVar[tuple[str, ...]]
    size: ClassVar[int] = 1  # contracts per trade
    lookback: ClassVar[int] = 0  # closed candles handed to on_candle
    is_default_sl_tp: ClassVar[bool] = True  # True: Settings' stop loss and take profit; False: the Signal's own
    # Hours of stored options numbers to hand to on_candle through market.option_history. The engine
    # reads them once and sends them with the job, so a strategy that compares the options market with
    # where it was keeps no state of its own and gives the same answer on any worker.
    options_history_hours: ClassVar[float] = 0.0
    # Hand the engine's directional view to on_candle through ctx.outlook. Same reasoning as
    # options_history_hours: the worker has no database, and the reading must be the shared one.
    wants_outlook: ClassVar[bool] = False
    # Hand the engine's market-structure read to on_candle through ctx.structure: the live zones,
    # the resting pools and the sweeps that took them, the volume profile, the estimated flow, and
    # `nearby` — every level above and below with how many sources name it. Same reasoning as
    # wants_outlook: a worker has no database, and a strategy that worked its own levels out would
    # be a second answer to "where does price react", which is how a system stops being trusted.
    wants_structure: ClassVar[bool] = False
    # Margin cap for this strategy's orders, as a % of the broker's available margin, overriding
    # Settings' trade_margin_pct. For a scalper and nothing else: its stop is a fraction of a
    # percent, so the risk-based size is large and the normal cap binds long before the risk rule
    # does — the position ends up decided by the cap rather than by what is being risked. Raising
    # the GLOBAL cap to fix that would widen every other strategy's positions too. The risk rule
    # is never bypassed: size is still min(by_risk, by_margin), so this can only let the risk
    # calculation have its way, never exceed it.
    margin_pct: ClassVar[float | None] = None
    # Whether this strategy is on before anyone has touched its switch. Toggles default to ON, so
    # a strategy that lands in a release starts trading the moment it is deployed — fine for one
    # that takes a position every few days, wrong for anything that trades often or sizes hard.
    # Set False and it waits to be switched on, like Delta does (invariant 1: fail closed).
    default_on: ClassVar[bool] = True

    @abstractmethod
    async def on_candle(self, ctx: Context) -> Signal | None: ...
