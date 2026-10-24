"""The only code that opens and closes positions — auto-trader and dashboard alike.

A structure is opened leg by leg, **bought legs first**, so a sold option is never on the book
without its protection; it is closed **sold legs first** (bought back) for the same reason. If a leg
fails, the legs already filled are unwound and the structure is recorded as failed, never left half
built without saying so. A timeout is not a rejection: the order is looked up by its
client_order_id, never resent; while its outcome is unknown the structure is `error` and the broker
takes no new entry until it is resolved.

Every refusal is a `Refused` naming the rule. All state changes run under one lock, so the
auto-trader, the guard and a dashboard click can never interleave on the same structures.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime
from typing import Any

from sqlmodel import col, select

from app.brokers.base import Broker, Fill, LegOrder
from app.core.db import session
from app.core.events import EventHub
from app.core.models import Leg, OrderRecord, Structure
from app.errors import BrokerError, BrokerTimeout, NotFound, Refused
from app.market.feed import MarketFeed
from app.options.assess import leg_fee
from app.options.pricing import LegSpec, analyze, intrinsic, margin_required, net_premium
from app.options.strategies import CATALOG, Plan
from app.settings import Settings
from app.trading.desk import Desk
from app.trading.risk import halt_reason, trading_day

ENTRY_SLACK = 0.05  # an entry fills at most 5% worse than the quote it was priced at, or not at all
CLOSE_SLACK = 0.15  # a close accepts more: a position left open is the worse outcome
UNWIND_SLACK = 0.25
RESOLVE_DELAYS = (1.0, 3.0, 10.0)
HELD = ("opening", "open", "closing", "error")
MIN_MANUAL_HOURS = 0.5


def structure_id(broker: str, request_id: str) -> str:
    """One structure per broker and request: a retried click or a re-run decision opens nothing twice."""
    return "s" + hashlib.sha256(f"{broker}:{request_id}".encode()).hexdigest()[:11]


def leg_sign(side: str) -> int:
    return 1 if side == "buy" else -1


def exit_thresholds(st: Structure) -> tuple[float | None, float | None]:
    """The P&L at which the guard takes profit and stops out. A credit takes profit at a share of its
    maximum profit and stops at a multiple of the credit; a debit at shares of what it cost."""
    if st.entry_cash > 0:
        base = st.max_profit if st.max_profit is not None else st.entry_cash
        return base * st.take_profit_pct / 100, -st.entry_cash * st.stop_loss_pct / 100
    if st.entry_cash < 0:
        debit = -st.entry_cash
        return debit * st.take_profit_pct / 100, -debit * st.stop_loss_pct / 100
    return None, None


class TradingService:
    def __init__(self, feed: MarketFeed, desk: Desk, brokers: dict[str, Broker], settings: Any, hub: EventHub) -> None:
        self.feed = feed
        self.desk = desk
        self.brokers = brokers
        self.settings: Any = settings  # () -> Settings
        self.hub = hub
        self.lock = asyncio.Lock()

    # -- reads ---------------------------------------------------------------------------------

    @staticmethod
    def _load(sid: str) -> tuple[Structure, list[Leg]]:
        with session() as s:
            st = s.get(Structure, sid)
            if st is None:
                raise NotFound(f"no structure {sid}")
            legs = list(s.exec(select(Leg).where(Leg.structure_id == sid).order_by(Leg.idx, Leg.id)).all())
        return st, legs

    def structures(self, status: str = "", broker: str = "", limit: int = 200) -> list[dict[str, Any]]:
        with session() as s:
            q = select(Structure)
            if status == "open":
                q = q.where(col(Structure.status).in_(HELD))
            elif status:
                q = q.where(Structure.status == status)
            if broker:
                q = q.where(Structure.broker == broker)
            rows = s.exec(q.order_by(col(Structure.opened_at).desc()).limit(min(max(limit, 1), 1000))).all()
            ids = [r.id for r in rows]
            legs = s.exec(select(Leg).where(col(Leg.structure_id).in_(ids)).order_by(Leg.idx, Leg.id)).all() if ids else []
        by_id: dict[str, list[Leg]] = {}
        for leg in legs:
            by_id.setdefault(leg.structure_id, []).append(leg)
        return [self.view(st, by_id.get(st.id, [])) for st in rows]

    def structure(self, sid: str) -> dict[str, Any]:
        st, legs = self._load(sid)
        with session() as s:
            orders = s.exec(select(OrderRecord).where(OrderRecord.structure_id == sid).order_by(OrderRecord.created_at)).all()
        return self.view(st, legs) | {"orders": [o.model_dump() for o in orders]}

    def orders(self, broker: str = "", limit: int = 300) -> list[dict[str, Any]]:
        with session() as s:
            q = select(OrderRecord)
            if broker:
                q = q.where(OrderRecord.broker == broker)
            return [o.model_dump() for o in s.exec(q.order_by(col(OrderRecord.created_at).desc()).limit(min(max(limit, 1), 2000))).all()]

    def valuation(self, st: Structure, legs: list[Leg]) -> dict[str, Any]:
        """What closing every held leg now would book — at the bid for a long, the ask for a short, net
        of every fee — and the same at the marks. The guard acts on the first; it never books a
        'profit' the order book would not pay."""
        slip = self.settings().paper_slippage_pct / 100
        book = mid = sum(-leg_sign(leg.side) * (leg.entry_price or 0) * leg.size * leg.contract_value - leg.entry_fee for leg in legs if leg.entry_price is not None)
        missing: list[str] = []
        spot = self.feed.spot(st.underlying) or 0.0
        for leg in legs:
            if leg.entry_price is None:
                continue
            units = leg.size * leg.contract_value
            if leg.exit_price is not None:
                cash = leg_sign(leg.side) * leg.exit_price * units - leg.exit_fee
                book += cash
                mid += cash
                continue
            q = self.feed.quote(leg.symbol)
            if q is None or not q.mark:
                missing.append(leg.symbol)
                continue
            price = (q.bid or q.mark * (1 - slip)) if leg.side == "buy" else (q.ask or q.mark * (1 + slip))
            fee = leg_fee(price, units, spot, self.desk.cached_fee(leg.symbol))
            book += leg_sign(leg.side) * price * units - fee
            mid += leg_sign(leg.side) * q.mark * units
        return {"pnl": None if missing else round(book, 6), "pnl_mid": None if missing else round(mid, 6), "missing": missing, "spot": spot or None}

    def view(self, st: Structure, legs: list[Leg]) -> dict[str, Any]:
        tp, sl = exit_thresholds(st)
        live = self.valuation(st, legs) if st.status in HELD else None
        out_legs = []
        for leg in legs:
            q = self.feed.quote(leg.symbol) if st.status in HELD else None
            out_legs.append(leg.model_dump() | {"mark": q.mark if q else None, "bid": q.bid if q else None, "ask": q.ask if q else None})
        spec = CATALOG.get(st.strategy)
        return st.model_dump() | {
            "label": spec.label if spec else "Custom",
            "kind": "credit" if st.entry_cash > 0 else "debit",
            "legs": out_legs,
            "live": live,
            "take_profit_usd": None if tp is None else round(tp, 6),
            "stop_loss_usd": None if sl is None else round(sl, 6),
            "hours_left": round(max(0.0, (st.expiry - time.time()) / 3600), 2),
            "expiry_label": datetime.fromtimestamp(st.expiry, UTC).strftime("%d %b %H:%M UTC"),
        }

    # -- gates ---------------------------------------------------------------------------------

    def _held(self, broker: str) -> list[Structure]:
        with session() as s:
            return list(s.exec(select(Structure).where(Structure.broker == broker, col(Structure.status).in_(HELD))).all())

    async def gate(self, broker: str, plan: Plan, qty: int, source: str) -> tuple[Broker, dict[str, Any]]:
        """Every rule an entry must pass, the same for a click and the auto-trader. Returns the broker
        and the assessment at `qty`; raises Refused naming the first rule that fails."""
        s: Settings = self.settings()
        if broker not in s.active_brokers or broker not in self.brokers:
            raise Refused(f"{broker} is not an active broker")
        b = self.brokers[broker]
        if reason := b.not_ready():
            raise Refused(f"{broker}: {reason}")
        if halt := halt_reason(broker, s.day_timezone):
            raise Refused(f"{broker} is halted for the day: {halt}")
        held = self._held(broker)
        if stuck := [st for st in held if st.status != "open"]:
            raise Refused(f"{broker} has an unresolved structure {stuck[0].id} ({stuck[0].status}): close or resolve it first")
        if len(held) >= s.max_open_structures:
            raise Refused(f"{broker} already holds {len(held)} structures (max {s.max_open_structures})")
        if sum(st.underlying == plan.underlying for st in held) >= s.max_per_underlying:
            raise Refused(f"{broker} already holds {s.max_per_underlying} {plan.underlying} structure(s) (max per underlying)")
        if not 1 <= qty <= s.max_lots:
            raise Refused(f"quantity must be 1 to {s.max_lots} lots")
        hours = (plan.expiry - time.time()) / 3600
        floor = s.expiry_min_hours if source == "auto" else MIN_MANUAL_HOURS
        if hours < floor:
            raise Refused(f"{hours:.1f}h to expiry is under the {floor:g}h minimum")
        if stale := [leg.symbol for leg in plan.legs if self.feed.quote(leg.symbol) is None]:
            raise Refused(f"no fresh quote for {', '.join(stale)}")
        assessment = await self.desk.assess(plan, qty)
        try:
            account = await b.account()
        except BrokerError as exc:
            raise Refused(f"{broker}: cannot read the account, so no trade ({exc})") from exc
        need = assessment["analysis"]["margin"] + assessment["analysis"]["entry_fees"]
        if need > account.available:
            raise Refused(f"{broker} needs {need:,.2f} of margin, {account.available:,.2f} is available")
        assessment["account"] = account.to_dict()
        return b, assessment

    # -- open ----------------------------------------------------------------------------------

    async def open(
        self, broker: str, plan: Plan, qty: int, source: str, request_id: str, decision_id: int | None = None,
    ) -> dict[str, Any]:
        sid = structure_id(broker, request_id)
        async with self.lock:
            with session() as s:
                if s.get(Structure, sid) is not None:
                    return self.structure(sid)
            b, assessment = await self.gate(broker, plan, qty, source)
            cfg: Settings = self.settings()
            credit = assessment["analysis"]["net_premium"] > 0
            st = Structure(
                id=sid, broker=broker, underlying=plan.underlying, strategy=plan.strategy, source=source, status="opening",
                expiry=plan.expiry, qty=qty, entry_spot=plan.spot, opened_at=time.time(), decision_id=decision_id,
                request_id=request_id, margin=assessment["analysis"]["margin"],
                take_profit_pct=cfg.credit_take_profit_pct if credit else cfg.debit_take_profit_pct,
                stop_loss_pct=cfg.credit_stop_loss_pct if credit else cfg.debit_stop_loss_pct,
            )
            legs = [
                Leg(structure_id=sid, idx=i, symbol=leg.symbol, kind=leg.kind, strike=leg.strike, side=leg.side,
                    size=leg.ratio * qty, contract_value=leg.contract_value)
                for i, leg in enumerate(plan.legs)
            ]
            self._save(st)  # before its legs: they reference it
            self._save(*legs)

            error, unknown = await self._enter(b, st, legs, plan)
            if error:
                await self._abort(b, st, legs, error, unknown)
            else:
                self._opened(st, legs)
            return self.view(st, legs)

    async def _enter(self, b: Broker, st: Structure, legs: list[Leg], plan: Plan) -> tuple[str, bool]:
        """Fill every leg, bought ones first. Returns (error, outcome unknown) — ("", False) when all filled."""
        for i in sorted(range(len(legs)), key=lambda i: legs[i].side != "buy"):
            leg = legs[i]
            q = self.feed.quote(leg.symbol)
            ref = ((q.ask if leg.side == "buy" else q.bid) or q.mark) if q else None
            ref = ref or plan.legs[i].price
            limit = ref * (1 + ENTRY_SLACK) if leg.side == "buy" else ref * (1 - ENTRY_SLACK)
            fill, error, unknown = await self._send(b, st, leg, "open", leg.side, leg.size, limit)
            if fill:
                leg.entry_price, leg.entry_fee = fill.price, fill.fee
                if fill.size < leg.size:
                    error = f"{leg.symbol} filled {fill.size} of {leg.size} contracts"
                    leg.size = fill.size  # what is actually held, so an unwind closes exactly that
                self._save(leg)
            if error:
                return error, unknown
        return "", False

    def _opened(self, st: Structure, legs: list[Leg]) -> None:
        specs = [LegSpec(leg.kind, leg.strike, leg.side, leg.size, leg.entry_price or 0.0, leg.contract_value) for leg in legs]
        a = analyze(specs, st.entry_spot or 0.0)
        st.status = "open"
        st.entry_cash = round(net_premium(specs), 6)
        st.fees = round(sum(leg.entry_fee for leg in legs), 6)
        st.max_profit, st.max_loss = a["max_profit"], a["max_loss"]
        st.margin = round(margin_required(specs, st.entry_spot or 0.0, a["max_loss"]), 6)
        self._save(st)
        what = "credit" if st.entry_cash > 0 else "debit"
        self.hub.publish(
            "StructureOpened",
            f"{self._title(st)} opened on {st.broker}: {what} {abs(st.entry_cash):,.2f}, worst case "
            + (f"{st.max_loss:,.2f}" if st.max_loss is not None else "unlimited"),
            structure_id=st.id, broker=st.broker, source=st.source, strategy=st.strategy, underlying=st.underlying,
            entry_cash=st.entry_cash, max_loss=st.max_loss, max_profit=st.max_profit, qty=st.qty,
        )

    async def _abort(self, b: Broker, st: Structure, legs: list[Leg], error: str, unknown: bool) -> None:
        if unknown:
            st.status, st.error = "error", f"{error} — the order's outcome is unknown; check the exchange, then close this structure"
            self._save(st)
            self.hub.publish("StructureError", f"{self._title(st)} on {st.broker}: {st.error}", "error", structure_id=st.id, broker=st.broker)
            return
        problems = await self._exit(b, st, legs, "unwind", UNWIND_SLACK)
        if problems:
            st.status = "error"
            st.error = f"entry failed ({error}) and the unwind did not finish: {'; '.join(problems)}"
            self._save(st)
            self.hub.publish("StructureError", f"{self._title(st)} on {st.broker}: {st.error}", "error", structure_id=st.id, broker=st.broker)
            return
        self._finish(st, legs, "failed", f"entry failed: {error}")
        self.hub.publish(
            "StructureFailed", f"{self._title(st)} on {st.broker} not opened: {error}", "warning",
            structure_id=st.id, broker=st.broker, source=st.source, pnl=st.pnl,
        )

    # -- close ---------------------------------------------------------------------------------

    async def close(self, sid: str, reason: str = "closed from the dashboard") -> dict[str, Any]:
        async with self.lock:
            return await self._close(sid, reason)

    async def _close(self, sid: str, reason: str) -> dict[str, Any]:
        st, legs = self._load(sid)
        if st.status not in ("open", "error"):
            raise Refused(f"structure {sid} is {st.status}, not open")
        b = self.brokers[st.broker]
        await self._resolve_unknown(b, st, legs)
        st.status = "closing"
        self._save(st)
        problems = await self._exit(b, st, legs, "close", CLOSE_SLACK)
        if problems:
            st.status, st.error = "error", "close did not finish: " + "; ".join(problems)
            self._save(st)
            self.hub.publish("StructureError", f"{self._title(st)} on {st.broker}: {st.error}", "error", structure_id=st.id, broker=st.broker)
        else:
            self._finish(st, legs, "closed", reason)
            self._closed_event(st)
        return self.view(st, legs)

    async def settle_expired(self, sid: str) -> dict[str, Any]:
        """Legs still held past expiry, settled at intrinsic value on the index."""
        async with self.lock:
            st, legs = self._load(sid)
            if st.status not in ("open", "error"):
                raise Refused(f"structure {sid} is {st.status}, not open")
            spot = self.feed.spot(st.underlying)
            if spot is None:
                raise Refused(f"no {st.underlying} index price to settle {sid} at")
            b = self.brokers[st.broker]
            for leg in legs:
                if leg.entry_price is None or leg.exit_price is not None:
                    continue
                order = LegOrder(f"{st.id}{leg.idx}x", leg.symbol, "sell" if leg.side == "buy" else "buy", leg.size, 0.0, leg.contract_value)
                price = intrinsic(leg.kind, leg.strike, spot)
                self._record(order, st, "settle")
                fill = await b.settle(order, price)
                self._mark_order(order.client_order_id, fill=fill)
                leg.exit_price, leg.exit_fee = fill.price, fill.fee
                self._save(leg)
            self._finish(st, legs, "closed", f"settled at expiry on index {spot:,.2f}")
            self._closed_event(st)
            return self.view(st, legs)

    async def close_all(self, reason: str, broker: str = "") -> dict[str, Any]:
        """The kill switch: every open structure, on one broker or all of them."""
        closed, errors = [], []
        for name in [broker] if broker else list(self.brokers):
            for st in self._held(name):
                if st.status not in ("open", "error"):
                    continue
                try:
                    view = await self.close(st.id, reason)
                except Exception as exc:
                    errors.append(f"{st.id}: {exc}")
                    continue
                if view["status"] == "closed":
                    closed.append(st.id)
                else:
                    errors.append(f"{st.id}: {view['error']}")
        return {"closed": closed, "errors": errors}

    def update_exits(self, sid: str, take_profit_pct: float, stop_loss_pct: float) -> dict[str, Any]:
        if not (5 <= take_profit_pct <= 1000 and 5 <= stop_loss_pct <= 500):
            raise Refused("take profit must be 5-1000% and stop loss 5-500%")
        st, legs = self._load(sid)
        if st.status not in ("open", "error"):
            raise Refused(f"structure {sid} is {st.status}")
        st.take_profit_pct, st.stop_loss_pct = take_profit_pct, stop_loss_pct
        self._save(st)
        return self.view(st, legs)

    async def _exit(self, b: Broker, st: Structure, legs: list[Leg], purpose: str, slack: float) -> list[str]:
        """Exit every held leg, sold ones first. A short that cannot be bought back stops the rest:
        selling its protection after that would leave it naked. Returns what did not exit."""
        problems: list[str] = []
        held = [leg for leg in legs if leg.entry_price is not None and leg.exit_price is None]
        for leg in sorted(held, key=lambda leg: leg.side != "sell"):
            side = "buy" if leg.side == "sell" else "sell"
            q = self.feed.quote(leg.symbol)
            ref = ((q.ask if side == "buy" else q.bid) or q.mark) if q else None
            if not ref:
                problems.append(f"{leg.symbol}: no fresh quote to {side} at")
                if leg.side == "sell":
                    break
                continue
            limit = ref * (1 + slack) if side == "buy" else ref * (1 - slack)
            fill, error, _unknown = await self._send(b, st, leg, purpose, side, leg.size, limit, reduce_only=True)
            if fill and fill.size == leg.size:
                leg.exit_price, leg.exit_fee = fill.price, fill.fee
                self._save(leg)
                continue
            if fill:  # partly exited: the exited part becomes its own leg, the rest stays held
                done = Leg(structure_id=st.id, idx=leg.idx, symbol=leg.symbol, kind=leg.kind, strike=leg.strike, side=leg.side,
                           size=fill.size, contract_value=leg.contract_value, entry_price=leg.entry_price,
                           entry_fee=leg.entry_fee * fill.size / leg.size, exit_price=fill.price, exit_fee=fill.fee)
                leg.entry_fee -= done.entry_fee
                leg.size -= fill.size
                self._save(leg, done)
                legs.append(done)
                error = error or f"{leg.symbol}: {leg.size} contracts did not exit"
            problems.append(error)
            if leg.side == "sell":
                break
        return problems

    async def _resolve_unknown(self, b: Broker, st: Structure, legs: list[Leg]) -> None:
        """Before closing a structure in error, settle what its unknown orders actually did."""
        with session() as s:
            unknown = s.exec(select(OrderRecord).where(OrderRecord.structure_id == st.id, OrderRecord.status == "unknown")).all()
        for rec in unknown:
            try:
                fill = await b.lookup(rec.client_order_id)
            except BrokerTimeout as exc:
                raise Refused(f"{rec.client_order_id} still cannot be looked up: {exc}") from exc
            self._mark_order(rec.client_order_id, fill=fill, error="" if fill else "not filled (looked up)")
            leg = next((leg for leg in legs if leg.symbol == rec.symbol), None)
            if fill and leg is not None:
                if rec.purpose == "open" and leg.entry_price is None:
                    leg.entry_price, leg.entry_fee, leg.size = fill.price, fill.fee, fill.size
                elif rec.purpose != "open" and leg.exit_price is None:
                    leg.exit_price, leg.exit_fee = fill.price, fill.fee
                self._save(leg)

    # -- orders --------------------------------------------------------------------------------

    async def _send(
        self, b: Broker, st: Structure, leg: Leg, purpose: str, side: str, size: int, limit: float, reduce_only: bool = False,
    ) -> tuple[Fill | None, str, bool]:
        """One leg order. Returns (fill, error, outcome unknown)."""
        with session() as s:
            attempt = len(s.exec(select(OrderRecord).where(OrderRecord.structure_id == st.id, OrderRecord.symbol == leg.symbol, OrderRecord.purpose == purpose)).all())
        order = LegOrder(f"{st.id}{leg.idx}{purpose[0]}{attempt}", leg.symbol, side, size, limit, leg.contract_value, reduce_only)
        self._record(order, st, purpose)
        try:
            fill = await b.execute(order)
        except BrokerTimeout as exc:
            fill, known = await self._look_up(b, order.client_order_id)
            if not known:
                self._mark_order(order.client_order_id, status="unknown", error=str(exc))
                return None, f"{leg.symbol} {side}: {exc}", True
            if fill is None:
                self._mark_order(order.client_order_id, error=f"timed out, then found unfilled: {exc}")
                return None, f"{leg.symbol} {side}: not filled", False
        except BrokerError as exc:
            self._mark_order(order.client_order_id, error=str(exc))
            return None, f"{leg.symbol} {side}: {exc}", False
        self._mark_order(order.client_order_id, fill=fill)
        return fill, "", False

    @staticmethod
    async def _look_up(b: Broker, cid: str) -> tuple[Fill | None, bool]:
        for delay in RESOLVE_DELAYS:
            await asyncio.sleep(delay)
            try:
                return await b.lookup(cid), True
            except BrokerTimeout:
                continue
        return None, False

    @staticmethod
    def _record(order: LegOrder, st: Structure, purpose: str) -> None:
        now = time.time()
        with session() as s:
            s.add(OrderRecord(
                client_order_id=order.client_order_id, structure_id=st.id, broker=st.broker, purpose=purpose, symbol=order.symbol,
                side=order.side, size=order.size, limit_price=round(order.limit_price, 6), status="pending", created_at=now, updated_at=now,
            ))
            s.commit()

    @staticmethod
    def _mark_order(cid: str, fill: Fill | None = None, status: str = "", error: str = "") -> None:
        with session() as s:
            rec = s.get(OrderRecord, cid)
            if rec is None:
                return
            if fill is not None:
                rec.status, rec.fill_price, rec.filled_size, rec.fee, rec.exchange_id = "filled", fill.price, fill.size, fill.fee, fill.exchange_id
            else:
                rec.status = status or "failed"
            rec.error, rec.updated_at = error[:500], time.time()
            s.add(rec)
            s.commit()

    # -- bookkeeping ---------------------------------------------------------------------------

    def _finish(self, st: Structure, legs: list[Leg], status: str, reason: str) -> None:
        entered = [leg for leg in legs if leg.entry_price is not None]
        st.entry_cash = round(sum(-leg_sign(leg.side) * (leg.entry_price or 0) * leg.size * leg.contract_value for leg in entered), 6)
        st.exit_cash = round(sum(leg_sign(leg.side) * (leg.exit_price or 0) * leg.size * leg.contract_value for leg in entered), 6)
        st.fees = round(sum(leg.entry_fee + leg.exit_fee for leg in entered), 6)
        st.pnl = round(st.entry_cash + st.exit_cash - st.fees, 6)
        st.status, st.close_reason, st.error = status, reason, ""
        st.closed_at = time.time()
        st.exit_spot = self.feed.spot(st.underlying)
        st.mark_pnl = None
        self._save(st)

    def _closed_event(self, st: Structure) -> None:
        self.hub.publish(
            "StructureClosed", f"{self._title(st)} closed on {st.broker}: {st.pnl:+,.2f} — {st.close_reason}",
            "info", structure_id=st.id, broker=st.broker, source=st.source, strategy=st.strategy, pnl=st.pnl, reason=st.close_reason,
        )

    @staticmethod
    def _title(st: Structure) -> str:
        spec = CATALOG.get(st.strategy)
        day = datetime.fromtimestamp(st.expiry, UTC).strftime("%d %b")
        return f"{spec.label if spec else 'Custom'} {st.underlying} {day} ×{st.qty}"

    @staticmethod
    def _save(*objs: Any) -> None:
        with session() as s:
            for obj in objs:
                s.add(obj)
            s.commit()
            for obj in objs:
                s.refresh(obj)

    # -- results -------------------------------------------------------------------------------

    def stats(self, broker: str = "") -> dict[str, Any]:
        tz = self.settings().day_timezone
        with session() as s:
            q = select(Structure).where(Structure.status == "closed")
            if broker:
                q = q.where(Structure.broker == broker)
            rows = list(s.exec(q.order_by(Structure.closed_at)).all())

        def summary(trades: list[Structure]) -> dict[str, Any]:
            pnls = [t.pnl or 0.0 for t in trades]
            wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p <= 0]
            n = len(pnls)
            return {
                "trades": n, "wins": len(wins), "losses": len(losses),
                "win_rate": round(100 * len(wins) / n, 1) if n else None,
                "pnl": round(sum(pnls), 4), "fees": round(sum(t.fees for t in trades), 4),
                "avg_win": round(sum(wins) / len(wins), 4) if wins else None,
                "avg_loss": round(sum(losses) / len(losses), 4) if losses else None,
                "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
                "expectancy": round(sum(pnls) / n, 4) if n else None,
                "best": round(max(pnls), 4) if pnls else None, "worst": round(min(pnls), 4) if pnls else None,
                "avg_hold_hours": round(sum((t.closed_at or 0) - t.opened_at for t in trades) / n / 3600, 2) if n else None,
            }

        curve, total, peak, drawdown = [], 0.0, 0.0, 0.0
        daily: dict[str, float] = {}
        for t in rows:
            total += t.pnl or 0.0
            peak = max(peak, total)
            drawdown = max(drawdown, peak - total)
            curve.append({"ts": t.closed_at, "pnl": round(total, 4), "trade": round(t.pnl or 0.0, 4), "strategy": t.strategy, "id": t.id})
            day = trading_day(tz, t.closed_at)
            daily[day] = round(daily.get(day, 0.0) + (t.pnl or 0.0), 4)

        def grouped(key: str) -> list[dict[str, Any]]:
            groups: dict[str, list[Structure]] = {}
            for t in rows:
                groups.setdefault(getattr(t, key), []).append(t)
            return sorted(({key: k} | summary(v) for k, v in groups.items()), key=lambda g: -g["pnl"])

        return {
            "overall": summary(rows) | {"max_drawdown": round(drawdown, 4)},
            "by_strategy": grouped("strategy"),
            "by_source": grouped("source"),
            "by_broker": grouped("broker"),
            "curve": curve,
            "daily": daily,
        }
