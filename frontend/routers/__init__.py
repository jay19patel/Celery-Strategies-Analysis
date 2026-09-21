"""FastAPI routers for dashboard API endpoints.

Thin routing layer that delegates all business logic to app.services.
"""

from frontend.routers.analytics_router import router as analytics_router
from frontend.routers.broker_router import router as broker_router
from frontend.routers.log_router import router as log_router
from frontend.routers.strategy_router import router as strategy_router
from frontend.routers.system_router import router as system_router

__all__ = [
    "analytics_router",
    "broker_router",
    "log_router",
    "strategy_router",
    "system_router",
]
