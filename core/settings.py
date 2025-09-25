from pydantic import BaseSettings, Field


class Settings(BaseSettings):
    # Celery connection
    broker_url: str = Field("redis://localhost:6379/0", env="CELERY_BROKER_URL")
    result_backend: str = Field("redis://localhost:6379/1", env="CELERY_RESULT_BACKEND")

    # Celery behavior
    timezone: str = Field("UTC", env="CELERY_TIMEZONE")
    enable_utc: bool = Field(True, env="CELERY_ENABLE_UTC")
    task_ignore_result: bool = Field(False, env="CELERY_TASK_IGNORE_RESULT")
    worker_prefetch_multiplier: int = Field(1, env="CELERY_WORKER_PREFETCH_MULTIPLIER")
    task_acks_late: bool = Field(True, env="CELERY_TASK_ACKS_LATE")
    broker_connection_retry_on_startup: bool = Field(True, env="CELERY_BROKER_RETRY_ON_STARTUP")

    # App defaults
    symbols: str = Field("AAPL,GOOG,MSFT", env="APP_SYMBOLS")  # comma-separated
    strategies: str = Field(
        "strategies.ema_strategy.EMAStrategy,strategies.rsi_strategy.RSIStrategy,strategies.custom_strategy.CustomStrategy",
        env="APP_STRATEGIES",
    )

    # Scheduling
    schedule_seconds: int = Field(30, env="APP_SCHEDULE_SECONDS")

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()

def get_symbols() -> list[str]:
    return [s.strip() for s in settings.symbols.split(",") if s.strip()]

def get_strategies() -> list[str]:
    return [s.strip() for s in settings.strategies.split(",") if s.strip()]


