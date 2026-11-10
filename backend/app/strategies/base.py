"""What a strategy is. Every file in this package that defines a concrete subclass of `Strategy`
(a position on the perpetual) or `OptionStrategy` (an option structure) is a strategy: the registry
finds it, the settings switch it, the dashboard draws its form from its `Params`. Delete the file and
it is gone. A file whose name starts with "_" holds shared helpers and is never registered.

A strategy only proposes. It never places an order, never sizes, never reads an account: the
trading service gates, sizes and executes, the same for every strategy, so a backtest runs exactly
the code that trades.

Perpetual: `decide()` reads closed bars of one timeframe (plus what it declares it wants) and
returns a `Decision` — no trade with the reason, or a side with its **own** stop and target as
prices. The stop sits where the idea stops being true, so every trade's risk is the strategy's.

Options: `expiry()` picks the expiry, `score()` says how well today's regime fits, `build()` picks
the strikes. Pricing, the checks, sizing and the exits are shared.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.market import options as chain
from app.market.delta import Candle
from app.market.options import OptionQuote
from app.options.strategies import SELLER_CHECKS, Picked, Plan, build_plan

DEFAULT_HOLD_HOURS = 24.0  # a perpetual position's time limit when its strategy sets none


class Params(BaseModel):
    """A strategy's tunable numbers: defaults, bounds, and the form the dashboard draws."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# -- perpetual ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    symbol: str  # BTCUSD
    underlying: str  # BTC
    interval: str
    candles: list[Candle]  # closed bars of `interval`, oldest first; the last one just closed
    outlook: dict[str, Any] = field(default_factory=dict)  # when the strategy wants_outlook
    structure: dict[str, Any] = field(default_factory=dict)  # when it wants_structure
    flow: dict[str, Any] = field(default_factory=dict)  # the tape's reading (`analysis.flow`), when it wants_flow
    bias: dict[str, Any] = field(default_factory=dict)  # the day's bias (`analysis.bias`), when it wants_bias
    cost_pct: float = 0.0  # one round trip, % of notional: fees both ways, the spread and expected funding


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
    """A position on the perpetual."""

    family: ClassVar[str] = "perp"
    name: ClassVar[str]  # stable: settings, signals and structures key off it
    version: ClassVar[int] = 1  # bump when decide() changes what it trades
    label: ClassVar[str]
    summary: ClassVar[str]
    stop_rule: ClassVar[str]  # where the stop goes, in words, for the dashboard
    target_rule: ClassVar[str]
    interval: ClassVar[str]
    lookback: ClassVar[int]  # closed bars of `interval` handed over
    default_on: ClassVar[bool] = False  # a new strategy starts off until it is switched on
    wants_outlook: ClassVar[bool] = False
    wants_structure: ClassVar[bool] = False
    wants_flow: ClassVar[bool] = False
    wants_bias: ClassVar[bool] = False
    Params: ClassVar[type[Params]] = Params

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.p: Any = self.Params.model_validate(params or {})

    @property
    def max_hold_hours(self) -> float:
        """Its own time limit: a `max_hold_hours` param, else `DEFAULT_HOLD_HOURS`."""
        return float(getattr(self.p, "max_hold_hours", DEFAULT_HOLD_HOURS))

    @abstractmethod
    def decide(self, ctx: Context) -> Decision: ...

    def describe(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in (
            "name", "family", "version", "label", "summary", "stop_rule", "target_rule", "interval", "lookback", "max_hold_hours",
        )}


# -- options --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OptionContext:
    """One underlying's option book, read once per cycle and shared by every option strategy."""

    underlying: str
    spot: float
    now: float
    quotes: list[OptionQuote]  # every fresh quote of the underlying, all expiries
    regime: Callable[[float], dict[str, Any]]  # the regime read at one expiry (`options.regime.analyze`)
    realised: dict[str, float | None]  # `Desk.realised`: reference (highest) and floor (lowest) among others
    fee: Callable[[float], float]  # one leg's fee per unit of the underlying at a premium
    max_spread_pct: float

    def chain(self, expiry: float) -> list[OptionQuote]:
        return [q for q in self.quotes if q.expiry == expiry]


class OptionParams(Params):
    """What every scored option structure shares: which expiry, and the gate a seller passes."""

    expiry_min_hours: float = Field(12.0, ge=1, le=720, description="Nearest expiry at least this far away")
    expiry_max_hours: float = Field(60.0, ge=2, le=2000, description="...and at most this far")
    min_pop_pct: float = Field(55.0, ge=0, le=95, description="Probability of profit at expiry, from the ATM IV")
    # At 0.18 delta and 2% wings a FAIR credit is about 6-10% of the width; whether it pays is what
    # "edge at realised vol" measures. This keeps the scraps out.
    min_credit_width_pct: float = Field(5.0, ge=0, le=60, description="Credit as % of the widest spread")

    @model_validator(mode="after")
    def _window(self) -> OptionParams:
        if self.expiry_min_hours >= self.expiry_max_hours:
            raise ValueError("expiry_min_hours must be below expiry_max_hours")
        return self


class OptionStrategy(ABC):
    """An option structure the auto-trader can pick."""

    family: ClassVar[str] = "options"
    name: ClassVar[str]
    version: ClassVar[int] = 1
    label: ClassVar[str]
    view: ClassVar[str]  # neutral | bullish | bearish | volatile
    premium: ClassVar[str]  # credit | debit
    risk: ClassVar[str]  # defined | undefined
    summary: ClassVar[str]
    required_checks: ClassVar[tuple[str, ...]] = SELLER_CHECKS  # which `assess` checks bind
    default_on: ClassVar[bool] = False
    Params: ClassVar[type[Params]] = OptionParams

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.p: Any = self.Params.model_validate(params or {})

    @property
    def min_pop_pct(self) -> float:
        return float(getattr(self.p, "min_pop_pct", 55.0))

    @property
    def min_credit_width_pct(self) -> float:
        return float(getattr(self.p, "min_credit_width_pct", 5.0))

    def expiry(self, ctx: OptionContext) -> float | None:
        """The expiry it trades: the nearest inside its window."""
        return chain.pick_expiry(ctx.quotes, self.p.expiry_min_hours, self.p.expiry_max_hours, ctx.now)

    def expiry_note(self) -> str:
        return f"no expiry between {self.p.expiry_min_hours:g}h and {self.p.expiry_max_hours:g}h away"

    @abstractmethod
    def score(self, regime: dict[str, Any]) -> tuple[int, list[str]]:
        """0-100 for how well this regime fits, and every point's reason."""

    def pick(self, quotes: list[OptionQuote], spot: float) -> Picked:
        """The strikes, as (quote, side) pairs. A structure built from a strike rule overrides this."""
        raise NotImplementedError

    def build(self, ctx: OptionContext, expiry: float) -> Plan | None:
        """The structure on one expiry, or None when that chain cannot make it."""
        return build_plan(self.name, self.pick(ctx.chain(expiry), ctx.spot), ctx.spot)

    def describe(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("name", "family", "version", "label", "view", "premium", "risk", "summary")} | {
            "required_checks": list(self.required_checks),
        }
