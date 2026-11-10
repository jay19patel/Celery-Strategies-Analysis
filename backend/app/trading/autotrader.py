"""The options auto-trader: one decision per underlying every `INTERVAL_MINUTES`.

  1. room     a broker already holding an automatic position on the underlying is skipped
  2. each     every option strategy switched on for the underlying (`app/strategies/`) picks its
              expiry, scores the regime there, and builds its structure if the score clears
              `MIN_SCORE`; a credit is not sold through a scheduled event
  3. filter   only structures passing every check the strategy binds (liquidity, POP, edge at
              realised vol, ...); the best by score, then edge, is the one — the rules decide, no
              model reviews it
  4. size     lots whose worst case is `risk_per_trade_pct` of that broker's equity, scaled by the
              strategy's measured edge and the broker's drawdown (`TradingService.risk_scale`)
  5. open     through `TradingService.open` — the same gate a dashboard click goes through, plus the
              portfolio rules (`trading.risk`)

A cycle that reached a broker is a `Decision` row: what it saw, every strategy it considered and why
each was dropped, and what each broker did. The latest cycle per underlying, skips included, is kept
live in `latest`, so "why did it not trade?" always has an answer without a row per skip.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from sqlmodel import col, select

from app.core.db import session
from app.core.events import EventHub
from app.core.models import Decision, Structure
from app.errors import AppError, BrokerError, Refused
from app.options.assess import unit_risk
from app.options.strategies import Plan
from app.settings import Settings
from app.strategies import registry
from app.trading.desk import Desk
from app.trading.risk import AUTO_SOURCES, day_start, trading_day
from app.trading.service import HELD, TradingService

log = logging.getLogger(__name__)
INTERVAL_MINUTES = 15
MIN_SCORE = 60  # the regime fit a strategy needs before it is built
MAX_CANDIDATES = 3
REGIME_KEYS = ("label", "direction", "direction_parts", "metrics", "notes", "gamma")


def lots_for_risk(equity: float, assessment: dict[str, Any], s: Settings) -> int:
    """Lots whose worst case (`unit_risk` of a one-lot assessment) is `risk_per_trade_pct` of equity."""
    risk = unit_risk(assessment)
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
    def __init__(self, desk: Desk, trading: TradingService, settings: Any, hub: EventHub) -> None:
        self.desk = desk
        self.trading = trading
        self.settings: Any = settings
        self.hub = hub
        # The latest decision per underlying, skips included: shown live and never stored, so "why is
        # it not trading?" has an answer on the page without a row per cycle in the database.
        self.latest: dict[str, dict[str, Any]] = {}

    def brokers(self) -> list[str]:
        s: Settings = self.settings()
        return [b for b in s.active_brokers if s.auto_on(b)]

    async def cycle(self) -> str:
        brokers = self.brokers()
        if not brokers:
            return "auto-trading is off on every active broker"
        if not registry.enabled(self.settings(), "options"):
            return "no option strategy is switched on"
        out = []
        for underlying in self.settings().underlyings:
            d = await self.decide(underlying, brokers)
            out.append(f"{underlying}: {d['action']}{' ' + d['strategy'] if d['strategy'] else ''}")
        return "; ".join(out)

    @staticmethod
    def _room(broker: str, underlying: str) -> str:
        """Why `broker` cannot take another automatic entry on `underlying`, or "" — checked before
        anything is built, so a full broker is a skip that is not written down. The heat cap needs
        the size, so it is the gate's (`trading.risk.portfolio_refusal`)."""
        with session() as db:
            held = db.exec(select(Structure).where(
                Structure.broker == broker, Structure.underlying == underlying, col(Structure.status).in_(HELD),
                col(Structure.source).in_(AUTO_SOURCES),
            )).first()
        return f"already holds an automatic {underlying} position ({held.strategy})" if held else ""

    @staticmethod
    def _stopped_today(underlying: str, since: float) -> set[str]:
        """The strategies an auto structure on this underlying was stopped out of since the day began.
        Re-entering at once buys the same idea into the same market: on 7 Nov the ETH long straddle
        was stopped and re-bought nine times in a morning, each one a fresh loss."""
        with session() as db:
            rows = db.exec(select(Structure.strategy, Structure.close_reason).where(
                Structure.underlying == underlying, Structure.source == "auto", col(Structure.closed_at) >= since,
            )).all()
        return {strategy for strategy, reason in rows if reason.startswith("stop loss")}

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
        # A skip that never reached a broker (no room, no expiry, no strategy passed) is not written
        # down: `_open` saves its row first, so every attempt on a broker still is.
        self.latest[underlying] = dec.model_dump()
        if dec.action == "skip" and dec.id is None:
            return self.latest[underlying]
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
        no_room = {b: why for b in brokers if (why := self._room(b, underlying))}
        if len(no_room) == len(brokers):
            dec.reason = "no room: " + "; ".join(f"{b} {why}" for b, why in no_room.items())
            return
        strategies = {n: p for n, p in registry.enabled(s, "options").items() if s.strategy_on(n, underlying)}
        if not strategies:
            dec.reason = f"no option strategy is switched on for {underlying}"
            return
        dec.spot = self.desk.spot(underlying)
        quotes = self.desk.feed.quotes(underlying)
        if not quotes:
            dec.reason = f"no fresh {underlying} options"
            return
        ctx = self.desk.option_context(underlying, await self.desk.fee_rate(quotes[0].symbol))
        stopped = self._stopped_today(underlying, day_start(s.day_timezone, trading_day(s.day_timezone)))
        considered: list[dict[str, Any]] = []
        passing: list[dict[str, Any]] = []
        plans: dict[str, Plan] = {}
        for name, plugin in strategies.items():
            entry: dict[str, Any] = {"strategy": name, "label": plugin.label}
            considered.append(entry)
            expiry = plugin.expiry(ctx)
            if expiry is None:
                entry["status"] = plugin.expiry_note()
                continue
            score, reasons = plugin.score(ctx.regime(expiry))
            entry |= {"score": score, "reasons": reasons, "expiry": expiry}
            if name in stopped:
                entry["status"] = "stopped out on this underlying earlier today: not re-entered until tomorrow"
                continue
            if plugin.premium == "credit" and (event := s.event_between(ctx.now, expiry)):
                entry["status"] = f"scheduled event at {event} UTC before expiry"
                continue
            if score < MIN_SCORE:
                entry["status"] = f"score {score} under {MIN_SCORE}"
                continue
            try:
                plan = plugin.build(ctx, expiry)
            except Refused as exc:
                entry["status"] = str(exc)
                continue
            if plan is None:
                entry["status"] = f"the chain for that expiry cannot make a {plugin.label}"
                continue
            assessment = await self.desk.assess(plan, 1)
            entry.update(_compact(assessment))
            failed = [c["name"] for c in assessment["checks"] if c["required"] and not c["ok"]]
            if failed:
                entry["status"] = "failed " + ", ".join(failed)
                continue
            entry["status"] = "candidate"
            plans[name] = plan
            passing.append(assessment | {"score": score, "reasons": reasons})
        passing.sort(key=lambda c: (c["score"], _edge(c)), reverse=True)
        dec.candidates = considered
        top = passing[:MAX_CANDIDATES]
        chosen = top[0] if top else None
        expiry = plans[chosen["strategy"]].expiry if chosen else next((c["expiry"] for c in considered if c.get("expiry")), None)
        if expiry is not None:
            dec.expiry = expiry
            dec.regime = {k: ctx.regime(expiry).get(k) for k in REGIME_KEYS} | {
                "hours_to_expiry": round((expiry - ctx.now) / 3600, 2),
                "scores": {c["strategy"]: {"score": c["score"], "reasons": c["reasons"]} for c in considered if "score" in c},
                "min_score": MIN_SCORE,
            }
        if chosen is None:
            dec.reason = "no strategy passed: " + "; ".join(f"{c['label']} {c.get('status', '')}" for c in considered)
            return
        dec.strategy = chosen["strategy"]
        why = f"rules chose {chosen['label']} (score {chosen['score']})"
        await self._open(dec, plans[chosen["strategy"]], chosen, brokers, no_room, why, s)

    async def _open(
        self, dec: Decision, plan: Plan, choice: dict[str, Any], brokers: list[str], no_room: dict[str, str], why: str, s: Settings,
    ) -> None:
        with session() as db:  # the id makes the request: a re-run of this decision opens nothing twice
            db.add(dec)
            db.commit()
            db.refresh(dec)
        allowed = s.strategy_brokers(plan.strategy)
        orders: dict[str, Any] = {}
        for broker in brokers:
            if broker in no_room:
                orders[broker] = {"refused": no_room[broker]}
                continue
            if broker not in allowed:
                orders[broker] = {"refused": f"{choice['label']} is not switched to trade on {broker}"}
                continue
            try:
                account = await self.trading.brokers[broker].account()
                x, sized = self.trading.risk_scale(broker, plan.strategy, account.equity)
                if x <= 0:
                    raise Refused(sized)
                qty = lots_for_risk(account.equity * x, choice, s)
                if qty < 1:
                    raise Refused(f"{x * s.risk_per_trade_pct:g}% of equity {account.equity:,.2f} does not cover one lot's worst case {unit_risk(choice):,.2f}")
                view = await self.trading.open(broker, plan, qty, "auto", f"auto-{dec.id}", dec.id, f"{why}: {', '.join(choice['reasons'][:4])}")
                orders[broker] = {"structure_id": view["id"], "status": view["status"], "qty": qty, "error": view["error"], "sized": sized}
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
            q = select(Decision)
            if underlying:
                q = q.where(Decision.underlying == underlying)
            if action:
                q = q.where(Decision.action == action)
            return [d.model_dump() for d in db.exec(q.order_by(col(Decision.ts).desc()).limit(min(max(limit, 1), 500))).all()]
