"""Runtime settings, stored in the database and edited from the Settings page.

Paper and Delta are independent brokers: either or both can be active. Fail
closed: a change that would start real-money trading needs the confirmation
phrase, and any change to the Delta account or keys switches Delta trading off.
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

BROKERS = ("paper", "delta")
SECRET_FIELDS = ("delta_api_key", "delta_api_secret")
# Changing any of these changes where Delta orders go: Delta trading stops.
DELTA_ROUTING_FIELDS = ("delta_active", "delta_env", *SECRET_FIELDS)
# Changing any of these changes where prices come from: the stream restarts.
STREAM_FIELDS = ("paper_active", "market_data", *DELTA_ROUTING_FIELDS)

BOOL_FIELDS = ("paper_active", "delta_active", "trailing_enabled")
CHOICES = {"market_data": ("demo", "live"), "delta_env": ("demo", "live"), "day_timezone": ("Asia/Kolkata", "UTC")}
INT_FIELDS = ("trailing_max_steps",)
RANGES = {
    "stop_loss_pct": (0.05, 50.0),
    "take_profit_pct": (0.05, 100.0),
    "paper_starting_balance": (1.0, 10_000_000.0),
    "paper_leverage": (1.0, 100.0),
    "paper_fee_pct": (0.0, 1.0),
    "paper_slippage_pct": (0.0, 1.0),
    "paper_max_hold_hours": (0.0, 24 * 90),
    "trade_margin_pct": (1.0, 100.0),
    "trailing_trigger_pct": (50.0, 99.0),
    "trailing_extend_pct": (10.0, 300.0),
    "trailing_lock_pct": (0.0, 95.0),
    "trailing_max_steps": (0, 50),
    "daily_loss_limit_pct": (0.0, 100.0),
}


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    paper_active: bool = True  # simulated broker; always on
    delta_active: bool = False  # Delta broker; shown and fed signals
    # Price source when Delta is not active: "live" (public data only, no keys) | "demo". Live by default:
    # paper should trade on the real market, and the testnet socket refuses connections from outside India.
    market_data: str = "live"
    delta_env: str = "demo"  # Delta account: "demo" (testnet) | "live" (real money)
    delta_api_key: str = ""
    delta_api_secret: str = ""
    stop_loss_pct: float = 1.0
    take_profit_pct: float = 2.0
    paper_starting_balance: float = 1000.0
    paper_leverage: float = 10.0
    paper_fee_pct: float = 0.05
    paper_slippage_pct: float = 0.02
    paper_max_hold_hours: float = 72.0  # 0 = no time exit
    trade_margin_pct: float = 20.0  # % of available margin to use per trade
    # Auto trailing: when price has covered trailing_trigger_pct of the way from entry to the target,
    # the target moves out by trailing_extend_pct of the original target distance and the stop moves
    # to lock trailing_lock_pct of the open profit. A stop only ever tightens. At most trailing_max_steps
    # times per position; after that the target stays and the position closes there.
    trailing_enabled: bool = True  # default for new positions; each position can be switched on its own
    trailing_trigger_pct: float = 80.0
    trailing_extend_pct: float = 50.0
    trailing_lock_pct: float = 50.0
    trailing_max_steps: int = 3
    # Daily loss limit, per broker: equity down this % from the start of the trading day closes every
    # position on that broker and blocks new entries until the next day. 0 = off.
    daily_loss_limit_pct: float = 5.0
    day_timezone: str = "Asia/Kolkata"  # when the trading day starts

    @property
    def active_brokers(self) -> list[str]:
        return [b for b in BROKERS if getattr(self, f"{b}_active")]

    @property
    def data_env(self) -> str:
        """With Delta active, every broker is priced by the exchange Delta trades on."""
        return self.delta_env if self.delta_active else self.market_data

    @property
    def is_real_money(self) -> bool:
        return self.delta_active and self.delta_env == "live"

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
            "active_brokers": self.active_brokers,
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
        elif name in BOOL_FIELDS:
            if not isinstance(value, bool):
                raise SettingsError(f"{name} must be true or false")
            clean[name] = value
        elif name in INT_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int | float | str):
                raise SettingsError(f"{name} must be a whole number")
            try:
                number = float(value)
            except ValueError as exc:
                raise SettingsError(f"{name} must be a whole number") from exc
            low, high = RANGES[name]
            if number != int(number) or not low <= number <= high:
                raise SettingsError(f"{name} must be a whole number between {low} and {high}")
            clean[name] = int(number)
        elif name in CHOICES:
            value = str(value).strip()
            value = value.lower() if name != "day_timezone" else value
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
    if not updated.active_brokers:
        raise SettingsError("keep at least one broker active")
    if updated.is_real_money and not current.is_real_money and confirm != LIVE_CONFIRM_PHRASE:
        raise SettingsError(f"real-money trading needs the confirmation phrase: {LIVE_CONFIRM_PHRASE}")
    return updated


def clear_credentials(current: Settings) -> Settings:
    return replace(current, delta_api_key="", delta_api_secret="")


def from_stored(values: dict[str, Any]) -> Settings:
    """Stored values are trusted but filtered: unknown or invalid keys fall back to defaults."""
    valid = {}
    for name, value in values.items():
        if name not in FIELD_TYPES:
            continue
        try:
            # Validate each field on its own; the at-least-one-broker rule is checked on the whole below.
            apply_changes(replace(Settings(), paper_active=True, delta_active=True), {name: value}, confirm=LIVE_CONFIRM_PHRASE)
        except SettingsError:
            continue
        valid[name] = value
    try:
        return apply_changes(Settings(), valid, confirm=LIVE_CONFIRM_PHRASE)
    except SettingsError:  # e.g. both brokers stored inactive
        return apply_changes(Settings(), {k: v for k, v in valid.items() if k not in BOOL_FIELDS}, confirm=LIVE_CONFIRM_PHRASE)


def changed_fields(before: Settings, after: Settings) -> list[str]:
    return [f for f in FIELD_TYPES if getattr(before, f) != getattr(after, f)]
