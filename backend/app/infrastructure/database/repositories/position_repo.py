"""Position repository — paper positions and trailing-stop controls."""

from __future__ import annotations

from sqlmodel import select

from app.domain.models import PaperPosition, PositionControl
from app.infrastructure.database.repositories.base_repo import BaseRepository


class PositionRepository(BaseRepository[PaperPosition]):
    """Data access for paper positions and per-position controls."""

    model = PaperPosition

    def get_all_open(self) -> list[PaperPosition]:
        """All currently open paper positions."""
        return self.list_all(limit=500)

    def get_by_symbol(self, symbol: str) -> PaperPosition | None:
        """Fetch the open position for a symbol."""
        return self.get_by_id(symbol)

    def close(self, symbol: str) -> bool:
        """Close a position by removing it from the open table."""
        return self.delete_by_id(symbol)

    # ── Position Controls ─────────────────────────────────────────

    def get_control(self, broker: str, symbol: str) -> PositionControl | None:
        """Fetch trailing-stop config for a specific position."""
        stmt = select(PositionControl).where(
            PositionControl.broker == broker,
            PositionControl.symbol == symbol,
        )
        return self._session.exec(stmt).first()

    def upsert_control(self, control: PositionControl) -> PositionControl:
        """Create or update position control settings."""
        existing = self.get_control(control.broker, control.symbol)
        if existing:
            existing.trailing = control.trailing
            existing.max_steps = control.max_steps
            existing.steps = control.steps
            existing.base_target = control.base_target
            existing.updated_at = control.updated_at
            existing.position = control.position
            self._session.add(existing)
            return existing
        self._session.add(control)
        self._session.flush()
        return control
