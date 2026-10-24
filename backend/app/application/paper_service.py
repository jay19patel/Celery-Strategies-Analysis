"""Paper trading service — account, trades, and statistics."""

from __future__ import annotations

from typing import Any

from app.application.base_service import BaseService
from app.infrastructure.database.repositories.paper_repo import PaperRepository


class PaperService(BaseService):
    """Manages the paper trading account and trade history."""

    def __init__(self, repo: PaperRepository) -> None:
        super().__init__()
        self._repo = repo

    def get_account(self) -> dict[str, Any]:
        """Paper account balances."""
        account = self._repo.get_account()
        if not account:
            return {"balance": 0.0, "starting_balance": 0.0, "realized_pnl": 0.0, "fees_paid": 0.0, "exists": False}
        return {
            "balance": account.balance, "starting_balance": account.starting_balance,
            "realized_pnl": account.realized_pnl, "fees_paid": account.fees_paid,
            "created_at": account.created_at, "exists": True,
        }

    def get_trades(self, limit: int = 200, symbol: str | None = None) -> list[dict[str, Any]]:
        """Recent closed trades."""
        return [
            {"id": t.id, "strategy": t.strategy, "symbol": t.symbol, "side": t.side,
             "entry_price": t.entry_price, "exit_price": t.exit_price, "leverage": t.leverage,
             "pnl": t.pnl, "fees": t.fees, "reason": t.reason,
             "opened_at": t.opened_at, "closed_at": t.closed_at}
            for t in self._repo.list_trades(limit=limit, symbol=symbol)
        ]

    def get_stats(self) -> dict[str, Any]:
        """Aggregate statistics across all paper trades."""
        return self._repo.compute_stats()

    def reset(self, starting_balance: float = 10000.0) -> dict[str, Any]:
        """Reset the paper account."""
        account = self._repo.reset_account(starting_balance)
        self._info("paper_reset", balance=starting_balance)
        return {"balance": account.balance, "starting_balance": account.starting_balance, "reset": True}

    def get_trades_in_range(self, start_ts: float, end_ts: float) -> list[dict[str, Any]]:
        """Trades closed within a time window — for journal."""
        return [
            {"id": t.id, "strategy": t.strategy, "symbol": t.symbol, "side": t.side,
             "entry_price": t.entry_price, "exit_price": t.exit_price,
             "pnl": t.pnl, "reason": t.reason,
             "opened_at": t.opened_at, "closed_at": t.closed_at}
            for t in self._repo.get_trades_in_range(start_ts, end_ts)
        ]
