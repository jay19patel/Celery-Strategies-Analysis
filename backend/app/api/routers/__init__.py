"""API routers package."""

from app.api.routers.auth import router as auth_router
from app.api.routers.journal import router as journal_router
from app.api.routers.market import router as market_router
from app.api.routers.paper import router as paper_router
from app.api.routers.settings import router as settings_router
from app.api.routers.system import router as system_router
from app.api.routers.trading import router as trading_router

__all__ = [
    "auth_router",
    "journal_router",
    "market_router",
    "paper_router",
    "settings_router",
    "system_router",
    "trading_router",
]
