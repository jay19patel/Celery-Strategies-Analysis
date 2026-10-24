"""Order repository — order lifecycle queries."""

from __future__ import annotations

from sqlmodel import col, select

from app.domain.models import Order
from app.infrastructure.database.repositories.base_repo import BaseRepository


class OrderRepository(BaseRepository[Order]):
    """Data access for the orders table."""

    model = Order

    def update_status(
        self,
        client_order_id: str,
        status: str,
        order_id: str | None = None,
        error: str | None = None,
    ) -> Order | None:
        """Transition an order to a new status."""
        order = self.get_by_id(client_order_id)
        if not order:
            return None
        order.status = status
        if order_id is not None:
            order.order_id = order_id
        if error is not None:
            order.error = error
        self._session.add(order)
        return order

    def list_recent(self, limit: int = 100, broker: str | None = None) -> list[Order]:
        """Most recent orders, optionally filtered by broker."""
        stmt = select(Order).order_by(col(Order.ts).desc()).limit(limit)
        if broker:
            stmt = stmt.where(Order.broker == broker)
        return list(self._session.exec(stmt).all())

    def list_open(self) -> list[Order]:
        """Orders not yet in a terminal state."""
        stmt = (
            select(Order)
            .where(Order.status.in_(["pending", "placed"]))
            .order_by(col(Order.ts).desc())
        )
        return list(self._session.exec(stmt).all())
