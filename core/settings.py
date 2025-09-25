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

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()


