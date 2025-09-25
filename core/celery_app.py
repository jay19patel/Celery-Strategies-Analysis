from celery import Celery
from core.settings import settings


celery_app = Celery(
    "stockanalysis",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["core.tasks"],
)

# Keep tasks discovery explicit to avoid import-time side effects
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone=settings.timezone,
    enable_utc=settings.enable_utc,
    task_ignore_result=settings.task_ignore_result,
    worker_prefetch_multiplier=settings.worker_prefetch_multiplier,  # fair scheduling
    task_acks_late=settings.task_acks_late,  # in case of worker crash, requeue
    broker_connection_retry_on_startup=settings.broker_connection_retry_on_startup,
)

# Ensure tasks are registered when worker starts
try:
    from core import tasks  # noqa: F401
    celery_app.autodiscover_tasks(["core"])  # explicit for clarity
except Exception:
    # Import errors should not crash configuration; worker will fail loudly if tasks missing
    pass


