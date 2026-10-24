"""Database tables. SQLite through SQLModel; the engine process is the only writer."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class KV(SQLModel, table=True):
    """Runtime settings, toggles and secrets the process generates for itself (the session key)."""

    key: str = Field(primary_key=True)
    value: Any = Field(sa_column=Column(JSON))


class PaperAccount(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    cash: float  # premium received and paid, fees taken: the wallet before open positions are marked
    starting_balance: float
    fees_paid: float = 0.0
    created_at: float


class Structure(SQLModel, table=True):
    """One options trade: every leg opened together and closed together.

    Cash is signed from the account's side: `entry_cash` > 0 is a credit received, < 0 a debit paid.
    Realised P&L is entry_cash + exit_cash - fees."""

    id: str = Field(primary_key=True)
    broker: str = Field(index=True)
    underlying: str
    strategy: str
    source: str  # "auto" | "manual"
    status: str = Field(index=True)  # opening | open | closing | closed | failed | error
    expiry: float
    qty: int  # lots: every leg's size is its ratio x qty
    entry_spot: float | None = None
    exit_spot: float | None = None
    entry_cash: float = 0.0
    exit_cash: float = 0.0
    fees: float = 0.0
    pnl: float | None = None  # realised, once closed
    max_profit: float | None = None  # None = unlimited
    max_loss: float | None = None  # None = unlimited; a positive number of dollars
    margin: float = 0.0
    take_profit_pct: float
    stop_loss_pct: float
    mark_pnl: float | None = None  # what closing now would book, refreshed by the guard
    marked_at: float | None = None
    opened_at: float
    closed_at: float | None = Field(default=None, index=True)
    close_reason: str = ""
    error: str = ""
    decision_id: int | None = None
    request_id: str = ""


class Leg(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    structure_id: str = Field(index=True, foreign_key="structure.id")
    idx: int
    symbol: str
    kind: str  # call | put
    strike: float
    side: str  # buy | sell (the opening side)
    size: int  # contracts
    contract_value: float
    entry_price: float | None = None
    exit_price: float | None = None
    entry_fee: float = 0.0
    exit_fee: float = 0.0


class OrderRecord(SQLModel, table=True):
    """Every order sent to a broker, keyed by the id the broker is idempotent on."""

    client_order_id: str = Field(primary_key=True)
    structure_id: str = Field(index=True)
    broker: str
    purpose: str  # open | close | unwind
    symbol: str
    side: str
    size: int
    limit_price: float
    status: str  # pending | filled | failed | unknown
    fill_price: float | None = None
    filled_size: int = 0
    fee: float = 0.0
    exchange_id: str = ""
    error: str = ""
    created_at: float = Field(index=True)
    updated_at: float


class Decision(SQLModel, table=True):
    """One auto-trader cycle for one underlying: what it saw, what it considered and what it did."""

    id: int | None = Field(default=None, primary_key=True)
    ts: float = Field(index=True)
    underlying: str
    action: str  # open | skip | error
    strategy: str = ""
    reason: str = ""
    expiry: float | None = None
    spot: float | None = None
    regime: Any = Field(default=None, sa_column=Column(JSON))
    candidates: Any = Field(default=None, sa_column=Column(JSON))
    ai: Any = Field(default=None, sa_column=Column(JSON))
    orders: Any = Field(default=None, sa_column=Column(JSON))  # per broker: structure id or refusal


class EventLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ts: float = Field(index=True)
    type: str = Field(index=True)
    level: str  # info | warning | error
    message: str
    data: Any = Field(default=None, sa_column=Column(JSON))


class Bar(SQLModel, table=True):
    symbol: str = Field(primary_key=True)
    interval: str = Field(primary_key=True)
    time: int = Field(primary_key=True)  # bar open, unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class AiBrief(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ts: float = Field(index=True)
    underlying: str
    model: str = ""
    content: Any = Field(default=None, sa_column=Column(JSON))
    error: str = ""
    latency_ms: int | None = None


class DayRisk(SQLModel, table=True):
    """Each broker's trading day: the equity it started at and, once the loss limit fires, why it stopped."""

    broker: str = Field(primary_key=True)
    day: str = Field(primary_key=True)
    start_equity: float
    halted: str = ""
