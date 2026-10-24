"""Paper trading repository — account, trades, and stats."""

from __future__ import annotations

import time

from sqlmodel import col, select

from app.domain.models import PaperAccount, PaperTrade
from app.infrastructure.database.repositories.base_repo import BaseRepository


class PaperRepository(BaseRepository[PaperTrade]):
    """Data access for the paper trading subsystem."""

    model = PaperTrade

    # ── Account ───────────────────────────────────────────────

    def get_account(self) -> PaperAccount | None:
        """The single paper account row (id=1)."""
        return self._session.get(PaperAccount, 1)

    def create_account(self, starting_balance: float) -> PaperAccount:
        """Initialise a fresh paper account."""
        account = PaperAccount(
            id=1,
            balance=starting_balance,
            starting_balance=starting_balance,
            created_at=time.time(),
        )
        self._session.add(account)
        self._session.flush()
        return account

    def reset_account(self, starting_balance: float) -> PaperAccount:
        """Wipe and recreate the paper account."""
        account = self.get_account()
        if account:
            self._session.delete(account)
            self._session.flush()
        return self.create_account(starting_balance)

    # ── Trades ────────────────────────────────────────────────

    def list_trades(self, limit: int = 200, symbol: str | None = None) -> list[PaperTrade]:
        """Recent closed trades, newest first."""
        stmt = select(PaperTrade).order_by(col(PaperTrade.closed_at).desc()).limit(limit)
        if symbol:
            stmt = stmt.where(PaperTrade.symbol == symbol)
        return list(self._session.exec(stmt).all())

    def get_trades_in_range(self, start_ts: float, end_ts: float) -> list[PaperTrade]:
        """Trades closed within a time window — for journal."""
        stmt = (
            select(PaperTrade)
            .where(PaperTrade.closed_at >= start_ts, PaperTrade.closed_at < end_ts)
            .order_by(col(PaperTrade.closed_at))
        )
        return list(self._session.exec(stmt).all())

    def compute_stats(self) -> dict:
        """Aggregate statistics across all paper trades."""
        trades = self._session.exec(select(PaperTrade)).all()
        if not trades:
            return {
                "total_trades": 0, "winning": 0, "losing": 0,
                "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0,
                "best_trade": 0.0, "worst_trade": 0.0,
            }

        pnls = [t.pnl for t in trades]
        winning = [p for p in pnls if p > 0]
        losing = [p for p in pnls if p <= 0]

        return {
            "total_trades": len(trades),
            "winning": len(winning),
            "losing": len(losing),
            "win_rate": round(len(winning) / len(trades) * 100, 1),
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(sum(pnls) / len(pnls), 2),
            "best_trade": round(max(pnls), 2),
            "worst_trade": round(min(pnls), 2),
            "avg_win": round(sum(winning) / len(winning), 2) if winning else 0.0,
            "avg_loss": round(sum(losing) / len(losing), 2) if losing else 0.0,
            "profit_factor": round(sum(winning) / abs(sum(losing)), 2) if losing and sum(losing) != 0 else 0.0,
        }
