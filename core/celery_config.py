"""
Celery configuration for stock analysis strategy execution.
Handles parallel execution of multiple symbols and strategies using Redis.
"""

import os
from celery import Celery

# Redis connection URL from environment or default
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Create Celery app instance
celery_app = Celery(
    'stockanalysis',
    broker=REDIS_URL,        # Task queue broker (Redis)
    backend=REDIS_URL,       # Result storage backend (Redis)
    include=['core.celery_tasks']  # Auto-discover tasks in celery_tasks module
)

# Celery configuration optimized for trading strategies
celery_app.conf.update(
    # Worker settings for high-performance parallel execution
    worker_concurrency=20,              # 20 parallel workers (optimal for 100 tasks)
    worker_prefetch_multiplier=1,       # Fair task distribution

    # Task routing
    task_routes={
        'core.celery_tasks.execute_symbol_strategy': {'queue': 'strategy_queue'},
        'core.celery_tasks.execute_symbol_batch': {'queue': 'batch_queue'},
    },

    # Performance optimization
    result_expires=3600,                # Results expire after 1 hour
    task_serializer='json',             # Fast JSON serialization
    result_serializer='json',
    accept_content=['json'],

    # Error handling
    task_reject_on_worker_lost=True,    # Retry if worker crashes
    task_acks_late=True,                # Acknowledge after completion

    # Timezone
    timezone='UTC',
    enable_utc=True,
)