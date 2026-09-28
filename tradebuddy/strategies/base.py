"""What a strategy is.

A strategy reacts to one closed candle and may return a Signal. It never sizes
beyond its fixed `size`, never places orders, and never sees the forming bar.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Protocol

from tradebuddy.delta import Candle, OptionQuote


@dataclass(frozen=True)
class Signal:
    side: str  # "buy" | "sell"
    reason: str = ""
    stop_loss_pct: float | None = None  # None -> STOP_LOSS_PCT from .env
    take_profit_pct: float | None = None  # None -> TAKE_PROFIT_PCT from .env


class Market(Protocol):
    def price(self, symbol: str) -> float | None: ...
    async def candles(self, symbol: str, resolution: str, count: int) -> list[Candle]: ...
    async def option_chain(self, underlying: str) -> list[OptionQuote]: ...


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

    @abstractmethod
    async def on_candle(self, ctx: Context) -> Signal | None: ...
