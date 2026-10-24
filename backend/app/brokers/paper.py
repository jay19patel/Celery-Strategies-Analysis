"""The simulated broker. It fills where the real book would: a purchase at the ask, a sale at the bid
(the mark plus slippage only when that side is empty), refuses a fill beyond the order's limit, and
charges the fee the exchange's product spec states. Its cash lives in SQLite, so it survives
restarts; equity is that cash plus every open leg marked at its live quote.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from sqlmodel import col, delete, select

from app.brokers.base import Account, Fill, LegOrder
from app.core.db import session
from app.core.models import Leg, OrderRecord, PaperAccount, Structure
from app.errors import BrokerError, Refused
from app.market.feed import MarketFeed
from app.options.assess import FeeRate, leg_fee
from app.settings import Settings

HELD = ("opening", "open", "closing", "error")


class PaperBroker:
    name = "paper"

    def __init__(self, feed: MarketFeed, settings: Callable[[], Settings], fee_rate: Callable[[str], Awaitable[FeeRate]]) -> None:
        self.feed = feed
        self.settings = settings
        self.fee_rate = fee_rate

    def not_ready(self) -> str:
        return ""

    def _row(self) -> PaperAccount:
        with session() as s:
            row = s.get(PaperAccount, 1)
            if row is None:
                balance = self.settings().paper_starting_balance
                row = PaperAccount(id=1, cash=balance, starting_balance=balance, created_at=time.time())
                s.add(row)
                s.commit()
            return row

    async def account(self) -> Account:
        acct = self._row()
        with session() as s:
            structures = s.exec(select(Structure).where(Structure.broker == self.name, col(Structure.status).in_(HELD))).all()
            ids = [st.id for st in structures]
            legs = s.exec(select(Leg).where(col(Leg.structure_id).in_(ids))).all() if ids else []
        value, stale = 0.0, []
        for leg in legs:
            if leg.entry_price is None or leg.exit_price is not None:
                continue
            q = self.feed.quote(leg.symbol)
            mark = q.mark if q and q.mark is not None else leg.entry_price
            if q is None:
                stale.append(leg.symbol)
            value += (1 if leg.side == "buy" else -1) * mark * leg.size * leg.contract_value
        margin = sum(st.margin for st in structures)
        equity = acct.cash + value
        return Account(
            broker=self.name, currency="USD", equity=round(equity, 6), available=round(equity - margin, 6),
            margin_used=round(margin, 6), cash=round(acct.cash, 6),
            extra={"starting_balance": acct.starting_balance, "fees_paid": round(acct.fees_paid, 6), "created_at": acct.created_at, "unpriced_legs": stale},
        )

    async def execute(self, order: LegOrder) -> Fill:
        q = self.feed.quote(order.symbol)
        if q is None or not q.mark:
            raise BrokerError(f"paper: no fresh quote for {order.symbol}")
        slip = self.settings().paper_slippage_pct / 100
        if order.side == "buy":
            price = q.ask or q.mark * (1 + slip)
            beyond = price > order.limit_price
        else:
            price = q.bid or q.mark * (1 - slip)
            beyond = price < order.limit_price
        if beyond:
            raise BrokerError(f"paper: {order.symbol} would {order.side} at {price:,.2f}, beyond the limit {order.limit_price:,.2f}")
        units = order.size * order.contract_value
        fee = leg_fee(price, units, q.spot or self.feed.spot(q.underlying) or 0.0, await self.fee_rate(order.symbol))
        cash = (price if order.side == "sell" else -price) * units - fee
        return self._book(order, price, fee, cash)

    async def settle(self, order: LegOrder, price: float) -> Fill:
        cash = (price if order.side == "sell" else -price) * order.size * order.contract_value
        return self._book(order, price, 0.0, cash)

    async def lookup(self, client_order_id: str) -> Fill | None:
        with session() as s:
            rec = s.get(OrderRecord, client_order_id)
        if rec is None or rec.status != "filled" or rec.fill_price is None:
            return None
        return Fill(client_order_id, rec.filled_size, rec.fill_price, rec.fee, rec.exchange_id)

    def _book(self, order: LegOrder, price: float, fee: float, cash: float) -> Fill:
        """The cash and the order's fill in one transaction: a restart can never see one without the other."""
        self._row()
        exchange_id = f"paper-{order.client_order_id[-12:]}"
        with session() as s:
            acct = s.get(PaperAccount, 1)
            acct.cash += cash
            acct.fees_paid += fee
            s.add(acct)
            rec = s.get(OrderRecord, order.client_order_id)
            if rec is not None:
                rec.status, rec.fill_price, rec.filled_size, rec.fee = "filled", price, order.size, fee
                rec.exchange_id, rec.updated_at = exchange_id, time.time()
                s.add(rec)
            s.commit()
        return Fill(order.client_order_id, order.size, price, fee, exchange_id)

    def reset(self) -> None:
        """A fresh paper account. Refused while a paper structure is open: its legs would be orphaned."""
        with session() as s:
            if s.exec(select(Structure).where(Structure.broker == self.name, col(Structure.status).in_(HELD))).first():
                raise Refused("close every paper structure before resetting the paper account")
            ids = [st.id for st in s.exec(select(Structure).where(Structure.broker == self.name)).all()]
            if ids:
                s.exec(delete(Leg).where(col(Leg.structure_id).in_(ids)))
                s.exec(delete(OrderRecord).where(col(OrderRecord.structure_id).in_(ids)))
                s.exec(delete(Structure).where(col(Structure.id).in_(ids)))
            s.exec(delete(PaperAccount))
            balance = self.settings().paper_starting_balance
            s.add(PaperAccount(id=1, cash=balance, starting_balance=balance, created_at=time.time()))
            s.commit()
