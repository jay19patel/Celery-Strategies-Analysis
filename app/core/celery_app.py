from celery import Celery
from celery.schedules import schedule
from app.core.settings import settings


celery_app = Celery(
    "stockanalysis",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.core.tasks"],
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
    result_expires=settings.result_expires,
)

# Ensure tasks are registered when worker starts
try:
    from core import tasks  # noqa: F401
    celery_app.autodiscover_tasks(["core"])  # explicit for clarity
except Exception:
    # Import errors should not crash configuration; worker will fail loudly if tasks missing
    pass

# Periodic schedule: run batch every N seconds
celery_app.conf.beat_schedule = {
    "run-batch-periodically": {
        "task": "run_all_batch_task",
        "schedule": schedule(settings.schedule_seconds),
    }
}


