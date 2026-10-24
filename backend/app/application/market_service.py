"""Market data service — bars, snapshots, and predictions."""

from __future__ import annotations

from typing import Any

from app.application.base_service import BaseService
from app.infrastructure.database.repositories.bar_repo import BarRepository


class MarketService(BaseService):
    """Serves market data and analysis to the API layer."""

    def __init__(self, bar_repo: BarRepository) -> None:
        super().__init__()
        self._bars = bar_repo

    def get_bars(self, symbol: str, interval: str = "15m", limit: int = 500, since: int | None = None) -> list[dict[str, Any]]:
        """Fetch OHLCV bars for charting."""
        return [
            {"time": b.time, "open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "volume": b.volume, "oi": b.oi, "funding": b.funding}
            for b in self._bars.get_bars(symbol, interval, limit, since)
        ]

    def get_snapshots(self, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
        """Analyst snapshots for a symbol."""
        return [
            {"bar_time": s.bar_time, "ts": s.ts, "price": s.price,
             "change_1h_pct": s.change_1h_pct, "change_24h_pct": s.change_24h_pct,
             "atr_pct": s.atr_pct, "ema20": s.ema20, "ema50": s.ema50,
             "atm_iv": s.atm_iv, "skew_25d": s.skew_25d,
             "pcr_oi": s.pcr_oi, "implied_move_pct": s.implied_move_pct,
             "insight_keys": s.insight_keys, "lean": s.lean}
            for s in self._bars.get_snapshots(symbol, limit)
        ]

    def get_predictions(self, symbol: str, source: str | None = None, scored_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        """Verdict predictions with scoring."""
        return [
            {"source": p.source, "bar_time": p.bar_time, "ts": p.ts,
             "horizon_hours": p.horizon_hours, "stance": p.stance,
             "label": p.label, "confidence": p.confidence, "price": p.price,
             "settled_at": p.settled_at, "actual_return": p.actual_return,
             "correct": p.correct, "r_multiple": p.r_multiple}
            for p in self._bars.get_predictions(symbol, source, scored_only, limit)
        ]
