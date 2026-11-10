"""The trade tape and the book, folded into one row a minute: the footprint.

Delta's `all_trades` channel prints every trade with the side that crossed the spread
(`buyer_role: taker` is a buyer lifting the ask), and `l2_orderbook` the resting book. Both are
folded here in memory and leave as one `FlowBar` per symbol per minute:

    footprint  bought and sold size at each price step (`STEP`), the bar's open/high/low/close
    delta      aggressive buying minus aggressive selling; trades; the largest single print
    book       resting size within `BOOK_BAND` of the mid on each side, averaged over the minute

Ticks are never stored (invariant 13): a minute is the finest grain kept. Option trades are folded
per underlying into premium bought and sold by calls and puts, taken by the minute recorder.

A print more than `MAX_JUMP` from the last good one is what an empty book does, not a market (the
same rule the bar store applies): it is counted and left out, never folded into a level.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

STEP = {"BTCUSD": 10.0, "ETHUSD": 0.5, "XAUTUSD": 0.5}  # footprint price step; else 0.05% of price
BOOK_BAND = 0.005  # resting size within 0.5% of the mid
MAX_JUMP = 0.05  # a print 5% from the last good one is a bad print


def step_for(symbol: str, price: float) -> float:
    return STEP.get(symbol) or max(price * 0.0005, 1e-9)


@dataclass
class _Minute:
    time: int
    open: float
    high: float
    low: float
    close: float
    buy: float = 0.0
    sell: float = 0.0
    trades: int = 0
    max_trade: float = 0.0
    levels: dict[float, list[float]] = field(default_factory=dict)
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    book_samples: int = 0

    def row(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol, "time": self.time, "open": self.open, "high": self.high, "low": self.low, "close": self.close,
            "volume": self.buy + self.sell, "buy": self.buy, "sell": self.sell, "trades": self.trades, "max_trade": self.max_trade,
            "levels": [[p, round(b, 6), round(s, 6)] for p, (b, s) in sorted(self.levels.items())],
            "bid_depth": self.bid_depth / self.book_samples if self.book_samples else None,
            "ask_depth": self.ask_depth / self.book_samples if self.book_samples else None,
        }


@dataclass
class OptionFlow:
    """Premium in USD that crossed the spread on each kind of option, since the last take."""

    call_buy: float = 0.0
    call_sell: float = 0.0
    put_buy: float = 0.0
    put_sell: float = 0.0
    trades: int = 0


class Tape:
    def __init__(self) -> None:
        self._minutes: dict[str, _Minute] = {}  # symbol -> the minute being built
        self._done: list[dict[str, Any]] = []  # finished minutes waiting for `closed`
        self._last: dict[str, float] = {}  # symbol -> the last good print
        self._options: dict[str, OptionFlow] = {}
        self.rejected = 0  # bad prints left out, for the System page

    def trade(self, symbol: str, price: float, size: float, taker_buy: bool, ts: float, mark: float | None = None) -> None:
        """One print. Judged against the perpetual's `mark` when it is known — no single print moves
        the mark, so a junk print can never become the yardstick — else against the last good print."""
        if not (math.isfinite(price) and price > 0 and size > 0):
            return
        last = mark or self._last.get(symbol)
        if last and abs(price / last - 1) > MAX_JUMP:
            self.rejected += 1
            return
        self._last[symbol] = price
        minute = int(ts // 60 * 60)
        bar = self._minutes.get(symbol)
        if bar is None or bar.time != minute:
            if bar is not None:
                self._done.append(bar.row(symbol))
            bar = self._minutes[symbol] = _Minute(minute, price, price, price, price)
        bar.high, bar.low, bar.close = max(bar.high, price), min(bar.low, price), price
        bar.trades += 1
        bar.max_trade = max(bar.max_trade, size)
        step = step_for(symbol, price)
        level = bar.levels.setdefault(round(math.floor(price / step) * step, 8), [0.0, 0.0])
        if taker_buy:
            bar.buy += size
            level[0] += size
        else:
            bar.sell += size
            level[1] += size

    def book(self, symbol: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> None:
        """One snapshot of the resting book: its size within the band, added to this minute's mean."""
        bar = self._minutes.get(symbol)
        if bar is None or not bids or not asks:
            return
        mid = (max(p for p, _ in bids) + min(p for p, _ in asks)) / 2
        if mid <= 0:
            return
        bar.bid_depth += sum(size for p, size in bids if p >= mid * (1 - BOOK_BAND))
        bar.ask_depth += sum(size for p, size in asks if p <= mid * (1 + BOOK_BAND))
        bar.book_samples += 1

    def option_trade(self, underlying: str, kind: str, price: float, units: float, taker_buy: bool) -> None:
        if not (math.isfinite(price) and price > 0 and units > 0):
            return
        flow = self._options.setdefault(underlying, OptionFlow())
        premium = price * units
        side = f"{kind}_{'buy' if taker_buy else 'sell'}"
        setattr(flow, side, getattr(flow, side) + premium)
        flow.trades += 1

    def closed(self, now: float) -> list[dict[str, Any]]:
        """Every minute that has ended, once: the finished ones, and a quiet symbol's last minute."""
        current = int(now // 60 * 60)
        for symbol, bar in list(self._minutes.items()):
            if bar.time < current:
                self._done.append(bar.row(symbol))
                del self._minutes[symbol]
        out, self._done = self._done, []
        return out

    def take_options(self, underlying: str) -> OptionFlow:
        """The option premium folded since the last take, and a fresh count from here."""
        return self._options.pop(underlying, OptionFlow())
