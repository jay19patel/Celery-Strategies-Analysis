from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Redis URLs
    redis_broker_url: str = Field("redis://localhost:6379/0")
    redis_result_url: str = Field("redis://localhost:6379/1")
    redis_pubsub_url: str = Field("redis://localhost:6379/2")

    # MongoDB Atlas connection
    mongodb_url: str = Field("mongodb://localhost:27017/stockanalysis")

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
        "app.strategies.ema_strategy.EMAStrategy," \
        "app.strategies.rsi_strategy.RSIStrategy," \
        "app.strategies.bollinger_bands_strategy.BollingerBandsStrategy," \
        "app.strategies.macd_strategy.MACDStrategy," \
        "app.strategies.volume_breakout_strategy.VolumeBreakoutStrategy"
    )

    # Scheduling
    schedule_seconds: int = Field(60) # in seconds

    # Redis pub/sub channels
    pubsub_channel_strategy: str = Field("stockanalysis:strategy_result")
    pubsub_channel_batch: str = Field("stockanalysis:batch_complete")

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

def get_strategies() -> list[str]:
    return [s.strip() for s in settings.strategies.split(",") if s.strip()]


