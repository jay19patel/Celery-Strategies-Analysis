"""Celery tasks and worker application."""

from app.infrastructure.celery.worker import celery_app

__all__ = ["celery_app"]
