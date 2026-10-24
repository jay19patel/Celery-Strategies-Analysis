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
from app.market.options import PERP_SLIPPAGE, UNDERLYING_OF
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
        value, notional, stale = 0.0, 0.0, []
        for leg in legs:
            if leg.entry_price is None or leg.exit_price is not None:
                continue
            touch = self._touch(leg.symbol)
            mark = touch[2] if touch else leg.entry_price
            if touch is None:
                stale.append(leg.symbol)
            sign = 1 if leg.side == "buy" else -1
            value += sign * mark * leg.size * leg.contract_value
            if leg.kind == "perp":  # its notional is not spent cash: shown back in the wallet
                notional += sign * leg.entry_price * leg.size * leg.contract_value
        margin = sum(st.margin for st in structures)
        equity = acct.cash + value
        return Account(
            broker=self.name, currency="USD", equity=round(equity, 6), available=round(equity - margin, 6),
            margin_used=round(margin, 6), cash=round(acct.cash + notional, 6),
            extra={"starting_balance": acct.starting_balance, "fees_paid": round(acct.fees_paid, 6), "created_at": acct.created_at, "unpriced_legs": stale},
        )

    def _touch(self, symbol: str) -> tuple[float | None, float | None, float, float, float] | None:
        """(bid, ask, mark, the spot fees are charged on, slippage) for an option or a perpetual."""
        if symbol in UNDERLYING_OF:
            perp = self.feed.perp(UNDERLYING_OF[symbol])
            if perp is None:
                return None
            return perp.stats.get("bid") or None, perp.stats.get("ask") or None, perp.mark, perp.mark, PERP_SLIPPAGE
        q = self.feed.quote(symbol)
        if q is None or not q.mark:
            return None
        return q.bid, q.ask, q.mark, q.spot or self.feed.spot(q.underlying) or 0.0, self.settings().paper_slippage_pct / 100

    async def execute(self, order: LegOrder) -> Fill:
        touch = self._touch(order.symbol)
        if touch is None:
            raise BrokerError(f"paper: no fresh quote for {order.symbol}")
        bid, ask, mark, spot, slip = touch
        if order.side == "buy":
            price = ask or mark * (1 + slip)
            beyond = price > order.limit_price
        else:
            price = bid or mark * (1 - slip)
            beyond = price < order.limit_price
        if beyond:
            raise BrokerError(f"paper: {order.symbol} would {order.side} at {price:,.2f}, beyond the limit {order.limit_price:,.2f}")
        units = order.size * order.contract_value
        fee = leg_fee(price, units, spot, await self.fee_rate(order.symbol))
        # An option's premium changes hands; a perpetual's notional does too, here, and comes back on
        # the close — the same arithmetic, so equity = cash + every held leg at its mark either way.
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
