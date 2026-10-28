"""The auto-trader: one decision per underlying every `auto_interval_minutes`.

  1. expiry   the nearest between `expiry_min_hours` and `expiry_max_hours`
  2. regime   volatility premium and direction, a rule score per strategy (`options.regime`)
  3. build    every enabled strategy scoring at least the bar, assessed at one lot. The bar is
              `min_score`, eased towards `min_score_floor` as a day with no entry on this
              underlying runs out — the one number pacing towards about a trade a day

  4. filter   only those passing every required check (liquidity, POP, edge at realised vol, ...)
  5. review   with AI on, a model approves one of the top three or skips; it cannot invent one
  6. size     lots whose worst case is `risk_per_trade_pct` of that broker's equity
  7. open     through `TradingService.open` — the same gate a dashboard click goes through

Every cycle is a `Decision` row: what it saw, every strategy it considered and why each was
dropped, what the model said, and what each broker did — including every skip, so "why did it not
trade?" always has an answer.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from sqlmodel import col, select

from app.ai.analyst import Analyst
from app.ai.llm import LLMError
from app.core.db import session
from app.core.events import EventHub
from app.core.models import Decision, Structure
from app.errors import AppError, BrokerError, Refused
from app.market import options as chain
from app.options.strategies import CATALOG, ZERO_HERO, Plan
from app.settings import Settings
from app.trading.desk import Desk
from app.trading.risk import day_start, trading_day
from app.trading.service import HELD, TradingService, is_directional

log = logging.getLogger(__name__)
MAX_CANDIDATES = 3


def unit_risk(assessment: dict[str, Any], s: Settings) -> float:
    """The worst case of one lot: the max loss when it is bounded, else the loss at the stop."""
    a = assessment["analysis"]
    if a["max_loss"] is not None:
        return a["max_loss"] + a["round_trip_fees"]
    return abs(a["net_premium"]) * s.credit_stop_loss_pct / 100 + a["round_trip_fees"]


def lots_for_risk(equity: float, assessment: dict[str, Any], s: Settings) -> int:
    risk = unit_risk(assessment, s)
    if risk <= 0:
        return 0
    return min(s.max_lots, math.floor(equity * s.risk_per_trade_pct / 100 / risk))


def _edge(c: dict[str, Any]) -> float:
    a = c["analysis"]
    ev = a.get("expected_value_realised") or 0.0
    return ev / a["max_loss"] if a["max_loss"] else ev


def _compact(assessment: dict[str, Any]) -> dict[str, Any]:
    a = {k: v for k, v in assessment["analysis"].items() if k != "curve"}
    legs = [{k: leg[k] for k in ("symbol", "side", "kind", "strike", "price")} for leg in assessment["plan"]["legs"]]
    return {"analysis": a, "legs": legs, "checks": assessment["checks"], "ok": assessment["ok"]}


class AutoTrader:
    def __init__(self, desk: Desk, trading: TradingService, analyst: Analyst, settings: Any, hub: EventHub) -> None:
        self.desk = desk
        self.trading = trading
        self.analyst = analyst
        self.settings: Any = settings
        self.hub = hub

    def brokers(self) -> list[str]:
        s: Settings = self.settings()
        return [b for b in s.active_brokers if s.auto_on(b)]

    async def cycle(self) -> str:
        if not self.settings().auto_options:
            return "options auto-trading is switched off"
        brokers = self.brokers()
        if not brokers:
            return "auto-trading is off on every active broker"
        out = []
        for underlying in self.settings().underlyings:
            d = await self.decide(underlying, brokers)
            out.append(f"{underlying}: {d['action']}{' ' + d['strategy'] if d['strategy'] else ''}")
        return "; ".join(out)

    def _room(self, broker: str, underlying: str, s: Settings) -> str:
        """Why `broker` cannot take another auto entry on `underlying`, or "".

        Only option structures are counted. A position on the perpetual holds its own slot, gated
        per symbol in `TradingService.gate_directional`, so BTC futures never block a BTC option
        structure and neither one blocks the other underlying — the same split `gate` already makes.
        """
        with session() as db:
            rows = db.exec(select(Structure).where(Structure.broker == broker, col(Structure.status).in_(HELD))).all()
        held = [st for st in rows if not is_directional(st)]
        if len(held) >= s.max_open_structures:
            return f"holds {len(held)} option structures (max {s.max_open_structures})"
        if sum(st.underlying == underlying for st in held) >= s.max_per_underlying:
            return f"already holds {underlying} options (max {s.max_per_underlying})"
        return ""

    def _bar(self, underlying: str, s: Settings, now: float | None = None) -> tuple[int, str]:
        """The score a strategy must beat this cycle, and the note that explains it.

        `min_score` is the bar at the start of the trading day. With `min_score_floor` set and
        nothing opened on this underlying yet today, it eases towards that floor as the day runs
        out — hold out for a good fit early, take the best there is late. The first entry of the day
        puts the bar back up: the floor paces the desk to about one entry a day, it never licenses a
        second one. Only this bar moves; every hard check runs at full strength at any hour.
        """
        if s.min_score_floor >= s.min_score:
            return s.min_score, ""
        now = time.time() if now is None else now
        day = trading_day(s.day_timezone, now)
        start = day_start(s.day_timezone, day)
        if self._opened_today(underlying, start):
            return s.min_score, ""
        through = min(max((now - start) / 86_400, 0.0), 1.0)
        bar = round(s.min_score - (s.min_score - s.min_score_floor) * through)
        return bar, f"nothing opened on {underlying} today: the bar has eased from {s.min_score} to {bar} ({through:.0%} through {day})"

    @staticmethod
    def _opened_today(underlying: str, since: float) -> bool:
        """Has an option structure on this underlying been opened since the day began? Failed and
        closed ones count: the desk took its shot, whatever became of it."""
        with session() as db:
            rows = db.exec(
                select(Structure).where(Structure.underlying == underlying, Structure.opened_at >= since)
            ).all()
        return any(not is_directional(st) for st in rows)

    async def decide(self, underlying: str, brokers: list[str]) -> dict[str, Any]:
        s: Settings = self.settings()
        dec = Decision(ts=time.time(), underlying=underlying, action="skip")
        try:
            await self._decide(dec, underlying, brokers, s)
        except AppError as exc:
            dec.action, dec.reason = "skip", str(exc)
        except Exception as exc:  # a bug must not stop the scheduler; it is recorded and shown
            log.exception("auto_decision_failed underlying=%s", underlying)
            dec.action, dec.reason = "error", f"{type(exc).__name__}: {exc}"
        with session() as db:
            db.add(dec)
            db.commit()
            db.refresh(dec)
        level = "error" if dec.action == "error" else "info"
        self.hub.publish(
            "AutoDecision", f"{underlying}: {dec.action}{' ' + dec.strategy if dec.strategy else ''} — {dec.reason}", level,
            decision_id=dec.id, underlying=underlying, action=dec.action, strategy=dec.strategy,
        )
        return dec.model_dump()

    async def _decide(self, dec: Decision, underlying: str, brokers: list[str], s: Settings) -> None:
        open_to = {b: why for b in brokers if (why := self._room(b, underlying, s))}
        if len(open_to) == len(brokers):
            dec.reason = "no room: " + "; ".join(f"{b} {why}" for b, why in open_to.items())
            return
        dec.spot = self.desk.spot(underlying)
        expiry = chain.pick_expiry(self.desk.feed.quotes(underlying), s.expiry_min_hours, s.expiry_max_hours)
        if expiry is None:
            dec.reason = f"no {underlying} expiry between {s.expiry_min_hours:g}h and {s.expiry_max_hours:g}h away"
            return
        dec.expiry = expiry
        regime = self.desk.regime(underlying, expiry)
        regime["hours_to_expiry"] = round((expiry - time.time()) / 3600, 2)
        dec.regime = {k: regime[k] for k in ("label", "direction", "direction_parts", "metrics", "scores", "notes", "hours_to_expiry")}

        bar, pacing = self._bar(underlying, s)
        dec.regime["min_score"] = bar  # what the bar actually was, so a past decision reads back straight
        if pacing:
            dec.regime["pacing"] = pacing

        considered, passing, plans = [], [], {}
        for key in s.auto_strategies:
            score = regime["scores"][key]
            entry: dict[str, Any] = {"strategy": key, "label": CATALOG[key].label, "score": score["score"], "reasons": score["reasons"]}
            considered.append(entry)
            if score["score"] < bar:
                entry["status"] = f"score {score['score']} under {bar}"
                continue
            try:
                plan = self.desk.plan(underlying, key, expiry)
            except Refused as exc:
                entry["status"] = str(exc)
                continue
            assessment = await self.desk.assess(plan, 1)
            entry.update(_compact(assessment))
            failed = [c["name"] for c in assessment["checks"] if c["required"] and not c["ok"]]
            if failed:
                entry["status"] = "failed " + ", ".join(failed)
                continue
            entry["status"] = "candidate"
            plans[key] = plan
            passing.append(assessment | {"score": score["score"], "reasons": score["reasons"]})
        passing.sort(key=lambda c: (c["score"], _edge(c)), reverse=True)
        top = passing[:MAX_CANDIDATES]
        dec.candidates = considered
        if not top:
            dec.reason = "no strategy passed: " + "; ".join(f"{c['label']} {c.get('status', '')}" for c in considered) if considered else "no strategy enabled"
            if pacing:
                dec.reason += f" ({pacing})"
            return

        choice = top[0]
        why = f"rules chose {choice['label']} (score {choice['score']}{f', bar eased to {bar}' if pacing else ''})"
        if s.ai_enabled and s.ai_review_trades:
            choice, why = await self._review(dec, underlying, regime, top, choice, why, s)
            if choice is None:
                return
        dec.strategy = choice["strategy"]
        await self._open(dec, plans[choice["strategy"]], choice, brokers, open_to, why, s)

    async def _review(
        self, dec: Decision, underlying: str, regime: dict[str, Any], top: list[dict[str, Any]],
        choice: dict[str, Any], why: str, s: Settings,
    ) -> tuple[dict[str, Any] | None, str]:
        """The model's verdict on the candidates. Returns (choice or None to skip, the reason)."""
        if not_ready := self.analyst.ready():
            dec.ai = {"used": False, "note": not_ready}
            if s.ai_required:
                dec.reason = f"AI review is required but {not_ready}"
                return None, dec.reason
            return choice, why + f" (AI not asked: {not_ready})"
        try:
            review = await self.analyst.review(underlying, regime, top)
        except LLMError as exc:
            dec.ai = {"used": False, "error": str(exc)[:600]}
            if s.ai_required:
                dec.reason = "AI review is required and no model answered"
                return None, dec.reason
            return choice, why + " (no model answered: the rules decided)"
        dec.ai = {"used": True} | review
        if review["decision"] == "skip":
            dec.reason = "AI reviewer skipped" + (f": {review['reasons'][0]}" if review["reasons"] else "")
            return None, dec.reason
        picked = next(c for c in top if c["strategy"] == review["candidate"])
        return picked, f"AI ({review['model']}, {review['confidence']} confidence) chose {picked['label']}"

    async def _open(
        self, dec: Decision, plan: Plan, choice: dict[str, Any], brokers: list[str], no_room: dict[str, str], why: str, s: Settings,
    ) -> None:
        with session() as db:  # the id makes the request: a re-run of this decision opens nothing twice
            db.add(dec)
            db.commit()
            db.refresh(dec)
        orders: dict[str, Any] = {}
        for broker in brokers:
            if broker in no_room:
                orders[broker] = {"refused": no_room[broker]}
                continue
            try:
                account = await self.trading.brokers[broker].account()
                qty = lots_for_risk(account.equity, choice, s)
                if qty < 1:
                    raise Refused(f"{s.risk_per_trade_pct:g}% of equity {account.equity:,.2f} does not cover one lot's worst case {unit_risk(choice, s):,.2f}")
                view = await self.trading.open(broker, plan, qty, "auto", f"auto-{dec.id}", dec.id)
                orders[broker] = {"structure_id": view["id"], "status": view["status"], "qty": qty, "error": view["error"]}
            except (Refused, BrokerError) as exc:
                orders[broker] = {"refused": str(exc)}
        dec.orders = orders
        opened = [b for b, o in orders.items() if o.get("status") == "open"]
        dec.action = "open" if opened else "skip"
        refusals = "; ".join(f"{b}: {o.get('refused') or o.get('error') or o.get('status')}" for b, o in orders.items() if b not in opened)
        dec.reason = why + (f" — opened on {', '.join(opened)}" if opened else "") + (f" — {refusals}" if refusals else "")

    @staticmethod
    def decisions(underlying: str = "", action: str = "", limit: int = 50) -> list[dict[str, Any]]:
        with session() as db:
            q = select(Decision).where(Decision.strategy != ZERO_HERO)  # zero-hero has its own page
            if underlying:
                q = q.where(Decision.underlying == underlying)
            if action:
                q = q.where(Decision.action == action)
            return [d.model_dump() for d in db.exec(q.order_by(col(Decision.ts).desc()).limit(min(max(limit, 1), 500))).all()]
