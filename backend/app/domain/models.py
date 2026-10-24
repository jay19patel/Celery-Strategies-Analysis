"""SQLModel table definitions — the single source of truth for the database schema.

Every table from the original raw-SQL schema is now a typed SQLModel class.
Composite primary keys use ``sa_column`` because SQLModel does not support
multi-column PKs natively.
"""

from __future__ import annotations

import time

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


# ---------------------------------------------------------------------------
# Settings & Toggles
# ---------------------------------------------------------------------------

class Setting(SQLModel, table=True):
    """Runtime configuration stored as key-value pairs."""

    __tablename__ = "settings"

    key: str = Field(primary_key=True, max_length=128)
    value: str


class Toggle(SQLModel, table=True):
    """Feature flags — strategies, brokers, guards — on or off."""

    __tablename__ = "toggles"

    key: str = Field(primary_key=True, max_length=128)
    enabled: bool


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

class Order(SQLModel, table=True):
    """Every order the system has ever placed, paper or live."""

    __tablename__ = "orders"

    client_order_id: str = Field(primary_key=True, max_length=64)
    ts: float = Field(default_factory=time.time)
    broker: str = Field(default="delta", max_length=16)
    strategy: str = Field(max_length=64)
    symbol: str = Field(max_length=32)
    side: str = Field(max_length=8)
    size: int
    price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    status: str = Field(default="pending", max_length=16)
    order_id: str | None = Field(default=None, max_length=64)
    error: str | None = None


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class Event(SQLModel, table=True):
    """Full event log — every tick, signal, trade, error that passes through the bus."""

    __tablename__ = "events"

    id: int | None = Field(default=None, primary_key=True)
    ts: float = Field(default_factory=time.time, index=True)
    type: str = Field(max_length=64, index=True)
    data: str  # JSON-encoded event payload


# ---------------------------------------------------------------------------
# Paper Trading
# ---------------------------------------------------------------------------

class PaperAccount(SQLModel, table=True):
    """The simulated account — exactly one row, id=1."""

    __tablename__ = "paper_account"

    id: int = Field(default=1, primary_key=True)
    balance: float
    starting_balance: float
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    created_at: float = Field(default_factory=time.time)


class PaperPosition(SQLModel, table=True):
    """Open simulated positions, keyed by symbol."""

    __tablename__ = "paper_positions"
    __table_args__ = (
        sa.UniqueConstraint("client_order_id"),
    )

    symbol: str = Field(primary_key=True, max_length=32)
    client_order_id: str = Field(max_length=64)
    strategy: str = Field(max_length=64)
    side: str = Field(max_length=8)
    size: float
    contract_value: float
    entry_price: float
    leverage: float
    margin: float
    entry_fee: float
    stop_loss: float | None = None
    take_profit: float | None = None
    liquidation_price: float | None = None
    opened_at: float = Field(default_factory=time.time)
    mae_pct: float = 0.0
    mfe_pct: float = 0.0


