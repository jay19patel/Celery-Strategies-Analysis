import importlib
import inspect
import pkgutil
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.base_strategy import BaseStrategy


class Settings(BaseSettings):
    # Redis URLs
    redis_broker_url: str = Field("redis://localhost:6379/0")
    redis_result_url: str = Field("redis://localhost:6379/1")
    redis_pubsub_url: str = Field("redis://localhost:6379/2")

    # MongoDB Atlas connection
    mongodb_url: str = Field("mongodb://localhost:27017/stockanalysis")

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
        "app.strategies.ema_strategy.EMAStrategy,"
        "app.strategies.pdhl_strategy.PDHLStrategy"
    )  # use "*" to auto-load every strategy module in app/strategies

    # Scheduling
    schedule_seconds: int = Field(60)  # in seconds

    # PaperBroker (simple per-strategy account) risk protection
    broker_stop_loss_pct: float = Field(1.0)
    broker_take_profit_pct: float = Field(2.0)
    broker_leverage: float = Field(20.0)

    # Portfolio paper-trading
    portfolio_symbol: str = Field("ETHUSD")
    portfolio_interval: str = Field("1h")
    portfolio_schedule_seconds: int = Field(1200)  # 20 minutes

    # Portfolio risk parameters (M3: moved from hardcoded constants)
    portfolio_initial_capital: float = Field(100.0)
    portfolio_risk_per_trade_pct: float = Field(2.0)
    portfolio_stop_loss_pct: float = Field(1.0)
    portfolio_take_profit_pct: float = Field(3.0)
    portfolio_max_hold_bars: int = Field(20)
    portfolio_fee_pct: float = Field(0.05)
    portfolio_max_leverage: float = Field(20.0)
    portfolio_max_concurrent_trades: int = Field(5)
    portfolio_risk_cap_pct: float = Field(10.0)
    portfolio_drawdown_trigger_pct: float = Field(10.0)
    portfolio_drawdown_recovery_pct: float = Field(5.0)
    portfolio_throttled_risk_pct: float = Field(1.0)

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

    @property
    def mongodb_uri(self) -> str:
        return self.mongodb_url


settings = Settings()

def get_symbols() -> list[str]:
    return [s.strip() for s in settings.symbols.split(",") if s.strip()]

def _strategies_package_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "strategies"


@lru_cache()
def _discover_strategy_class_paths() -> List[str]:
    """
    Import every module inside app.strategies and collect concrete BaseStrategy subclasses.
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
    """
    Returns explicit strategies listed in settings. If "*" is present,
    auto-discovers all strategies defined in app/strategies.
    """
    declared = [s.strip() for s in settings.strategies.split(",") if s.strip()]
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
