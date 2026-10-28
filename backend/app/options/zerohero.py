"""The expiry-day momentum buy, scored.

WHAT THIS IS BETTING ON
-----------------------
Two to four hours before a daily settlement, an option half a percent out of the money is cheap
and almost all gamma. If the underlying goes through its strike, delta runs from ~0.1 to ~0.6 and
the contract multiplies. If it does not, theta takes the lot. Both halves are large and both
happen fast, which is the entire proposition: it is not a cheap option, it is a **convexity trade
with a deadline**.

That is also why "it is only eight dollars" is the wrong way to size it. The premium is small and
the loss rate is high, so the thing that decides whether this makes or loses money over a hundred
attempts is not the entry price. It is the hit rate against the multiple — and nothing in this
module can promise either. What it can do is refuse the attempts where the arithmetic says no.

WHY A SELLER'S GATE CANNOT SCORE IT
-----------------------------------
`assess.py` requires a probability of profit above 55% and a positive expected value at realised
volatility. A long OTM buy fails both by construction: its POP is 10-15%, and on a fairly priced
chain the expected value of buying decay is negative. Running it through those checks would refuse
every single attempt, correctly, and tell you nothing. So the gate here is a different one, and
these five dimensions are it:

  PAYOFF        Can it even multiply? `pricing.reprice` at the target, with the clock advanced as
                well as the spot. A move that pays 3x is not this strategy, whatever it cost.
  PROBABILITY   `outlook.p_touch` over the hold, from REALISED vol. Combined with the payoff into
                one expected value per unit paid — the only number that ranks candidates.
  MOMENTUM      A fresh break, with displacement, with flow agreeing. Without this the scan is
                buying lottery tickets and the probability above is the only edge, which is no
                edge at all once the spread is paid.
  POSITIONING   Away from max pain, not toward it. On expiry day price is pulled to the strike
                that pays holders least; a contract on the far side of that pull is the one that
                decays to zero while being "right". This is a hard reject, not a score.
  TRADABILITY   Spread, open interest, and how much can actually be bought. An 8/10 quote is a
                20% loss the moment you touch it, and no budget is deployable into a strike that
                does not trade.

Pure: quotes, bars-derived readings and a clock in; a scan out. No network, no database, no
orders. `trading/zerohero.py` is what acts on it.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from app.analysis.fibonacci import Fib, next_extension
from app.analysis.orderflow import Flow
from app.analysis.outlook import p_touch, sigma_over
from app.analysis.pulse import Pulse
from app.analysis.structure import Structure as Smc
from app.market.options import OptionQuote
from app.options.pricing import multiple_at_move, reprice
from app.settings import Settings

# A break older than this many bars is not news; price has had time to do something with it.
MAX_BREAK_AGE = 3
# Pulse states that mean something is moving. A squeeze is the coil, not the release.
MOVING = ("expanding", "breakout", "surge", "trending")


# -- the directional trigger -------------------------------------------------------------------


@dataclass(frozen=True)
class Trigger:
    direction: int  # +1 up, -1 down, 0 none
    kind: str  # bos | choch | ""
    level: float | None
    bars_ago: int | None
    displacement: float  # the breaking bar's range against the recent mean
    volume_ratio: float
    pulse_state: str
    squeeze: bool
    flow: str  # buyers | sellers | balanced
    flow_agrees: bool
    target: float | None  # the fib extension the leg projects to
    target_pct: float | None  # that target as a move from here
    checks: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def trigger(smc: Smc, pulse: Pulse, flow: Flow, fib: Fib, price: float, s: Settings) -> Trigger:
    """Is something actually breaking, which way, and how far does the leg project?

    The break is read as a FRESH cross (`structure.find_breaks` already enforces that a bar cannot
    break a level it set itself) and is only taken while it is still recent. A level that broke an
    hour ago is a fact about the past; every bar since has been true of it, and a condition that is
    true every bar is not an entry.
    """
    last = smc.last_break
    direction = 0
    kind, level, bars_ago = "", None, None
    displacement = volume_ratio = 0.0
    if last is not None:
        bars_ago = max(0, (smc.bars - 1) - last.index)
        kind, level = last.kind, last.level
        displacement, volume_ratio = last.displacement, last.volume_ratio
        if bars_ago <= MAX_BREAK_AGE:
            direction = 1 if last.direction == "up" else -1

    flow_agrees = flow.pressure == ("buyers" if direction > 0 else "sellers") if direction else False
    ext = next_extension(fib, direction, price) if direction else None
    target = ext.price if ext else None
    target_pct = round(100 * (target - price) / price, 4) if target and price > 0 else None

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    check("fresh break", direction != 0,
          f"{kind.upper()} {('up' if direction > 0 else 'down') if direction else last.direction if last else '—'} "
          f"through {level:,.0f}, {bars_ago} bar(s) ago" if last else "no break in the series")
    check("displacement", displacement >= s.zh_displacement_atr,
          f"breaking bar {displacement:.2f}x the mean range (min {s.zh_displacement_atr:g}x)")
    check("volatility expanding", pulse.state in MOVING and not smc.squeeze,
          f"pulse is {pulse.state}" + (" but the band is still squeezed" if smc.squeeze else ""))
    check("flow agrees", flow_agrees, f"estimated pressure is {flow.pressure}")
    check("a target to aim at", target is not None,
          f"fib extension at {target:,.0f} ({target_pct:+.2f}%)" if target else "the leg projects nowhere in this direction")

    return Trigger(
        direction=direction, kind=kind, level=level, bars_ago=bars_ago,
        displacement=round(displacement, 3), volume_ratio=round(volume_ratio, 3),
        pulse_state=pulse.state, squeeze=smc.squeeze, flow=flow.pressure, flow_agrees=flow_agrees,
        target=target, target_pct=target_pct,
        checks=checks, ok=all(c["ok"] for c in checks),
    )


# -- positioning ---------------------------------------------------------------------------------


def positioning(summary: dict[str, Any], spot: float, direction: int, s: Settings) -> dict[str, Any]:
    """The dealers' side of it: where the pin is, and whether this trade runs into it or away.

    Max pain is the settlement price at which holders are paid least, and on expiry day price is
    drawn toward it. A contract bought on the pin's side of spot is fighting that pull with hours
    left and a decaying premium; it is the single most reliable way to be directionally right and
    still get zero.
    """
    pain = summary.get("max_pain")
    call_wall, put_wall = summary.get("call_wall"), summary.get("put_wall")
    buffer = spot * s.zh_max_pain_buffer_pct / 100
    if pain is None or direction == 0:
        away = False
        detail = "no max pain on this expiry" if pain is None else "no direction to judge"
    else:
        # Away from the pin means moving further from it, i.e. up when already above it.
        away = (spot > pain + buffer) if direction > 0 else (spot < pain - buffer)
        detail = (
            f"spot {spot:,.0f} is {'above' if spot > pain else 'below'} max pain {pain:,.0f}; "
            f"{'running away from' if away else 'running into'} the pin"
        )
    wall = call_wall if direction > 0 else put_wall
    return {
        "max_pain": pain,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "wall_ahead": wall,
        "wall_distance_pct": round(100 * (wall - spot) / spot, 3) if wall and spot else None,
        "pcr_oi": summary.get("pcr_oi"),
        "skew_25d": summary.get("skew_25d"),
        "atm_iv": summary.get("atm_iv"),
        "implied_move_pct": summary.get("implied_move_pct"),
        "away_from_pin": away,
        "detail": detail,
    }


# -- candidates ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    symbol: str
    kind: str
    strike: float
    distance_pct: float
    paid: float  # the ask, which is what a buy actually costs
    mark: float | None
    bid: float | None
    iv: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    spread_pct: float | None
    oi: float
    volume: float
    contract_value: float
    hold_minutes: float
    target_pct: float
    multiple: float | None  # what one unit is worth at the target, over what it cost
    value_at_target: float | None
    decay_if_flat: float | None  # the same unit if nothing happens: the other half of the trade
    p_touch: float | None
    ev: float | None  # per unit paid, after the exit haircut
    capacity_lots: int
    checks: list[dict[str, Any]]
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _haircut(q: OptionQuote) -> float:
    """What the book takes on the way out: half the spread, because `reprice` values at the mid and
    an exit sells the bid. No two-sided quote and the liquidity check has already failed."""
    return min(50.0, (q.spread_pct or 0.0) / 2)


def capacity(q: OptionQuote, s: Settings) -> int:
    """How many lots this strike can absorb — a PROXY, and named one.

    Delta's ticker carries no book depth, so the ceiling here is a share of what the contract
    actually trades: the smaller of its open interest and its 24h volume, in the underlying. It is
    not the resting ask size and will be wrong in both directions on a fast-moving strike. It is
    still the difference between sizing into something that trades and sizing into a number.
    """
    traded = min(q.oi, q.volume) if q.volume > 0 else q.oi
    if traded <= 0 or q.contract_value <= 0:
        return 0
    return int(traded * s.zh_capacity_share_pct / 100 / q.contract_value)


def score(
    q: OptionQuote, spot: float, direction: int, target_pct: float, hours_left: float,
    hold_minutes: float, rv: float | None, s: Settings,
) -> Candidate:
    """One contract, against the three per-contract dimensions."""
    paid = q.ask or q.mark or 0.0
    distance_pct = 100 * (q.strike - spot) / spot * (1 if direction > 0 else -1)
    haircut = _haircut(q)

    multiple = value = decay = None
    if paid > 0 and q.iv:
        value = reprice(q.kind, q.strike, spot, target_pct, q.iv, hours_left, hold_minutes) * (1 - haircut / 100)
        multiple = multiple_at_move(q.kind, q.strike, paid, spot, target_pct, q.iv, hours_left, hold_minutes, haircut)
        decay = reprice(q.kind, q.strike, spot, 0.0, q.iv, hours_left, hold_minutes) * (1 - haircut / 100) / paid

    # Probability the move happens at all, from REALISED volatility over the hold — not from the
    # chain's IV, which is the price being paid and cannot also be the evidence for paying it.
    p = None
    if rv and rv > 0 and hold_minutes > 0:
        # Both arguments are fractions OF PRICE, which is what `sigma_over` returns.
        p = p_touch(abs(target_pct) / 100, sigma_over(rv, hold_minutes / 60))

    # Expected value per unit paid. A clean exit at the touch and a clean stop otherwise; both are
    # optimistic, and slippage in the last hour makes both worse than this says.
    stop_fraction = s.zh_stop_loss_pct / 100
    ev = p * (multiple - 1) - (1 - p) * stop_fraction if p is not None and multiple is not None else None

    lots = capacity(q, s)
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    check("distance", s.zh_min_distance_pct <= distance_pct <= s.zh_max_distance_pct,
          f"{distance_pct:+.2f}% out of the money (want {s.zh_min_distance_pct:g}-{s.zh_max_distance_pct:g}%)")
    check("payoff", multiple is not None and multiple >= s.zh_min_multiple,
          f"{multiple:.1f}x at {target_pct:+.2f}% in {hold_minutes:.0f} min, {decay:.2f}x if flat (min {s.zh_min_multiple:g}x)"
          if multiple is not None else "no IV or ask to reprice from")
    check("expected value", ev is not None and ev >= s.zh_min_ev,
          f"{ev:+.2f} per unit paid at p={100 * p:.1f}% (min {s.zh_min_ev:+g})"
          if ev is not None else "no realised volatility to price the move with")
    check("spread", q.spread_pct is not None and q.spread_pct <= s.zh_max_spread_pct,
          f"{q.spread_pct:.1f}% of the mark (max {s.zh_max_spread_pct:g}%)" if q.spread_pct is not None
          else "no two-sided quote")
    check("open interest", q.oi >= s.zh_min_oi, f"{q.oi:,.1f} {q.underlying} open (min {s.zh_min_oi:g})")
    check("capacity", lots > 0, f"about {lots:,} lots at {s.zh_capacity_share_pct:g}% of traded size (proxy)")

    return Candidate(
        symbol=q.symbol, kind=q.kind, strike=q.strike, distance_pct=round(distance_pct, 3),
        paid=round(paid, 4), mark=q.mark, bid=q.bid, iv=q.iv, delta=q.delta, gamma=q.gamma, theta=q.theta,
        spread_pct=q.spread_pct, oi=q.oi, volume=q.volume, contract_value=q.contract_value,
        hold_minutes=round(hold_minutes, 1), target_pct=round(target_pct, 4),
        multiple=None if multiple is None else round(multiple, 3),
        value_at_target=None if value is None else round(value, 4),
        decay_if_flat=None if decay is None else round(decay, 3),
        p_touch=None if p is None else round(p, 4),
        ev=None if ev is None else round(ev, 4),
        capacity_lots=lots,
        checks=checks, ok=all(c["ok"] for c in checks),
    )


# -- the scan ----------------------------------------------------------------------------------------


@dataclass
class Scan:
    underlying: str
    at: float
    spot: float
    expiry: float | None
    hours_left: float | None
    window_open: bool
    note: str
    hold_minutes: float | None = None
    realised_vol: float | None = None
    trigger: dict[str, Any] | None = None
    positioning: dict[str, Any] | None = None
    gates: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    best: dict[str, Any] | None = None
    ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def window(hours_left: float, s: Settings) -> tuple[bool, str]:
    if hours_left > s.zh_window_start_hours:
        return False, f"{hours_left:.1f}h to expiry: the window opens at {s.zh_window_start_hours:g}h"
    if hours_left < s.zh_window_end_hours:
        return False, f"{hours_left:.1f}h to expiry: the window closed at {s.zh_window_end_hours:g}h"
    return True, f"{hours_left:.1f}h to expiry: inside the {s.zh_window_start_hours:g}-{s.zh_window_end_hours:g}h window"


def scan(
    underlying: str, quotes: list[OptionQuote], spot: float, now: float, summary: dict[str, Any],
    smc: Smc, pulse: Pulse, flow: Flow, fib: Fib, rv: float | None, s: Settings,
) -> Scan:
    """Every dimension, on one expiry's chain. Candidates are ranked by expected value and the list
    of what was refused, and why, is returned with them — a scan that finds nothing has to be able
    to say which of the five it was."""
    if not quotes:
        return Scan(underlying, now, spot, None, None, False, "no live options for this expiry")

    expiry = quotes[0].expiry
    hours_left = max(0.0, (expiry - now) / 3600)
    is_open, note = window(hours_left, s)
    # The hold is not a preference: it is whatever is left between now and the time stop.
    hold_minutes = max(0.0, hours_left * 60 - s.zh_exit_minutes)
    out = Scan(underlying, now, spot, expiry, round(hours_left, 3), is_open, note,
               hold_minutes=round(hold_minutes, 1), realised_vol=rv)

    trig = trigger(smc, pulse, flow, fib, spot, s)
    pos = positioning(summary, spot, trig.direction, s)
    out.trigger, out.positioning = trig.to_dict(), pos

    gates: list[dict[str, Any]] = [
        {"name": "window", "ok": is_open, "detail": note},
        {"name": "momentum", "ok": trig.ok, "detail": "; ".join(c["detail"] for c in trig.checks if not c["ok"]) or "a fresh break with size behind it"},
        {"name": "away from the pin", "ok": pos["away_from_pin"], "detail": pos["detail"]},
        {"name": "hold time", "ok": hold_minutes > 0, "detail": f"{hold_minutes:.0f} min before the {s.zh_exit_minutes} min time stop"},
        {"name": "realised volatility", "ok": bool(rv and rv > 0), "detail": f"{100 * rv:.1f}% annualised" if rv else "not enough stored bars"},
    ]
    out.gates = gates
    if not all(g["ok"] for g in gates):
        return out

    want = "call" if trig.direction > 0 else "put"
    target_pct = trig.target_pct or 0.0
    scored = [
        score(q, spot, trig.direction, target_pct, hours_left, hold_minutes, rv, s)
        for q in quotes
        if q.kind == want and (q.strike > spot if trig.direction > 0 else q.strike < spot) and (q.ask or q.mark)
    ]
    passed = sorted([c for c in scored if c.ok], key=lambda c: c.ev or 0, reverse=True)
    out.candidates = [c.to_dict() for c in passed]
    out.rejected = [
        {"symbol": c.symbol, "strike": c.strike, "paid": c.paid,
         "why": next(ch["name"] for ch in c.checks if not ch["ok"]),
         "detail": next(ch["detail"] for ch in c.checks if not ch["ok"])}
        for c in scored if not c.ok
    ]
    out.best = out.candidates[0] if out.candidates else None
    out.ok = out.best is not None
    if not out.ok:
        out.note = f"{len(scored)} {want}s priced, none passed all six contract checks"
    return out


# -- sizing -------------------------------------------------------------------------------------------


def lots_for_budget(equity: float, spent_today: float, attempts_today: int, paid: float,
                    contract_value: float, capacity_lots: int, s: Settings) -> tuple[int, str]:
    """How many lots this attempt gets, and the sentence explaining it.

    The budget is the DAY's, divided by the attempts the day still has. Dividing by the REMAINING
    attempts rather than the maximum is deliberate: the last attempt of a quiet day may use what
    the first two did not, and the first attempt of any day can never use more than its share.
    """
    budget = equity * s.zh_daily_budget_pct / 100
    remaining_budget = max(0.0, budget - spent_today)
    remaining_attempts = max(0, s.zh_max_attempts_day - attempts_today)
    if remaining_attempts <= 0:
        return 0, f"{attempts_today} of {s.zh_max_attempts_day} attempts already used today"
    if remaining_budget <= 0:
        return 0, f"the {s.zh_daily_budget_pct:g}% daily budget ({budget:,.2f}) is spent"
    allowance = remaining_budget / remaining_attempts
    unit = paid * contract_value
    if unit <= 0:
        return 0, "no price to size against"
    lots = math.floor(allowance / unit)
    reason = f"{allowance:,.2f} of a {budget:,.2f} daily budget over {remaining_attempts} attempt(s) left"
    if lots > capacity_lots:
        lots, reason = capacity_lots, reason + f", clamped to {capacity_lots:,} lots of capacity"
    if lots > s.zh_max_lots:
        lots, reason = s.zh_max_lots, reason + f", clamped to the {s.zh_max_lots:,} lot cap"
    return max(0, lots), reason