class PaperTrade(SQLModel, table=True):
    """Closed simulated trades — the journal's raw material."""

    __tablename__ = "paper_trades"
    __table_args__ = (
        sa.UniqueConstraint("client_order_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    client_order_id: str = Field(max_length=64)
    strategy: str = Field(max_length=64)
    symbol: str = Field(max_length=32)
    side: str = Field(max_length=8)
    size: float
    contract_value: float
    entry_price: float
    exit_price: float
    leverage: float
    margin: float
    gross_pnl: float
    fees: float
    pnl: float
    reason: str = Field(max_length=32)
    opened_at: float
    closed_at: float = Field(default_factory=time.time)
    mae_pct: float = 0.0
    mfe_pct: float = 0.0


# ---------------------------------------------------------------------------
# Position Controls & Risk
# ---------------------------------------------------------------------------

class PositionControl(SQLModel, table=True):
    """Per-position trailing-stop configuration."""

    __tablename__ = "position_controls"

    broker: str = Field(max_length=16, primary_key=True)
    symbol: str = Field(max_length=32, primary_key=True)
    position: str = Field(max_length=64)
    trailing: bool
    max_steps: int
    steps: int = 0
    base_target: float = 0.0
    updated_at: float = Field(default_factory=time.time)


class DailyRisk(SQLModel, table=True):
    """Daily risk circuit-breaker — halts trading when the loss limit is hit."""

    __tablename__ = "daily_risk"

    broker: str = Field(max_length=16, primary_key=True)
    day: str = Field(max_length=10, primary_key=True)
    start_equity: float
    halted_at: float | None = None
    halt_reason: str | None = None


# ---------------------------------------------------------------------------
# Market Data — Bars
# ---------------------------------------------------------------------------

class Bar(SQLModel, table=True):
    """OHLCV candle data, kept permanently. One row per symbol/interval/time."""

    __tablename__ = "bars"

    symbol: str = Field(max_length=32, primary_key=True)
    interval: str = Field(max_length=8, primary_key=True)
    time: int = Field(primary_key=True)
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float | None = None
    funding: float | None = None


# ---------------------------------------------------------------------------
# Options History
# ---------------------------------------------------------------------------

class OptionsHistory(SQLModel, table=True):
    """Options metrics over time — IV, skew, PCR, OI, max pain."""

    __tablename__ = "options_history"

    underlying: str = Field(max_length=32, primary_key=True)
    ts: float = Field(primary_key=True)
    keep: int = 0
    spot: float | None = None
    atm_iv: float | None = None
    near_atm_iv: float | None = None
    skew_25d: float | None = None
    pcr_oi: float | None = None
    pcr_volume: float | None = None
    call_oi: float | None = None
    put_oi: float | None = None
    oi_usd: float | None = None
    turnover_usd: float | None = None
    max_pain: float | None = None
    implied_move_pct: float | None = None


# ---------------------------------------------------------------------------
# Market Snapshots (Analyst Readings)
# ---------------------------------------------------------------------------

class MarketSnapshot(SQLModel, table=True):
    """Analyst's reading of one bar — typed columns for fast aggregation."""

    __tablename__ = "market_snapshots"

    symbol: str = Field(max_length=32, primary_key=True)
    bar_time: int = Field(primary_key=True)
    ts: float = Field(primary_key=True)
    keep: int = 0
    price: float | None = None
    change_1h_pct: float | None = None
    change_4h_pct: float | None = None
    change_24h_pct: float | None = None
    rv_24h: float | None = None
    rv_7d: float | None = None
    atr_pct: float | None = None
    range_pos_24h: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    volume_z_1h: float | None = None
    oi_change_1h_pct: float | None = None
    oi_change_24h_pct: float | None = None
    funding_avg_24h_pct: float | None = None
    basis_pct: float | None = None
    atm_iv: float | None = None
    iv_rv_ratio: float | None = None
    skew_25d: float | None = None
    pcr_oi: float | None = None
    pcr_volume: float | None = None
    implied_move_pct: float | None = None
    max_pain_gap_pct: float | None = None
    call_wall_gap_pct: float | None = None
    put_wall_gap_pct: float | None = None
    term_slope: float | None = None
    insight_keys: str = ""
    lean: float | None = None


# ---------------------------------------------------------------------------
# Predictions (Verdict Scoring)
# ---------------------------------------------------------------------------

class Prediction(SQLModel, table=True):
    """Directional claims made by verdict cards, scored against the market."""

    __tablename__ = "predictions"

    symbol: str = Field(max_length=32, primary_key=True)
    source: str = Field(max_length=32, primary_key=True)
    bar_time: int = Field(primary_key=True)
    ts: float
    horizon_hours: float
    stance: str = Field(max_length=16)
    measure: str = Field(max_length=16)
    label: str
    confidence: float
    price: float
    numbers: str  # JSON
    settled_at: float | None = None
    actual_return: float | None = None
    actual_vol: float | None = None
    prior_vol: float | None = None
    actual_excursion: float | None = None
    regime: str | None = None
    exit_reason: str | None = None
    r_multiple: float | None = None
    correct: int | None = None
    baseline_correct: int | None = None


# ---------------------------------------------------------------------------
# AI Reports
# ---------------------------------------------------------------------------

class AiReport(SQLModel, table=True):
    """AI-generated market summaries from Mistral."""

    __tablename__ = "ai_reports"

    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(max_length=32, index=True)
    ts: float
    bar_time: int
    ok: bool
    model: str = Field(max_length=64)
    report: str | None = None  # JSON when ok=True
    error: str = ""
    raw: str = ""
    ms: int = 0
    tokens: int = 0


# ---------------------------------------------------------------------------
# System Metrics
# ---------------------------------------------------------------------------

class SystemMetric(SQLModel, table=True):
    """Process-level metrics sampled every few seconds."""

    __tablename__ = "system_metrics"

    id: int | None = Field(default=None, primary_key=True)
    ts: float = Field(default_factory=time.time, index=True)
    cpu_pct: float
    memory_mb: float
    loop_lag_ms: float | None = None
    connections: int = 0
    events_per_sec: float = 0.0
