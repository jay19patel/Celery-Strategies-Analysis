"""Whether an open position is still worth holding — priced, not guessed.

The engine already decides how far a stop sits and how big a position is (`trading.Trader`).
This is the other half: once a position is open and neither barrier has been touched, is holding
it still the best use of the slot? The Trader never asks, so until now the only ways out were the
stop, the target, the trailing ratchet and the max hold.

Everything here is arithmetic over the position's own levels and the measured volatility, in the
spirit of invariant 15: nothing is fitted and nothing is guessed. `outlook.py` already carries the
arithmetic (`p_stop_first`, `p_neither`, `sigma_over`) and `tests/test_outlook.py` checks it
against simulation rather than against itself.

WHAT THE ARITHMETIC CAN AND CANNOT SAY
--------------------------------------
Run the numbers on a live bracketed position and `EV(hold)` comes out equal to `EV(close now)`,
to the cent. That is not a bug, it is the martingale property: for a driftless walk the expected
value of holding a bracket IS its current value. Counting the stored bars says the walk really is
driftless here — over 35,488 stored 15m bars, price was higher an hour later 50.1% of the time,
four hours later 50.2%, a day later 50.4%, which at this sample is a coin.

So the probability arithmetic can never, on its own, say "close". It is fair by construction.
What it CAN do is price the decision honestly, which is worth having on its own: a position whose
stop is 0.42% away and whose target is 1.21% away looks like a 1:2.9 trade and is in fact a 74.3%
chance of being stopped first (`p_stop_first` is target/(stop+target) — volatility cancels).

An exit therefore has to earn its reason somewhere the coin cannot reach, and there are only four
such places. They are the four rules below, strongest first.

THE RULES
---------
INVALIDATED  the reason for the trade is gone — a break back through the level, a close back
             inside the range the breakout left. Structural, not probabilistic: no sample size,
             no calibration, and it is the only one that is true regardless of what the coin does.

STALLED      price has spent most of the risk and made none of the progress. Scale-free on
             purpose: measured as a share of the position's OWN stop and target distances, not in
             percent and not in ATR. A fixed percentage is a different trade on BTC (15m ATR
             0.14% of price) than on ETH (0.21%), and fitting one to 28 trades puts the threshold
             exactly at the edge of the observed winners — which is what overfitting looks like.

TIMEOUT      the horizon is nearly up and neither barrier is in reach. `p_neither` is where this
             lands, and it only bites when the horizon is short enough to bind: at a 72-hour max
             hold the chance of a 15m position timing out is 0.0%, so the barrier was decoration.
             At 4 hours it is 11.8%, at 2 hours 38.7%.

CARRY        holding costs funding, and funding is collected on every stored bar. A position with
             no expected gain and a real carry is a position being paid to close.

Pure: bars and numbers in, a decision out. No clock, no network, no database — the guard, a
backtest and the dashboard all get the same answer, the same way `structure.py`, `outlook.py` and
`verdict.py` do.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from tradebuddy import outlook

# Actions, in the order the guard applies them.
HOLD = "hold"
CLOSE = "close"

# Rule names, as they appear in the event and on the page.
INVALIDATED = "invalidated"
STALLED = "stalled"
TIMEOUT = "timeout"
CARRY = "carry"

HOUR = 3600.0


@dataclass(frozen=True)
class Params:
    """Every threshold the rules use, so they are visible in one place and settable from Settings.

    The defaults are deliberately slack. Nothing here was fitted — the stored sample (28
    directional trades) is far too small to calibrate on, and the one sweep that was run over it
    put its optimum exactly at the worst winner's excursion. These are starting values for the
    backtest to move, not measurements.
    """

    horizon_hours: float = 8.0  # how long this position is given to resolve
    # STALLED: closed when BOTH are true — most of the risk spent, almost none of the ground made.
    stalled_adverse: float = 0.75  # share of the stop distance already travelled against it
    stalled_progress: float = 0.25  # share of the target distance ever travelled in its favour
    stalled_min_hours: float = 1.0  # not before this: a position needs room to breathe
    # TIMEOUT: closed when the chance of either barrier resolving before the horizon falls here.
    timeout_min_resolve: float = 0.20
    # CARRY: closed when funding over the remaining horizon eats this share of the target.
    carry_max_share: float = 0.50

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Pricing:
    """What holding is worth, as a % of the entry price. Reported whether or not anything acts.

    `ev_hold` and `ev_exit` come out equal for a driftless walk; that is the point. They are here
    so the page can show the honest odds next to the flattering reward-to-risk ratio.
    """

    stop_pct: float  # distance from the current price to the stop, as a % of price
    target_pct: float  # and to the target
    value_now: float  # closing now, as a % of entry, net of the exit cost
    value_stop: float  # if the stop is hit
    value_target: float  # if the target is hit
    p_stop: float
    p_target: float
    p_timeout: float
    ev_hold: float
    ev_exit: float
    reward_risk: float  # what the ticket looks like — shown so the odds can be read beside it
    sigma: float  # volatility over the remaining horizon, as a fraction of price
    hours_left: float

    @property
    def edge(self) -> float:
        """What holding is worth over closing. Zero for a fair coin — see the module docstring."""
        return self.ev_hold - self.ev_exit

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"edge": round(self.edge, 6)}


@dataclass(frozen=True)
class Decision:
    action: str  # HOLD | CLOSE
    rule: str  # "" when holding
    reason: str  # one sentence, always filled: a hold says why it is still open
    pricing: Pricing | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def closing(self) -> bool:
        return self.action == CLOSE

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action, "rule": self.rule, "reason": self.reason,
            "pricing": self.pricing.to_dict() if self.pricing else None, "detail": self.detail,
        }


def _direction(side: str) -> int:
    return 1 if side in ("long", "buy") else -1


def price_position(
    side: str, entry: float, price: float, stop: float | None, target: float | None,
    rv_annual: float, hours_left: float, cost_pct: float,
) -> Pricing | None:
    """The honest odds on an open bracketed position. None when it is not bracketed both ways.

    Values are in % of the entry price and already net of what closing costs, so `value_stop` on a
    stop that has been trailed into profit is a positive number — the guard must be able to tell
    "my worst case is a smaller win" from "my worst case is a loss" (invariant 7b: an exit books
    what it reported).
    """
    if entry <= 0 or price <= 0 or not stop or not target:
        return None
    d = _direction(side)
    stop_pct = 100 * abs(price - stop) / price
    target_pct = 100 * abs(price - target) / price
    if stop_pct <= 0 or target_pct <= 0:
        return None

    one_side = cost_pct / 2  # the exit half of the round trip; the entry half is already paid

    def value(level: float) -> float:
        return 100 * d * (level - entry) / entry - one_side

    value_now, value_stop, value_target = value(price), value(stop), value(target)

    sigma = outlook.sigma_over(rv_annual, max(hours_left, 0.0))
    p_neither = outlook.p_neither(stop_pct, target_pct, sigma)
    share_stop = outlook.p_stop_first(stop_pct, target_pct)
    if share_stop is None:
        return None
    resolve = max(0.0, 1.0 - p_neither)
    p_stop = resolve * share_stop
    p_target = resolve * (1.0 - share_stop)
    # A position that times out is closed at the market, which for a driftless walk is worth what
    # it is worth now. Assuming anything else here is assuming the drift the counting denies.
    ev_hold = p_target * value_target + p_stop * value_stop + p_neither * value_now
    return Pricing(
        stop_pct=round(stop_pct, 4), target_pct=round(target_pct, 4),
        value_now=round(value_now, 4), value_stop=round(value_stop, 4), value_target=round(value_target, 4),
        p_stop=round(p_stop, 4), p_target=round(p_target, 4), p_timeout=round(p_neither, 4),
        ev_hold=round(ev_hold, 4), ev_exit=round(value_now, 4),
        reward_risk=round(target_pct / stop_pct, 3),
        sigma=round(sigma, 6), hours_left=round(hours_left, 3),
    )


def stalled(
    adverse_pct: float | None, favourable_pct: float | None, stop_from_entry: float,
    target_from_entry: float, held_hours: float, p: Params,
) -> tuple[bool, dict[str, Any]]:
    """Most of the risk spent, almost none of the ground made.

    Both excursions are measured against the position's OWN distances, so the rule means the same
    thing on a 0.2% scalp stop and a 4% swing stop. `adverse_pct` and `favourable_pct` are the
    worst and best moves the position has seen, as a % of entry — what the paper broker records as
    `mae_pct` and `mfe_pct`. Either missing and the rule abstains rather than guessing (invariant
    8: absence over staleness).
    """
    if adverse_pct is None or favourable_pct is None or stop_from_entry <= 0 or target_from_entry <= 0:
        return False, {"available": False, "why": "this broker does not record the excursions"}
    spent = abs(min(adverse_pct, 0.0)) / stop_from_entry
    made = max(favourable_pct, 0.0) / target_from_entry
    detail = {
        "available": True, "risk_spent": round(spent, 4), "progress_made": round(made, 4),
        "risk_spent_limit": p.stalled_adverse, "progress_limit": p.stalled_progress,
        "held_hours": round(held_hours, 3),
    }
    if held_hours < p.stalled_min_hours:
        return False, detail | {"why": f"held {held_hours:.1f}h, under the {p.stalled_min_hours:g}h this rule waits"}
    return (spent >= p.stalled_adverse and made <= p.stalled_progress), detail


def carry_cost_pct(funding_rate_pct: float | None, side: str, hours_left: float) -> float:
    """What holding costs in funding over the remaining horizon, as a % of notional.

    Delta's funding is charged every eight hours; a positive rate is paid by longs. A short with a
    positive rate is being PAID to hold, which is a negative cost and must stay negative here — a
    rule that treated carry as always adverse would close the one position the market is subsidising.
    """
    if funding_rate_pct is None or hours_left <= 0:
        return 0.0
    periods = hours_left / 8.0
    return funding_rate_pct * periods * _direction(side)


def outlook_invalidation(side: str, view: dict[str, Any] | None, intervals: tuple[str, ...] = ("15m", "1h")) -> str:
    """Why the engine's own directional read no longer supports this position, or "".

    Not a forecast — counting the stored bars says direction is a coin at every horizon this
    system trades, and nothing here pretends otherwise. It is a PERMISSION check, and the same one
    the entry had to pass: `scalper` refuses a trade unless 15m and 1h agree, `trade_buddy_manager`
    refuses one with a higher timeframe against it. A position whose entry needed that agreement
    and no longer has it has lost the reason it was opened for, whatever price has done since.

    Reading `ctx.outlook` rather than working a direction out here is the same rule the strategies
    follow: one reading, so the page, the strategy and the exit can never disagree about which way
    the market is pointing.
    """
    frames = (view or {}).get("timeframes") or []
    if not frames:
        return ""
    want = _direction(side)
    against = [
        f for f in frames
        if f.get("interval") in intervals and f.get("direction") and int(f["direction"]) == -want
    ]
    if not against:
        return ""
    names = ", ".join(str(f["interval"]) for f in against)
    return (
        f"the {names} read has turned against this {side} — the agreement the entry needed is gone"
    )


def decide(
    *,
    side: str,
    entry_price: float,
    price: float,
    stop_loss: float | None,
    take_profit: float | None,
    held_hours: float,
    rv_annual: float,
    cost_pct: float,
    mae_pct: float | None = None,
    mfe_pct: float | None = None,
    funding_rate_pct: float | None = None,
    invalidation: str = "",
    params: Params | None = None,
) -> Decision:
    """Whether to keep holding. Pure.

    `invalidation` is the one input this module does not work out for itself: the caller passes the
    sentence explaining why the trade's own reason is gone, or "" when it stands. It belongs to
    whoever knows the thesis — the strategy that opened the position and the structure read the
    engine already builds — not here.
    """
    p = params or Params()
    hours_left = max(0.0, p.horizon_hours - held_hours)
    pricing = price_position(side, entry_price, price, stop_loss, take_profit, rv_annual, hours_left, cost_pct)

    # 1. INVALIDATED — structural, and true whatever the coin is doing.
    if invalidation:
        return Decision(CLOSE, INVALIDATED, invalidation, pricing)

    if pricing is None:
        return Decision(HOLD, "", "not bracketed on both sides — nothing here can price it", None)

    d = _direction(side)
    stop_from_entry = 100 * abs(stop_loss - entry_price) / entry_price if stop_loss else 0.0
    target_from_entry = 100 * abs(take_profit - entry_price) / entry_price if take_profit else 0.0

    # 2. STALLED — most of the risk spent, none of the ground made.
    is_stalled, stall = stalled(mae_pct, mfe_pct, stop_from_entry, target_from_entry, held_hours, p)
    if is_stalled:
        return Decision(
            CLOSE, STALLED,
            f"spent {stall['risk_spent']:.0%} of the stop and made {stall['progress_made']:.0%} of the target "
            f"in {held_hours:.1f}h — the move this was opened for has not happened",
            pricing, {"stalled": stall},
        )

    # 3. TIMEOUT — the horizon is nearly up and neither barrier is in reach.
    resolve = pricing.p_stop + pricing.p_target
    if hours_left <= 0:
        return Decision(
            CLOSE, TIMEOUT, f"the {p.horizon_hours:g}h this position was given is up", pricing,
            {"hours_left": 0.0, "resolve": round(resolve, 4)},
        )
    if resolve < p.timeout_min_resolve:
        return Decision(
            CLOSE, TIMEOUT,
            f"{resolve:.0%} chance either level is reached in the {hours_left:.1f}h left "
            f"(under {p.timeout_min_resolve:.0%}) — this is capital standing still",
            pricing, {"hours_left": round(hours_left, 3), "resolve": round(resolve, 4)},
        )

    # 4. CARRY — funding over the remaining horizon against what is left to win.
    carry = carry_cost_pct(funding_rate_pct, side, hours_left)
    if carry > 0 and pricing.target_pct > 0 and carry >= p.carry_max_share * pricing.target_pct:
        return Decision(
            CLOSE, CARRY,
            f"funding costs {carry:.3f}% over the {hours_left:.1f}h left, against {pricing.target_pct:.2f}% "
            f"still to win — the carry is eating the trade",
            pricing, {"carry_pct": round(carry, 5)},
        )

    return Decision(
        HOLD, "",
        f"{pricing.p_target:.0%} chance of the target and {pricing.p_stop:.0%} of the stop over the next "
        f"{hours_left:.1f}h; worst case here is {pricing.value_stop:+.2f}%",
        pricing,
        {"stalled": stall, "carry_pct": round(carry, 5), "direction": d},
    )
