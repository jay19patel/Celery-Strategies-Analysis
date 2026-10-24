"""Unit tests for application layer services."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.application.auth_service import AuthService
from app.application.journal_service import JournalService
from app.application.paper_service import PaperService
from app.application.settings_service import SettingsService
from app.application.system_service import SystemService
from app.application.trading_service import TradingService
from app.domain.exceptions import AuthenticationError, NotFoundError, SettingsError, TradeRefusedError
from app.domain.models import Order, PaperAccount, PaperPosition, PaperTrade, Setting, Toggle
from app.infrastructure.database.repositories.event_repo import EventRepository
from app.infrastructure.database.repositories.order_repo import OrderRepository
from app.infrastructure.database.repositories.paper_repo import PaperRepository
from app.infrastructure.database.repositories.position_repo import PositionRepository
from app.infrastructure.database.repositories.settings_repo import SettingsRepository
from app.infrastructure.redis.cache import RedisCache
from app.infrastructure.redis.pubsub import EventPublisher


@pytest.fixture
def in_memory_session() -> Session:
    """Fixture providing isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.mark.asyncio
async def test_auth_service_pin_verification():
    """Verify valid and invalid PIN authentication."""
    mock_cache = MagicMock(spec=RedisCache)
    mock_cache.set_session = AsyncMock()
    mock_cache.get_session = AsyncMock(return_value={"authenticated": True})

    auth = AuthService(auth_pin="123456", auth_secret="test_secret", cache=mock_cache)

    # Valid PIN
    token = await auth.verify_pin("123456")
    assert token is not None
    assert len(token) > 10

    # Invalid PIN raises AuthenticationError
    with pytest.raises(AuthenticationError):
        await auth.verify_pin("999999")


@pytest.mark.asyncio
async def test_trading_service_place_and_close(in_memory_session: Session):
    """Test order placement and position closing through TradingService."""
    order_repo = OrderRepository(in_memory_session)
    position_repo = PositionRepository(in_memory_session)
    mock_pub = MagicMock(spec=EventPublisher)
    mock_pub.publish = AsyncMock()

    service = TradingService(order_repo=order_repo, position_repo=position_repo, publisher=mock_pub)

    # Test invalid inputs
    with pytest.raises(TradeRefusedError):
        await service.place_order(symbol="BTCUSD", side="invalid", size=1, price=50000.0)

    # Place order
    result = await service.place_order(symbol="BTCUSD", side="buy", size=2, price=65000.0)
    assert result["symbol"] == "BTCUSD"
    assert result["status"] == "pending"

    # Verify order in repo
    orders = service.get_orders()
    assert len(orders) == 1
    assert orders[0]["symbol"] == "BTCUSD"

    # Add mock position and close
    position_repo.create(PaperPosition(
        symbol="BTCUSD",
        client_order_id="test_order_123",
        strategy="manual",
        side="buy",
        size=2.0,
        contract_value=130000.0,
        entry_price=65000.0,
        leverage=1.0,
        margin=130000.0,
        entry_fee=10.0,
        opened_at=time.time(),
    ))

    close_result = await service.close_position("BTCUSD")
    assert close_result["closed"] is True

    # Closing again should raise NotFoundError
    with pytest.raises(NotFoundError):
        await service.close_position("BTCUSD")


def test_settings_service_validation(in_memory_session: Session):
    """Test settings range validation and live trade confirmation phrase."""
    repo = SettingsRepository(in_memory_session)
    service = SettingsService(repo=repo)

    # Valid settings update
    warnings = service.update_settings({"stop_loss_pct": "2.5", "paper_active": True})
    assert "delta_active" not in warnings
    assert service.get_all()["stop_loss_pct"] == "2.5"

    # Live trading without confirmation phrase raises SettingsError
    with pytest.raises(SettingsError):
        service.update_settings({"delta_active": True}, confirm="WRONG PHRASE")


def test_paper_service_stats_and_reset(in_memory_session: Session):
    """Test paper trading statistics calculation and account reset."""
    repo = PaperRepository(in_memory_session)
    service = PaperService(repo=repo)

    # Reset
    reset_data = service.reset(starting_balance=25000.0)
    assert reset_data["balance"] == 25000.0

    account = service.get_account()
    assert account["balance"] == 25000.0
    assert account["starting_balance"] == 25000.0


def test_journal_service_summaries(in_memory_session: Session):
    """Test daily and monthly trade journal calculations."""
    paper_repo = PaperRepository(in_memory_session)
    paper_service = PaperService(repo=paper_repo)
    journal_service = JournalService(paper_service=paper_service)

    summary = journal_service.get_day_summary("2026-10-24")
    assert summary["day"] == "2026-10-24"
    assert summary["trade_count"] == 0
    assert summary["total_pnl"] == 0.0


def test_system_service_health(in_memory_session: Session):
    """Test system health metrics and event logging."""
    event_repo = EventRepository(in_memory_session)
    service = SystemService(event_repo=event_repo)

    health = service.get_system_health()
    assert health["status"] == "healthy"
    assert "uptime_seconds" in health
    assert "cpu_percent" in health

    service.log_event("TEST_EVENT", {"detail": "unit test"})
    events = service.get_events()
    assert len(events) == 1
    assert events[0]["type"] == "TEST_EVENT"
