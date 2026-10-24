"""Settings repository — settings and toggles CRUD."""

from __future__ import annotations

from typing import Any

from sqlmodel import select

from app.domain.models import Setting, Toggle
from app.infrastructure.database.repositories.base_repo import BaseRepository


class SettingsRepository(BaseRepository[Setting]):
    """Data access for the settings and toggles tables."""

    model = Setting

    def get_all_settings(self) -> dict[str, str]:
        """Return every setting as a flat dictionary."""
        rows = self._session.exec(select(Setting)).all()
        return {row.key: row.value for row in rows}

    def upsert_setting(self, key: str, value: str) -> None:
        """Create or update a single setting."""
        existing = self.get_by_id(key)
        if existing:
            existing.value = value
            self._session.add(existing)
        else:
            self.create(Setting(key=key, value=value))

    def upsert_many(self, changes: dict[str, Any]) -> None:
        """Batch-upsert multiple settings."""
        for key, value in changes.items():
            self.upsert_setting(key, str(value))

    def get_all_toggles(self) -> list[dict[str, Any]]:
        """Return every toggle as a list of dicts."""
        rows = self._session.exec(select(Toggle)).all()
        return [{"key": r.key, "enabled": r.enabled} for r in rows]

    def upsert_toggle(self, key: str, enabled: bool) -> None:
        """Create or update a toggle."""
        existing = self._session.get(Toggle, key)
        if existing:
            existing.enabled = enabled
            self._session.add(existing)
        else:
            self._session.add(Toggle(key=key, enabled=enabled))
