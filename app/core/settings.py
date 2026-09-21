import importlib
import inspect
import pkgutil
from functools import lru_cache
from pathlib import Path
from typing import Any, List

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.base_strategy import BaseStrategy


class Settings(BaseSettings):
    # Redis URLs
    redis_broker_url: str = Field("redis://localhost:6379/0")
    redis_result_url: str = Field("redis://localhost:6379/1")
    redis_pubsub_url: str = Field("redis://localhost:6379/2")

    # SQLite Database Configuration
    sqlite_db_path: str = Field("data/stockanalysis.db")

    # Execution Mode: "PAPER" or "LIVE"
    execution_mode: str = Field("PAPER")
    live_trading_armed: bool = Field(False)  # Explicit confirmation required for real orders

    # Delta Exchange Broker Integration (Trade-Buddy-Broker)
    delta_base_url: str = Field("https://api.india.delta.exchange")
    delta_websocket_url: str = Field("wss://socket.india.delta.exchange")
    delta_api_key: str = Field("")
    delta_api_secret: str = Field("")
    delta_client_id: int = Field(0)
    trade_capital_pct: float = Field(30.0)   # 30% of account capital per trade
    risk_ratio: float = Field(0.01)         # 1.0% stop-loss threshold
    reward_ratio: float = Field(0.015)      # 1.5% take-profit threshold
    exit_on_signal: bool = Field(True)      # Close position on opposite signal

    # Celery behavior
    timezone: str = Field("Asia/Kolkata")
    enable_utc: bool = Field(False)
    task_ignore_result: bool = Field(True)
    result_expires: int = Field(900)
    worker_prefetch_multiplier: int = Field(1)
    task_acks_late: bool = Field(True)
    broker_connection_retry_on_startup: bool = Field(True)

    # App defaults
    symbols: str = Field("BTC-USD,ETH-USD,SOL-USD")  # comma-separated
    strategies: str = Field(
        "*"
    )  # use "*" to auto-load every strategy module in app/strategies

    # Scheduling
    schedule_seconds: int = Field(60)  # in seconds

    # ── Global Unified Risk & Trading Settings ────────────────────────────────
    leverage: float = Field(20.0)                 # Default 20x Leverage
    stop_loss_pct: float = Field(0.5)            # Default 0.5% Stop Loss
    take_profit_pct: float = Field(1.0)          # Default 1.0% Take Profit
    capital_allocation_pct: float = Field(50.0)  # Default 50% Capital Margin per trade
    max_hold_hours: float = Field(72.0)          # Default 72 hours (3 Days) hold limit

    # Portfolio paper-trading specific
    portfolio_symbol: str = Field("ETHUSD")
    portfolio_interval: str = Field("15m")
    portfolio_schedule_seconds: int = Field(1200)  # 20 minutes
    portfolio_initial_capital: float = Field(100.0)
    portfolio_risk_per_trade_pct: float = Field(2.0)
    portfolio_fee_pct: float = Field(0.05)
    portfolio_max_concurrent_trades: int = Field(5)
    portfolio_risk_cap_pct: float = Field(10.0)
    portfolio_drawdown_trigger_pct: float = Field(10.0)
    portfolio_drawdown_recovery_pct: float = Field(5.0)
    portfolio_throttled_risk_pct: float = Field(1.0)

    # ── Backward Compatibility Properties ─────────────────────────────────────
    @property
    def broker_leverage(self) -> float:
        return self.leverage

    @property
    def portfolio_max_leverage(self) -> float:
        return self.leverage

    @property
    def broker_stop_loss_pct(self) -> float:
        return self.stop_loss_pct

    @property
    def portfolio_stop_loss_pct(self) -> float:
        return self.stop_loss_pct

    @property
    def broker_take_profit_pct(self) -> float:
        return self.take_profit_pct

    @property
    def portfolio_take_profit_pct(self) -> float:
        return self.take_profit_pct

    @property
    def broker_capital_allocation_pct(self) -> float:
        return self.capital_allocation_pct

    @property
    def broker_max_hold_hours(self) -> float:
        return self.max_hold_hours

    @property
    def portfolio_max_hold_bars(self) -> int:
        return int(self.max_hold_hours)

    # Redis pub/sub channels
    pubsub_channel_batch: str = Field("stockanalysis:batch_complete")
    pubsub_channel_strategy: str = Field("stockanalysis:strategy_result")

    model_config = {
        "case_sensitive": False,
        "extra": "ignore"
    }

    @property
    def broker_url(self) -> str:
        return self.redis_broker_url

    @property
    def result_backend(self) -> str:
        return self.redis_result_url


