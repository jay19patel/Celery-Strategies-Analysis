"""Runtime settings, stored in the database and edited on the Settings page.

Few on purpose: every field is a decision the owner can make well. A strategy's own numbers are not
here — they are its `Params` (`app/strategies/`), stored per strategy in `strategies`.

Fail closed:
  - Delta is inactive and on its demo (testnet) account by default.
  - Becoming real-money needs LIVE_CONFIRM_PHRASE in the same request.
  - Auto-trading on Delta is off at every engine start, and any change to the Delta account or
    keys switches it off again: it is turned on deliberately, after the change, or not at all.
  - A strategy trades on Delta only when its own `live` switch is on as well.
Secrets are never returned: `public()` masks them, and a blank secret in an update means "keep".
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.errors import SettingsError

ENVIRONMENTS = {
    "demo": ("https://cdn-ind.testnet.deltaex.org", "wss://socket-ind.testnet.deltaex.org"),
    "live": ("https://api.india.delta.exchange", "wss://socket.india.delta.exchange"),
}
LIVE_CONFIRM_PHRASE = "I UNDERSTAND THIS IS REAL MONEY"
BROKERS = ("paper", "delta")
SECRET_FIELDS = ("delta_api_key", "delta_api_secret", "telegram_bot_token", "email_smtp_pass")
DELTA_ROUTING_FIELDS = ("delta_active", "delta_env", "delta_api_key", "delta_api_secret")
EVENT_FORMAT = "%Y-%m-%d %H:%M"
EVENT_BUFFER_HOURS = 2.0  # a seller stands aside from this long before a scheduled event
MODEL_ID = re.compile(r"^[A-Za-z0-9._:/@+\-]{2,120}$")
EMAIL = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")

Underlying = Literal["BTC", "ETH", "XAUT"]


class StrategyConfig(BaseModel):
    """One strategy's switches and numbers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    live: bool = False  # may it trade on Delta too (still behind `auto_delta` and a measured edge)
    underlyings: list[Underlying] = Field(default_factory=list)  # empty: every enabled underlying
    params: dict[str, Any] = Field(default_factory=dict)  # only values that differ from the defaults


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # -- brokers ---------------------------------------------------------------------------
    paper_active: bool = True
    delta_active: bool = False
    delta_env: Literal["demo", "live"] = "demo"
    delta_api_key: str = Field("", max_length=200)
    delta_api_secret: str = Field("", max_length=200)
    underlyings: list[Underlying] = Field(default_factory=lambda: ["BTC"], min_length=1)

    # -- paper -----------------------------------------------------------------------------
    paper_starting_balance: float = Field(1000.0, ge=10, le=10_000_000)
    paper_leverage: float = Field(10.0, ge=1, le=100)  # the paper account's perpetual leverage

    # -- automation ------------------------------------------------------------------------
    auto_paper: bool = True
    auto_delta: bool = False
    strategies: dict[str, StrategyConfig] = Field(default_factory=dict)  # name -> its config; absent: its defaults

    # -- options ---------------------------------------------------------------------------
    max_spread_pct: float = Field(12.0, ge=1, le=100)  # each leg's bid/ask spread as % of its mark
    # Scheduled events (UTC, "YYYY-MM-DD HH:MM") a premium seller stands aside for: no credit
    # structure is opened whose life, from EVENT_BUFFER_HOURS before now to its expiry, holds one.
    event_times: list[str] = Field(default_factory=lambda: ["2026-12-09 19:00"], max_length=100)  # FOMC
    exit_before_expiry_minutes: int = Field(60, ge=0, le=1440)

    # -- risk ------------------------------------------------------------------------------
    risk_per_trade_pct: float = Field(0.5, ge=0.1, le=5)  # worst case of one automatic entry, % of equity
    daily_loss_limit_pct: float = Field(5.0, ge=0, le=100)  # 0 = off
    # Equity this far below its peak stops every entry on every broker until resumed by hand. 0 = off.
    max_drawdown_pct: float = Field(25.0, ge=0, le=90)
    max_lots: int = Field(50, ge=1, le=100_000)  # a fat-finger cap on one order, not the sizing rule
    day_timezone: Literal["Asia/Kolkata", "UTC"] = "Asia/Kolkata"

    # -- AI (LiteLLM) ----------------------------------------------------------------------
    ai_enabled: bool = False
    ai_model: str = "mistral/mistral-small-latest"
    ai_fallback_models: list[str] = Field(default_factory=list, max_length=5)

    # -- notifications ---------------------------------------------------------------------
    notify_telegram: bool = False
    telegram_bot_token: str = Field("", max_length=200)
    telegram_chat_id: str = Field("", max_length=64)
    notify_email: bool = False
    email_to: str = Field("", max_length=500)
    email_smtp_host: str = Field("", max_length=253)
    email_smtp_port: int = Field(587, ge=1, le=65535)
    email_smtp_user: str = Field("", max_length=200)
    email_smtp_pass: str = Field("", max_length=200)
    daily_report: bool = True
    daily_report_hour: int = Field(21, ge=0, le=23)

    @field_validator("ai_model")
    @classmethod
    def _model_id(cls, v: str) -> str:
        v = v.strip()
        if not MODEL_ID.match(v):
            raise ValueError("must be a LiteLLM model id such as mistral/mistral-small-latest")
        return v

    @field_validator("ai_fallback_models")
    @classmethod
    def _model_ids(cls, v: list[str]) -> list[str]:
        out = [m.strip() for m in v if m.strip()]
        for m in out:
            if not MODEL_ID.match(m):
                raise ValueError(f"{m!r} is not a LiteLLM model id")
        return out

    @field_validator("email_to")
    @classmethod
    def _emails(cls, v: str) -> str:
        v = v.strip()
        if v and not all(EMAIL.match(a.strip()) for a in v.split(",") if a.strip()):
            raise ValueError("must be email addresses separated by commas")
        return v

    @field_validator("event_times")
    @classmethod
    def _event_times(cls, v: list[str]) -> list[str]:
        out = sorted({t.strip() for t in v if t.strip()})
        for t in out:
            try:
                datetime.strptime(t, EVENT_FORMAT).replace(tzinfo=UTC)
            except ValueError:
                raise ValueError(f"{t!r} is not a UTC time like 2026-12-09 19:00") from None
        return out

    @field_validator("underlyings")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @field_validator("strategies")
    @classmethod
    def _strategies(cls, v: dict[str, StrategyConfig]) -> dict[str, StrategyConfig]:
        from app.strategies import registry  # the strategies import this module

        out = {}
        for name, cfg in v.items():
            try:
                params = registry.validate_params(name, cfg.params)
            except ValidationError as exc:
                raise ValueError(f"{name}: {_message(exc)}") from None
            out[name] = cfg.model_copy(update={"params": params, "underlyings": list(dict.fromkeys(cfg.underlyings))})
        return out

    @model_validator(mode="after")
    def _consistent(self) -> Settings:
        if not (self.paper_active or self.delta_active):
            raise ValueError("keep at least one broker active")
        return self

    # -- derived ---------------------------------------------------------------------------

    @property
    def active_brokers(self) -> list[str]:
        return [b for b in BROKERS if getattr(self, f"{b}_active")]

    @property
    def is_real_money(self) -> bool:
        return self.delta_active and self.delta_env == "live"

    @property
    def data_env(self) -> str:
        """With Delta active every broker is priced by the exchange Delta trades on; otherwise the
        live public data, which needs no keys."""
        return self.delta_env if self.delta_active else "live"

    @property
    def has_delta_credentials(self) -> bool:
        return bool(self.delta_api_key and self.delta_api_secret)

    @property
    def email_recipients(self) -> list[str]:
        return [a.strip() for a in self.email_to.split(",") if a.strip()]

    def event_between(self, start: float, end: float) -> str:
        """The first scheduled event from `EVENT_BUFFER_HOURS` before `start` to `end`, or ""."""
        lo = start - EVENT_BUFFER_HOURS * 3600
        for t in self.event_times:
            at = datetime.strptime(t, EVENT_FORMAT).replace(tzinfo=UTC).timestamp()
            if lo <= at <= end:
                return t
        return ""

    def auto_on(self, broker: str) -> bool:
        return getattr(self, f"{broker}_active") and getattr(self, f"auto_{broker}")

    def config(self, name: str) -> StrategyConfig:
        """A strategy's stored config, else its defaults (on only if it says `default_on`)."""
        if name in self.strategies:
            return self.strategies[name]
        from app.strategies import registry

        cls = registry.get(name)
        return StrategyConfig(enabled=bool(cls and cls.default_on))

    def strategy_on(self, name: str, underlying: str) -> bool:
        """Both switches: the strategy is enabled, and this underlying is one it may trade."""
        cfg = self.config(name)
        return cfg.enabled and (not cfg.underlyings or underlying in cfg.underlyings)

    def strategy_brokers(self, name: str) -> list[str]:
        """The brokers a strategy's automatic entries go to: every auto-on broker, Delta only with
        the strategy's own `live` switch on."""
        live = self.config(name).live
        return [b for b in self.active_brokers if self.auto_on(b) and (b != "delta" or live)]

    def public(self) -> dict[str, Any]:
        data = self.model_dump()
        key = self.delta_api_key
        data["delta_api_key"] = f"••••{key[-4:]}" if len(key) > 8 else ("set" if key else "")
        for name in ("delta_api_secret", "telegram_bot_token", "email_smtp_pass"):
            data[name] = "set" if getattr(self, name) else ""
        return data | {
            "active_brokers": self.active_brokers,
            "is_real_money": self.is_real_money,
            "data_env": self.data_env,
            "has_delta_credentials": self.has_delta_credentials,
            "rest_url": ENVIRONMENTS[self.delta_env][0],
        }


