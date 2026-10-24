"""Bar repository — OHLCV bars, market snapshots, and predictions."""

from __future__ import annotations

from sqlmodel import col, select

from app.domain.models import Bar, MarketSnapshot, Prediction
from app.infrastructure.database.repositories.base_repo import BaseRepository


class BarRepository(BaseRepository[Bar]):
    """Data access for bars, market snapshots, and predictions."""

    model = Bar

    def get_bars(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 500,
        since: int | None = None,
    ) -> list[Bar]:
        """Fetch OHLCV bars in chronological order."""
        stmt = (
            select(Bar)
            .where(Bar.symbol == symbol, Bar.interval == interval)
            .order_by(col(Bar.time).desc())
            .limit(limit)
        )
        if since:
            stmt = stmt.where(Bar.time >= since)
        rows = list(self._session.exec(stmt).all())
        rows.reverse()
        return rows

    def get_latest_bar(self, symbol: str, interval: str = "15m") -> Bar | None:
        """The most recent closed bar."""
        stmt = (
            select(Bar)
            .where(Bar.symbol == symbol, Bar.interval == interval)
            .order_by(col(Bar.time).desc())
            .limit(1)
        )
        return self._session.exec(stmt).first()

    def get_snapshots(self, symbol: str, limit: int = 50) -> list[MarketSnapshot]:
        """Recent analyst readings for a symbol, chronological."""
        stmt = (
            select(MarketSnapshot)
            .where(MarketSnapshot.symbol == symbol)
            .order_by(col(MarketSnapshot.ts).desc())
            .limit(limit)
        )
        rows = list(self._session.exec(stmt).all())
        rows.reverse()
        return rows

    def get_latest_snapshot(self, symbol: str) -> MarketSnapshot | None:
        """The most recent analyst snapshot."""
        stmt = (
            select(MarketSnapshot)
            .where(MarketSnapshot.symbol == symbol)
            .order_by(col(MarketSnapshot.ts).desc())
            .limit(1)
        )
        return self._session.exec(stmt).first()

    def get_predictions(
        self,
        symbol: str,
        source: str | None = None,
        scored_only: bool = False,
        limit: int = 100,
    ) -> list[Prediction]:
        """Fetch verdict predictions, chronological."""
        stmt = (
            select(Prediction)
            .where(Prediction.symbol == symbol)
            .order_by(col(Prediction.bar_time).desc())
            .limit(limit)
        )
        if source:
            stmt = stmt.where(Prediction.source == source)
        if scored_only:
            stmt = stmt.where(Prediction.settled_at.is_not(None))  # noqa: E711
        rows = list(self._session.exec(stmt).all())
        rows.reverse()
        return rows
