"""API dependency injection — repositories, services, Redis, and auth guards."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Cookie, Depends, Header
from sqlmodel import Session

from app.application.auth_service import AuthService
from app.application.journal_service import JournalService
from app.application.market_service import MarketService
from app.application.paper_service import PaperService
from app.application.settings_service import SettingsService
from app.application.system_service import SystemService
from app.application.trading_service import TradingService
from app.config import Settings
from app.domain.exceptions import AuthenticationError
from app.infrastructure.database.repositories.bar_repo import BarRepository
from app.infrastructure.database.repositories.event_repo import EventRepository
from app.infrastructure.database.repositories.order_repo import OrderRepository
from app.infrastructure.database.repositories.paper_repo import PaperRepository
from app.infrastructure.database.repositories.position_repo import PositionRepository
from app.infrastructure.database.repositories.settings_repo import SettingsRepository
from app.infrastructure.database.session import get_session
from app.infrastructure.redis.cache import RedisCache
from app.infrastructure.redis.client import get_redis
from app.infrastructure.redis.pubsub import EventPublisher

logger = logging.getLogger(__name__)

# Cached app settings singleton
_settings: Settings | None = None


def get_app_settings() -> Settings:
    """Return application configuration settings."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

def get_order_repo(session: Annotated[Session, Depends(get_session)]) -> OrderRepository:
    """Yield an OrderRepository instance bound to the request session."""
    return OrderRepository(session)


def get_position_repo(session: Annotated[Session, Depends(get_session)]) -> PositionRepository:
    """Yield a PositionRepository instance bound to the request session."""
    return PositionRepository(session)


def get_bar_repo(session: Annotated[Session, Depends(get_session)]) -> BarRepository:
    """Yield a BarRepository instance bound to the request session."""
    return BarRepository(session)


def get_paper_repo(session: Annotated[Session, Depends(get_session)]) -> PaperRepository:
    """Yield a PaperRepository instance bound to the request session."""
    return PaperRepository(session)


def get_settings_repo(session: Annotated[Session, Depends(get_session)]) -> SettingsRepository:
    """Yield a SettingsRepository instance bound to the request session."""
    return SettingsRepository(session)


def get_event_repo(session: Annotated[Session, Depends(get_session)]) -> EventRepository:
    """Yield an EventRepository instance bound to the request session."""
    return EventRepository(session)


# ---------------------------------------------------------------------------
# Infrastructure (Redis & PubSub)
# ---------------------------------------------------------------------------

def get_cache() -> RedisCache:
    """Yield a RedisCache instance using the shared connection."""
    return RedisCache(get_redis())


def get_publisher() -> EventPublisher:
    """Yield an EventPublisher instance using the shared connection."""
    return EventPublisher(get_redis())


# ---------------------------------------------------------------------------
# Application Services
# ---------------------------------------------------------------------------

def get_auth_service(
    cache: Annotated[RedisCache, Depends(get_cache)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AuthService:
    """Yield configured AuthService instance."""
    return AuthService(
        auth_pin=settings.auth_pin,
        auth_secret=settings.auth_secret,
        cache=cache,
    )


def get_trading_service(
    order_repo: Annotated[OrderRepository, Depends(get_order_repo)],
    position_repo: Annotated[PositionRepository, Depends(get_position_repo)],
    publisher: Annotated[EventPublisher, Depends(get_publisher)],
) -> TradingService:
    """Yield configured TradingService instance."""
    return TradingService(
        order_repo=order_repo,
        position_repo=position_repo,
        publisher=publisher,
    )


def get_market_service(
    bar_repo: Annotated[BarRepository, Depends(get_bar_repo)],
) -> MarketService:
    """Yield configured MarketService instance."""
    return MarketService(bar_repo=bar_repo)


def get_paper_service(
    paper_repo: Annotated[PaperRepository, Depends(get_paper_repo)],
) -> PaperService:
    """Yield configured PaperService instance."""
    return PaperService(repo=paper_repo)


def get_journal_service(
    paper_service: Annotated[PaperService, Depends(get_paper_service)],
) -> JournalService:
    """Yield configured JournalService instance."""
    return JournalService(paper_service=paper_service)


def get_settings_service(
    settings_repo: Annotated[SettingsRepository, Depends(get_settings_repo)],
    publisher: Annotated[EventPublisher, Depends(get_publisher)],
) -> SettingsService:
    """Yield configured SettingsService instance."""
    return SettingsService(repo=settings_repo, publisher=publisher)


def get_system_service(
    event_repo: Annotated[EventRepository, Depends(get_event_repo)],
    publisher: Annotated[EventPublisher, Depends(get_publisher)],
) -> SystemService:
    """Yield configured SystemService instance."""
    return SystemService(event_repo=event_repo, publisher=publisher)


# ---------------------------------------------------------------------------
# Authentication Guard
# ---------------------------------------------------------------------------

async def require_auth(
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    tb_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_api_token: Annotated[str | None, Header()] = None,
) -> bool:
    """Validate request authentication via cookie, Bearer token, or API token.

    Raises:
        AuthenticationError: If no valid credential is provided.
    """
    # 1. Check API token header (machine-to-machine / internal jobs)
    if x_api_token and settings.api_token and x_api_token == settings.api_token:
        return True

    # 2. Check Bearer token header
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif tb_session:
        token = tb_session

    if not token:
        raise AuthenticationError("Authentication required — no credentials provided")

    is_valid = await auth_service.validate_token(token)
    if not is_valid:
        raise AuthenticationError("Session expired or invalid")

    return True