settings = Settings()


def get_pipeline_settings_override() -> dict[str, Any]:
    """Read dynamic pipeline settings from SQLite system_config table.

    Returns:
        Dictionary of stored pipeline overrides or empty dict.
    """
    try:
        import json
        from app.database.sqlite_db import get_sqlite_db

        db = get_sqlite_db()
        row = db.execute_one("SELECT value FROM system_config WHERE key = 'pipeline_settings';")
        if row and row.get("value"):
            return json.loads(row["value"])
    except Exception:
        pass
    return {}


def get_symbols_raw() -> str:
    """Return raw comma-separated symbols string, prioritizing SQLite config.

    Returns:
        String of comma-separated symbols.
    """
    override = get_pipeline_settings_override()
    if "symbols" in override and override["symbols"]:
        return str(override["symbols"]).strip()
    return settings.symbols


def get_symbols() -> list[str]:
    """Return parsed list of active trading symbols.

    Returns:
        List of symbol strings (e.g. ['BTC-USD', 'ETH-USD', 'SOL-USD']).
    """
    raw = get_symbols_raw()
    return [s.strip() for s in raw.split(",") if s.strip()]


def get_strategies_raw() -> str:
    """Return raw comma-separated strategies configuration string.

    Returns:
        String of comma-separated strategy class names or '*'.
    """
    override = get_pipeline_settings_override()
    if "strategies" in override and override["strategies"]:
        return str(override["strategies"]).strip()
    return settings.strategies


def get_schedule_seconds() -> int:
    """Return batch pipeline execution interval in seconds.

    Returns:
        Integer seconds interval (minimum 10s).
    """
    override = get_pipeline_settings_override()
    if "schedule_seconds" in override and override["schedule_seconds"] is not None:
        try:
            return max(10, int(override["schedule_seconds"]))
        except (ValueError, TypeError):
            pass
    return max(10, int(settings.schedule_seconds))


def _strategies_package_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "strategies"


@lru_cache()
def _discover_strategy_class_paths() -> List[str]:
    """Import every module inside app.strategies and collect concrete BaseStrategy subclasses.

    Cached to avoid hitting the filesystem repeatedly within the same process.
    """
    strategy_dir = _strategies_package_dir()
    if not strategy_dir.exists():
        return []

    discovered: List[str] = []
    package_prefix = "app.strategies"

    for module_info in pkgutil.iter_modules([str(strategy_dir)]):
        if module_info.ispkg or module_info.name.startswith("_"):
            continue

        module_name = f"{package_prefix}.{module_info.name}"
        module = importlib.import_module(module_name)

        for attr_name, attr_value in inspect.getmembers(module, inspect.isclass):
            if attr_value is BaseStrategy:
                continue
            if not issubclass(attr_value, BaseStrategy):
                continue
            if attr_value.__module__ != module.__name__:
                continue

            discovered.append(f"{module_name}.{attr_name}")

    return sorted(discovered)


def get_strategies() -> list[str]:
    """Return explicit strategies listed in settings or system_config.

    If "*" is present, auto-discovers all strategies defined in app/strategies.
    """
    raw = get_strategies_raw()
    declared = [s.strip() for s in raw.split(",") if s.strip()]
    include_discovered = False
    strategies: List[str] = []

    for entry in declared:
        if entry == "*":
            include_discovered = True
            continue
        strategies.append(entry)

    if include_discovered:
        strategies.extend(_discover_strategy_class_paths())

    # Preserve order while removing duplicates
    deduped: List[str] = []
    seen = set()
    for path in strategies:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)

    return deduped