def _message(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"]) or "settings"
        parts.append(f"{where}: {err['msg'].removeprefix('Value error, ')}")
    return "; ".join(parts)


def apply_changes(current: Settings, changes: dict[str, Any], confirm: str = "") -> Settings:
    unknown = set(changes) - set(Settings.model_fields)
    if unknown:
        raise SettingsError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    data = current.model_dump()
    for name, value in changes.items():
        if name in SECRET_FIELDS:
            value = str(value or "").strip()
            if not value:
                continue  # blank keeps the stored secret; clearing is its own action
        data[name] = value
    try:
        updated = Settings.model_validate(data)
    except ValidationError as exc:
        raise SettingsError(_message(exc)) from exc

    if updated.is_real_money and not current.is_real_money and confirm != LIVE_CONFIRM_PHRASE:
        raise SettingsError(f"real-money trading needs the confirmation phrase: {LIVE_CONFIRM_PHRASE}")
    if any(getattr(updated, f) != getattr(current, f) for f in DELTA_ROUTING_FIELDS) and updated.auto_delta:
        updated = updated.model_copy(update={"auto_delta": False})
    return updated


def clear_delta_credentials(current: Settings) -> Settings:
    return current.model_copy(update={"delta_api_key": "", "delta_api_secret": "", "auto_delta": False})


# Settings an earlier version kept globally that are now a strategy's params.
LEGACY_OPTION_PARAMS = ("expiry_min_hours", "expiry_max_hours", "short_delta", "wing_width_pct", "min_pop_pct", "min_credit_width_pct")


def _migrate(values: dict[str, Any]) -> dict[str, Any]:
    """Settings stored by the version with global strategy lists become per-strategy configs: each
    strategy keeps its on/off, its underlyings and the option numbers it used. Run once, on the
    first start of this version; afterwards `strategies` is what is stored."""
    if "strategies" in values or not ({"directional_strategies", "auto_strategies"} & set(values)):
        return values
    from app.strategies import registry

    on = set(values.get("directional_strategies") or []) | set(values.get("auto_strategies") or [])
    if values.get("zh_enabled"):
        on.add("expiry_decay")  # the decay spread rode on the zero-hero switch
    symbols = values.get("strategy_symbols") or {}
    out: dict[str, Any] = {}
    for name in registry.names():
        cls = registry.get(name)
        legacy = {k: values[k] for k in LEGACY_OPTION_PARAMS if k in values and cls and k in cls.Params.model_fields}
        try:
            params = registry.validate_params(name, legacy)
        except ValidationError:
            params = {}
        out[name] = {"enabled": name in on, "underlyings": list(symbols.get(name) or []), "params": params}
    return values | {"strategies": out}


def from_stored(values: dict[str, Any]) -> Settings:
    """Stored values are filtered field by field: one that no longer validates falls back to its default."""
    values = _migrate(values)
    if isinstance(values.get("strategies"), dict):  # one strategy that no longer validates loses only its own config
        good = {}
        for name, cfg in values["strategies"].items():
            try:
                Settings.model_validate({"strategies": {name: cfg}})
            except ValidationError:
                continue
            good[name] = cfg
        values = values | {"strategies": good}
    base = Settings().model_dump()
    kept: dict[str, Any] = {}
    for name, value in values.items():
        if name not in Settings.model_fields:
            continue
        try:
            Settings.model_validate(base | kept | {name: value})
        except ValidationError:
            continue
        kept[name] = value
    return Settings.model_validate(base | kept)


def changed_fields(before: Settings, after: Settings) -> list[str]:
    return [f for f in Settings.model_fields if getattr(before, f) != getattr(after, f)]
