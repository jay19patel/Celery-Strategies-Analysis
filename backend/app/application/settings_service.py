"""Settings service — validates and applies settings changes."""

from __future__ import annotations

from typing import Any

from app.application.base_service import BaseService
from app.domain.exceptions import SettingsError, ValidationError
from app.infrastructure.database.repositories.settings_repo import SettingsRepository

LIVE_CONFIRM_PHRASE = "I UNDERSTAND THIS IS REAL MONEY"

BOOL_FIELDS = {
    "paper_active", "delta_active", "trailing_enabled", "email_enabled",
    "atr_protection", "ai_enabled", "early_exit_enabled",
}

RANGES: dict[str, tuple[float, float]] = {
    "stop_loss_pct": (0.05, 50.0),
    "take_profit_pct": (0.05, 100.0),
    "paper_starting_balance": (1.0, 10_000_000.0),
    "paper_leverage": (1.0, 100.0),
    "paper_fee_pct": (0.0, 1.0),
    "paper_slippage_pct": (0.0, 1.0),
    "trade_margin_pct": (1.0, 100.0),
    "risk_per_trade_pct": (0.05, 10.0),
    "atr_stop_mult": (0.5, 10.0),
    "atr_target_mult": (0.5, 20.0),
    "trailing_trigger_pct": (50.0, 99.0),
    "trailing_extend_pct": (10.0, 300.0),
    "trailing_lock_pct": (0.0, 95.0),
    "trailing_max_steps": (0, 50),
    "daily_loss_limit_pct": (0.0, 100.0),
    "signal_settle_seconds": (0.0, 30.0),
}

SECRET_FIELDS = {"delta_api_key", "delta_api_secret", "email_smtp_pass"}
DELTA_ROUTING_FIELDS = {"delta_active", "delta_env", "delta_api_key", "delta_api_secret"}


class SettingsService(BaseService):
    """Orchestrates settings reads and validated writes."""

    def __init__(self, repo: SettingsRepository, publisher: EventPublisher | None = None) -> None:
        super().__init__(publisher)
        self._repo = repo

    def get_all(self) -> dict[str, Any]:
        """Return all settings with secrets masked."""
        raw = self._repo.get_all_settings()
        return {
            key: ("***" if key in SECRET_FIELDS and value else value)
            for key, value in raw.items()
        }

    def get_raw(self) -> dict[str, str]:
        """Return all settings unmasked — internal use only."""
        return self._repo.get_all_settings()

    def get_toggles(self) -> list[dict[str, Any]]:
        """Return all feature toggles."""
        return self._repo.get_all_toggles()

    def update_toggle(self, key: str, enabled: bool) -> None:
        """Flip a feature toggle."""
        self._repo.upsert_toggle(key, enabled)
        self._info("toggle_changed", key=key, enabled=enabled)

    def update_settings(self, changes: dict[str, Any], confirm: str = "") -> dict[str, str]:
        """Validate and apply settings changes.

        Returns:
            Dict of warnings (key → message).

        Raises:
            SettingsError: If confirmation is missing for live trading.
            ValidationError: If a value is out of range.
        """
        warnings: dict[str, str] = {}

        if changes.get("delta_active") in (True, "true", "1") and confirm != LIVE_CONFIRM_PHRASE:
            raise SettingsError(
                f"Enabling live trading requires: '{LIVE_CONFIRM_PHRASE}'"
            )

        validated: dict[str, str] = {}
        for key, value in changes.items():
            validated[key] = self._validate_field(key, value)

        if any(k in DELTA_ROUTING_FIELDS for k in validated) and "delta_active" not in validated:
            validated["delta_active"] = "false"
            warnings["delta_active"] = "Delta trading disabled — routing fields changed"

        self._repo.upsert_many(validated)
        self._info("settings_updated", keys=list(validated.keys()))
        return warnings

    def _validate_field(self, key: str, value: Any) -> str:
        """Validate a single field and return its string representation."""
        if key in BOOL_FIELDS:
            if isinstance(value, bool):
                return str(value).lower()
            if isinstance(value, str) and value.lower() in ("true", "false", "1", "0"):
                return "true" if value.lower() in ("true", "1") else "false"
            raise ValidationError(f"{key} must be a boolean")

        if key in RANGES:
            lo, hi = RANGES[key]
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"{key} must be a number") from exc
            if not lo <= numeric <= hi:
                raise ValidationError(f"{key} must be between {lo} and {hi}")
            return str(numeric)

        return str(value)
