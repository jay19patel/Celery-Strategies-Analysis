from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Redis connection (for Celery broker, result backend, and pub/sub)
    redis_host: str = Field("localhost")
    redis_port: int = Field(6379)
    redis_broker_db: int = Field(0)
    redis_result_db: int = Field(1)
    redis_pubsub_db: int = Field(2)

    # MongoDB connection
    mongodb_host: str = Field("localhost")
    mongodb_port: int = Field(27017)
    mongodb_database: str = Field("stockanalysis")
    mongodb_username: str = Field("")
    mongodb_password: str = Field("")

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
    schedule_seconds: int = Field(60) # in seconds

    # Redis pub/sub channels
    pubsub_channel_strategy: str = Field("stockanalysis:strategy_result")
    pubsub_channel_batch: str = Field("stockanalysis:batch_complete")

    model_config = {
        "env_file": ".env",
        "case_sensitive": False,
        "extra": "ignore"
    }

    @property
    def broker_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_broker_db}"

    @property
    def result_backend(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_result_db}"

    @property
    def mongodb_uri(self) -> str:
        if self.mongodb_username and self.mongodb_password:
            return f"mongodb://{self.mongodb_username}:{self.mongodb_password}@{self.mongodb_host}:{self.mongodb_port}/{self.mongodb_database}"
        return f"mongodb://{self.mongodb_host}:{self.mongodb_port}/{self.mongodb_database}"


settings = Settings()

def get_symbols() -> list[str]:
    return [s.strip() for s in settings.symbols.split(",") if s.strip()]

def get_strategies() -> list[str]:
    return [s.strip() for s in settings.strategies.split(",") if s.strip()]


