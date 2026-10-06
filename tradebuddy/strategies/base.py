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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Context:
    symbol: str
    resolution: str
    bar_time: int
    candles: list[Candle]  # closed bars, oldest first; the last one just closed. Empty when lookback == 0.
    market: Market


class Strategy(ABC):
    name: ClassVar[str]  # stable: signals and toggles key off it
    version: ClassVar[int] = 1  # bump when the logic changes
    interval: ClassVar[str]  # the candle whose close triggers this strategy: "1m", "5m", "15m", ...
    symbols: ClassVar[tuple[str, ...]]
    size: ClassVar[int] = 1  # contracts per trade
    lookback: ClassVar[int] = 0  # closed candles handed to on_candle
    is_default_sl_tp: ClassVar[bool] = True  # True: Settings' stop loss and take profit; False: the Signal's own

    @abstractmethod
    async def on_candle(self, ctx: Context) -> Signal | None: ...
