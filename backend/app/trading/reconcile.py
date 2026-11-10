"""The exchange against the database: what Delta says the account holds, per contract, against what
the open structures say it should hold.

Run at start and every few minutes while Delta is active and has keys. A mismatch — a trade made
outside the desk, a stop that filled while the engine was down, a fill the database missed — is
published as an error, sent to Telegram, and blocks new entries on Delta (`trading.risk`) until the
two agree again. It never trades to make them agree: which side is right is a human's call.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import col, select

from app.core.db import session
from app.core.events import EventHub
from app.core.models import Leg, Structure
from app.errors import BrokerError
from app.trading.risk import reconcile_reason, set_reconcile
from app.trading.service import HELD


def expected_positions(broker: str) -> dict[str, int]:
    """Signed contracts per symbol the database says `broker` holds (positive long)."""
    with session() as s:
        ids = [st.id for st in s.exec(select(Structure).where(Structure.broker == broker, col(Structure.status).in_(HELD))).all()]
        legs = s.exec(select(Leg).where(col(Leg.structure_id).in_(ids))).all() if ids else []
    out: dict[str, int] = {}
    for leg in legs:
        if leg.entry_price is not None and leg.exit_price is None:
            out[leg.symbol] = out.get(leg.symbol, 0) + (leg.size if leg.side == "buy" else -leg.size)
    return {k: v for k, v in out.items() if v}


def differences(expected: dict[str, int], actual: dict[str, int]) -> list[str]:
    return [
        f"{sym}: exchange {actual.get(sym, 0):+d}, desk {expected.get(sym, 0):+d}"
        for sym in sorted(set(expected) | set(actual)) if expected.get(sym, 0) != actual.get(sym, 0)
    ]


class Reconciler:
    def __init__(self, brokers: dict[str, Any], settings: Any, hub: EventHub) -> None:
        self.brokers = brokers
        self.settings: Any = settings
        self.hub = hub

    async def run(self) -> str:
        s = self.settings()
        broker = self.brokers.get("delta")
        if broker is None or "delta" not in s.active_brokers or broker.not_ready():
            if reconcile_reason("delta"):
                set_reconcile("delta", "")  # nothing to compare: an inactive broker blocks nothing
            return "delta inactive"
        try:
            actual = await broker.positions()
        except BrokerError as exc:
            return f"positions unreadable: {exc}"  # unknown is not a mismatch; the gate's account read covers it
        diff = differences(expected_positions("delta"), actual)
        was = reconcile_reason("delta")
        if diff:
            why = "; ".join(diff)[:500]
            set_reconcile("delta", why)
            if why != was:
                self.hub.publish("ReconcileMismatch", f"delta positions do not match the desk: {why} — no new entries until they do", "error",
                                 broker="delta", differences=diff)
            return f"mismatch: {why}"
        if was:
            set_reconcile("delta", "")
            self.hub.publish("ReconcileMatched", "delta positions match the desk again: entries resumed", "warning", broker="delta")
        return f"{len(actual)} positions match"
