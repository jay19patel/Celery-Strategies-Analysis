"""Runtime settings, stored in the database and edited from the Settings page.

Fail closed: a change that would start real-money trading needs the
confirmation phrase, and any change to broker, exchange or keys switches
trading off.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from typing import Any

# Delta India. "demo" is the testnet behind the demo account.
ENVIRONMENTS = {
    "demo": ("https://cdn-ind.testnet.deltaex.org", "wss://socket-ind.testnet.deltaex.org"),
    "live": ("https://api.india.delta.exchange", "wss://socket.india.delta.exchange"),
}
LIVE_CONFIRM_PHRASE = "I UNDERSTAND THIS IS REAL MONEY"

SECRET_FIELDS = ("delta_api_key", "delta_api_secret")
# Changing any of these changes where orders or prices come from.
ROUTING_FIELDS = ("broker", "market_data", "delta_env", *SECRET_FIELDS)

CHOICES = {"broker": ("paper", "delta"), "market_data": ("demo", "live"), "delta_env": ("demo", "live")}
RANGES = {
    "stop_loss_pct": (0.05, 50.0),
    "take_profit_pct": (0.05, 100.0),
    "paper_starting_balance": (1.0, 10_000_000.0),
    "paper_leverage": (1.0, 100.0),
    "paper_fee_pct": (0.0, 1.0),
    "paper_slippage_pct": (0.0, 1.0),
    "paper_max_hold_hours": (0.0, 24 * 90),
}


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    broker: str = "paper"  # where orders go: "paper" (simulated) | "delta"
    market_data: str = "demo"  # price source while on paper: Delta "demo" | "live" (public data only)
    delta_env: str = "demo"  # Delta account used when broker == "delta"
    delta_api_key: str = ""
    delta_api_secret: str = ""
    stop_loss_pct: float = 1.0
    take_profit_pct: float = 2.0
    paper_starting_balance: float = 1000.0
    paper_leverage: float = 10.0
    paper_fee_pct: float = 0.05
    paper_slippage_pct: float = 0.02
    paper_max_hold_hours: float = 72.0  # 0 = no time exit

    @property
    def data_env(self) -> str:
        """Prices come from the exchange the orders go to; on paper, from the chosen feed."""
        return self.delta_env if self.broker == "delta" else self.market_data

    @property
    def is_real_money(self) -> bool:
        return self.broker == "delta" and self.delta_env == "live"

    @property
    def has_credentials(self) -> bool:
        return bool(self.delta_api_key and self.delta_api_secret)

    def public(self) -> dict[str, Any]:
        """Safe to send to a browser: secrets are masked, never returned."""
        data = asdict(self)
        key = self.delta_api_key
        data["delta_api_key"] = f"••••{key[-4:]}" if len(key) > 8 else ("set" if key else "")
        data["delta_api_secret"] = "set" if self.delta_api_secret else ""
        data |= {
            "data_env": self.data_env,
            "is_real_money": self.is_real_money,
            "has_credentials": self.has_credentials,
            "rest_url": ENVIRONMENTS[self.delta_env][0],
            "data_ws_url": ENVIRONMENTS[self.data_env][1],
        }
        return data


FIELD_TYPES = {f.name: f.type for f in fields(Settings)}


def apply_changes(current: Settings, changes: dict[str, Any], confirm: str = "") -> Settings:
    unknown = set(changes) - set(FIELD_TYPES)
    if unknown:
        raise SettingsError(f"unknown setting(s): {', '.join(sorted(unknown))}")

    clean: dict[str, Any] = {}
    for name, value in changes.items():
        if name in SECRET_FIELDS:
            value = str(value or "").strip()
            if not value:
                continue  # blank means "keep the stored secret"; use clear_credentials() to remove it
            clean[name] = value
        elif name in CHOICES:
            value = str(value).strip().lower()
            if value not in CHOICES[name]:
                raise SettingsError(f"{name} must be one of {', '.join(CHOICES[name])}")
            clean[name] = value
        else:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise SettingsError(f"{name} must be a number") from exc
            low, high = RANGES[name]
            if not low <= number <= high:
                raise SettingsError(f"{name} must be between {low:g} and {high:g}")
            clean[name] = number

    updated = replace(current, **clean)
    if updated.is_real_money and not current.is_real_money and confirm != LIVE_CONFIRM_PHRASE:
        raise SettingsError(f"real-money trading needs the confirmation phrase: {LIVE_CONFIRM_PHRASE}")
    return updated


def clear_credentials(current: Settings) -> Settings:
    return replace(current, delta_api_key="", delta_api_secret="")


def from_stored(values: dict[str, Any]) -> Settings:
    """Stored values are trusted but filtered: unknown or invalid keys fall back to defaults."""
    settings = Settings()
    for name, value in values.items():
        if name not in FIELD_TYPES:
            continue
        try:
            settings = apply_changes(settings, {name: value}, confirm=LIVE_CONFIRM_PHRASE)
        except SettingsError:
            continue
    return settings


def changed_fields(before: Settings, after: Settings) -> list[str]:
    return [f for f in FIELD_TYPES if getattr(before, f) != getattr(after, f)]
