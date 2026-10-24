"""What a directional strategy is.

It reads closed bars of one timeframe (plus whatever else it declares it wants) and returns a
`Decision`: no trade with the reason, or a side with its **own** stop and target as prices. The stop
sits where the idea stops being true — the other side of VWAP, beyond the signal candle, on the
Supertrend line — and the target is set relative to it, so every trade's risk is the strategy's,
not a percentage typed into a form. It never places an order and keeps no state between bars:
`decide()` is pure, so a backtest runs exactly the code that trades.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

from app.market.delta import Candle


@dataclass(frozen=True)
class Context:
    symbol: str  # BTCUSD
    underlying: str  # BTC
    interval: str
    candles: list[Candle]  # closed bars of `interval`, oldest first; the last one just closed
    outlook: dict[str, Any] = field(default_factory=dict)  # when the strategy wants_outlook
    structure: dict[str, Any] = field(default_factory=dict)  # when it wants_structure
    options_history: list[dict[str, Any]] = field(default_factory=list)  # when it wants_options_history
    cost_pct: float = 0.0  # one round trip, % of notional: fees both ways and the spread


@dataclass(frozen=True)
class Decision:
    side: str | None  # buy | sell | None
    reason: str
    entry: float | None = None  # the close it decided on
    stop: float | None = None
    target: float | None = None

    @property
    def rr(self) -> float | None:
        if not self.side or not self.entry or self.stop is None or self.target is None:
            return None
        risk = abs(self.entry - self.stop)
        return abs(self.target - self.entry) / risk if risk else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"rr": None if self.rr is None else round(self.rr, 2)}


def none(reason: str) -> Decision:
    return Decision(None, reason)


def net_rr(target_pct: float, stop_pct: float, cost_pct: float) -> float:
    """Reward over risk after the round trip: the cost comes off the reward and goes on to the risk."""
    if stop_pct <= 0:
        return 0.0
    reward, risk = target_pct - max(0.0, cost_pct), stop_pct + max(0.0, cost_pct)
    return max(0.0, reward / risk) if risk > 0 else 0.0


def min_stop_pct(cost_pct: float, floor: float, multiple: float = 3.0) -> float:
    """The closest a stop may sit: the strategy's floor or `multiple` round trips, whichever is wider."""
    return max(floor, multiple * max(0.0, cost_pct))


def way(view: dict[str, Any], interval: str) -> int:
    """+1, -1 or 0 for one timeframe's read in the engine's outlook."""
    for row in view.get("summary") or []:
        if str(row.get("interval")) == interval:
            return int(row.get("direction") or 0)
    for frame in view.get("timeframes") or []:
        if str(frame.get("interval")) == interval:
            return {"bullish": 1, "bearish": -1}.get(str((frame.get("structure") or {}).get("bias")), 0)
    return 0


def next_level(structure: dict[str, Any], side: int, price: float) -> tuple[float | None, str]:
    """The nearest level the trade is heading into, from `nearby` (every source merged)."""
    near = (structure or {}).get("nearby") or {}
    for level in (near.get("above") if side > 0 else near.get("below")) or []:
        at = level.get("price")
        if isinstance(at, int | float) and ((at > price) if side > 0 else (at < price)):
            agree = level.get("confluence") or 1
            what = str(level.get("label") or level.get("kind_label") or "level")
            return float(at), f"{what}{f' ({agree} sources agree)' if agree > 1 else ''}"
    return None, ""


def bracket(side: int, entry: float, stop: float, reward_multiple: float) -> float:
    """The target `reward_multiple` times the stop's distance away, on the trade's side."""
    return entry + side * reward_multiple * abs(entry - stop)


class Strategy(ABC):
    name: ClassVar[str]  # stable: settings, signals and structures key off it
    label: ClassVar[str]
    summary: ClassVar[str]
    stop_rule: ClassVar[str]  # where the stop goes, in words, for the dashboard
    target_rule: ClassVar[str]
    interval: ClassVar[str]
    lookback: ClassVar[int]  # closed bars of `interval` handed over
    wants_outlook: ClassVar[bool] = False
    wants_structure: ClassVar[bool] = False
    wants_options_history: ClassVar[bool] = False

    @abstractmethod
    def decide(self, ctx: Context) -> Decision: ...

    def describe(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("name", "label", "summary", "stop_rule", "target_rule", "interval", "lookback")}
