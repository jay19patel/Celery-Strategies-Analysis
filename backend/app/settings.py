"""Runtime settings, stored in the database and edited on the Settings page.

Fail closed:
  - Delta is inactive and on its demo (testnet) account by default.
  - Becoming real-money needs LIVE_CONFIRM_PHRASE in the same request.
  - Auto-trading on Delta is off at every engine start, and any change to the Delta account or
    keys switches it off again: it is turned on deliberately, after the change, or not at all.
Secrets are never returned: `public()` masks them, and a blank secret in an update means "keep".
"""

from __future__ import annotations

import re
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
STRATEGY_KEYS = (
    "iron_condor", "iron_butterfly", "short_strangle", "short_straddle",
    "long_straddle", "bull_put_spread", "bear_call_spread",
)
MODEL_ID = re.compile(r"^[A-Za-z0-9._:/@+\-]{2,120}$")
EMAIL = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")

Underlying = Literal["BTC", "ETH"]
Directional = Literal["ema_vwap", "ema_9_15", "pcr_options", "supertrend"]
Strategy = Literal[
    "iron_condor", "iron_butterfly", "short_strangle", "short_straddle",
    "long_straddle", "bull_put_spread", "bear_call_spread",
]


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # -- brokers ---------------------------------------------------------------------------
    paper_active: bool = True
    delta_active: bool = False
    delta_env: Literal["demo", "live"] = "demo"
    delta_api_key: str = Field("", max_length=200)
    delta_api_secret: str = Field("", max_length=200)
    # Where prices come from while Delta is not active. Live public data needs no keys.
    market_data: Literal["live", "demo"] = "live"
    underlyings: list[Underlying] = Field(default_factory=lambda: ["BTC"], min_length=1)

    # -- paper -----------------------------------------------------------------------------
    paper_starting_balance: float = Field(1000.0, ge=10, le=10_000_000)
    # Applied to the mark when a leg has no quote on the side it trades; with a quote it fills there.
    paper_slippage_pct: float = Field(2.0, ge=0, le=25)

    # -- auto-trader -----------------------------------------------------------------------
    # The per-broker switches cover everything automatic; under them, the options auto-trader and
    # each directional strategy can be switched on its own.
    auto_paper: bool = True
    auto_delta: bool = False
    auto_options: bool = True
    directional_strategies: list[Directional] = Field(default_factory=lambda: ["ema_vwap", "ema_9_15", "pcr_options", "supertrend"])
    # Two switches, as in v3: the strategy itself above, and which underlyings it may trade here.
    # A strategy absent from this map, or mapped to an empty list, runs on every enabled underlying.
    strategy_symbols: dict[Directional, list[Underlying]] = Field(default_factory=dict)
    auto_interval_minutes: int = Field(15, ge=5, le=240)
    # Every structure in the catalog is on. Note that short_strangle and short_straddle are the two
    # whose loss is NOT bounded before entry — the regime score and the hard checks are all that
    # stands between them and an open-ended position. Drop them here to go back to defined risk only.
    auto_strategies: list[Strategy] = Field(default_factory=lambda: list(STRATEGY_KEYS))
    expiry_min_hours: float = Field(12.0, ge=1, le=720)
    expiry_max_hours: float = Field(60.0, ge=2, le=2000)
    short_delta: float = Field(0.18, ge=0.05, le=0.45)  # |delta| of sold strikes on OTM structures
    wing_width_pct: float = Field(2.0, ge=0.25, le=15)  # distance of the protective wings, % of spot
    min_score: int = Field(60, ge=0, le=100)  # regime fit a strategy needs to be considered
    # Pacing, towards about one entry a day. While nothing has opened on an underlying today, the
    # score above eases towards this floor as the day runs out: early, hold out for a good fit;
    # late, take the best there is. One entry puts the bar back. 0 switches it off — then `min_score`
    # is the bar all day. The hard checks never ease: a thin premium is refused at any hour.
    min_score_floor: int = Field(0, ge=0, le=100)
    min_pop_pct: float = Field(55.0, ge=0, le=95)  # probability of profit at expiry, from the ATM IV
    min_credit_width_pct: float = Field(10.0, ge=0, le=60)  # credit / widest spread on defined-risk credits
    max_spread_pct: float = Field(30.0, ge=1, le=100)  # each leg's bid/ask spread as % of its mark

    # -- perpetual (directional strategies) --------------------------------------------------
    paper_leverage: float = Field(10.0, ge=1, le=100)
    trade_margin_pct: float = Field(25.0, ge=1, le=100)  # most of the available margin one position may use
    perp_max_hold_hours: float = Field(24.0, ge=1, le=720)

    # -- risk ------------------------------------------------------------------------------
    risk_per_trade_pct: float = Field(2.0, ge=0.1, le=20)  # worst case of one structure, % of equity
    max_lots: int = Field(50, ge=1, le=10_000)
    max_open_structures: int = Field(3, ge=1, le=50)  # per broker
    max_per_underlying: int = Field(1, ge=1, le=10)  # per broker
    daily_loss_limit_pct: float = Field(5.0, ge=0, le=100)  # 0 = off

    # -- exits -----------------------------------------------------------------------------
    credit_take_profit_pct: float = Field(50.0, ge=5, le=100)  # of the maximum profit
    credit_stop_loss_pct: float = Field(100.0, ge=10, le=500)  # of the credit received
    debit_take_profit_pct: float = Field(60.0, ge=5, le=1000)  # of the debit paid
    debit_stop_loss_pct: float = Field(50.0, ge=5, le=100)  # of the debit paid
    exit_before_expiry_minutes: int = Field(60, ge=0, le=1440)

    # -- AI (LiteLLM) ----------------------------------------------------------------------
    ai_enabled: bool = False
    ai_model: str = "mistral/mistral-small-latest"
    ai_fallback_models: list[str] = Field(default_factory=list, max_length=5)
    ai_review_trades: bool = True  # the auto-trader asks the model to approve or pick a candidate
    ai_required: bool = False  # True: no answer from any model means no trade; False: the rules decide
    ai_brief_minutes: int = Field(30, ge=5, le=720)

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
    notify_trades: bool = True
    notify_decisions: bool = False
    notify_risk: bool = True
    daily_report: bool = True
    daily_report_hour: int = Field(21, ge=0, le=23)
    day_timezone: Literal["Asia/Kolkata", "UTC"] = "Asia/Kolkata"

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

    @field_validator("underlyings", "auto_strategies", "directional_strategies")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _consistent(self) -> Settings:
        if not (self.paper_active or self.delta_active):
            raise ValueError("keep at least one broker active")
        if self.expiry_min_hours >= self.expiry_max_hours:
            raise ValueError("expiry_min_hours must be below expiry_max_hours")
        if self.min_score_floor > self.min_score:
            raise ValueError("min_score_floor cannot be above min_score: the bar eases down, never up")
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
        """With Delta active every broker is priced by the exchange Delta trades on."""
        return self.delta_env if self.delta_active else self.market_data

    @property
    def has_delta_credentials(self) -> bool:
        return bool(self.delta_api_key and self.delta_api_secret)

    @property
    def email_recipients(self) -> list[str]:
        return [a.strip() for a in self.email_to.split(",") if a.strip()]

    def auto_on(self, broker: str) -> bool:
        return getattr(self, f"{broker}_active") and getattr(self, f"auto_{broker}")

    def strategy_on(self, name: str, underlying: str) -> bool:
        """Both switches: the strategy is enabled, and this underlying is one it may trade."""
        if name not in self.directional_strategies:
            return False
        only = self.strategy_symbols.get(name) or []
        return not only or underlying in only

    def strategy_underlyings(self, name: str) -> list[str]:
        """The enabled underlyings this strategy would run on — what the Strategies page shows."""
        return [u for u in self.underlyings if self.strategy_on(name, u)]

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


def from_stored(values: dict[str, Any]) -> Settings:
    """Stored values are filtered field by field: one that no longer validates falls back to its default."""
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
