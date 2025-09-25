from celery import Celery
import os


def _get_broker_url() -> str:
    return os.getenv("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")


def _get_backend_url() -> str:
    return os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")


celery_app = Celery(
    "stockanalysis",
    broker=_get_broker_url(),
    backend=_get_backend_url(),
    include=["core.tasks"],
)

# Keep tasks discovery explicit to avoid import-time side effects
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_ignore_result=False,
    worker_prefetch_multiplier=1,  # fair scheduling
    task_acks_late=True,  # in case of worker crash, requeue
    broker_connection_retry_on_startup=True,
)

# Ensure tasks are registered when worker starts
try:
    from core import tasks  # noqa: F401
    celery_app.autodiscover_tasks(["core"])  # explicit for clarity
except Exception:
    # Import errors should not crash configuration; worker will fail loudly if tasks missing
    pass


