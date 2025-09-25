from pydantic import BaseSettings, Field


class Settings(BaseSettings):
    # Celery connection
    broker_url: str = Field("redis://localhost:6379/0")
    result_backend: str = Field("redis://localhost:6379/1")

    # Celery behavior
    timezone: str = Field("UTC")
    enable_utc: bool = Field(True)
    task_ignore_result: bool = Field(False)
    worker_prefetch_multiplier: int = Field(1)
    task_acks_late: bool = Field(True)
    broker_connection_retry_on_startup: bool = Field(True)

    # App defaults
    symbols: str = Field("BTC-USD,ETH-USD,SOL-USD")  # comma-separated
    strategies: str = Field(
        "strategies.ema_strategy.EMAStrategy," \
        "strategies.rsi_strategy.RSIStrategy," \
        "strategies.bollinger_bands_strategy.BollingerBandsStrategy," \
        "strategies.macd_strategy.MACDStrategy," \
        "strategies.volume_breakout_strategy.VolumeBreakoutStrategy"
    )

    # Scheduling
    schedule_seconds: int = Field(60*10) # in seconds

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()

def get_symbols() -> list[str]:
    return [s.strip() for s in settings.symbols.split(",") if s.strip()]

def get_strategies() -> list[str]:
    return [s.strip() for s in settings.strategies.split(",") if s.strip()]


