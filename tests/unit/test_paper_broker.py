from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.core.paper_broker import PaperBroker
from app.models.strategy_models import SignalType


@pytest.fixture
def mock_db_and_redis():
    """Mock the SQLite adapter and Redis client for the PaperBroker tests."""
    with patch("app.core.paper_broker.DatabaseConnection") as mock_mongo_conn, \
         patch("app.core.paper_broker.get_redis_client") as mock_redis_client:
        
        # Mock database and collections
        mock_db = MagicMock()
        mock_mongo_conn.get_database.return_value = mock_db
        
        mock_accounts = MagicMock()
        mock_trades = MagicMock()
        mock_db.broker_accounts = mock_accounts
        mock_db.broker_trades = mock_trades
        
        # Mock Redis client and locks
        mock_redis = MagicMock()
        mock_redis_client.return_value = mock_redis
        mock_lock = MagicMock()
        mock_redis.lock.return_value = mock_lock
        mock_lock.acquire.return_value = True
        
        yield {
            "accounts_coll": mock_accounts,
            "trades_coll": mock_trades,
            "redis_client": mock_redis,
            "lock": mock_lock
        }


def test_paper_broker_get_account_new(mock_db_and_redis: dict) -> None:
    """Test retrieving an account when it does not exist in SQLite."""
    mock_accounts = mock_db_and_redis["accounts_coll"]
    mock_accounts.find_one.return_value = None  # Account does not exist
    
    broker = PaperBroker()
    account = broker._get_account("TestStrategy", "BTC-USD")
    
    assert account["_id"] == "TestStrategy::BTC-USD"
    assert account["capital"] == 100.0
    assert account["total_trades"] == 0
    mock_accounts.insert_one.assert_called_once_with(account)


def test_paper_broker_get_account_existing(mock_db_and_redis: dict) -> None:
    """Test retrieving an account when it already exists in SQLite."""
    mock_accounts = mock_db_and_redis["accounts_coll"]
    existing_account = {
        "_id": "TestStrategy::BTC-USD",
        "strategy_name": "TestStrategy",
        "symbol": "BTC-USD",
        "capital": 150.0,
        "total_trades": 2,
        "winning_trades": 1,
        "win_rate": 50.0,
        "open_position": None
    }
    mock_accounts.find_one.return_value = existing_account
    
    broker = PaperBroker()
    account = broker._get_account("TestStrategy", "BTC-USD")
    
    assert account["capital"] == 150.0
    mock_accounts.insert_one.assert_not_called()


def test_paper_broker_open_position(mock_db_and_redis: dict) -> None:
    """Test opening a new LONG position and verify correct capital/fee/size calculation with 20x leverage and 50% capital allocation."""
    broker = PaperBroker()
    account = {
        "_id": "TestStrategy::BTC-USD",
        "strategy_name": "TestStrategy",
        "symbol": "BTC-USD",
        "capital": 100.0,
        "total_trades": 0,
        "winning_trades": 0,
        "win_rate": 0.0,
        "open_position": None
    }
    
    entry_time = datetime.now(UTC)
    updated_account = broker._open_position(account, "LONG", "BTC-USD", 50000.0, entry_time)
    
    pos = updated_account["open_position"]
    assert pos is not None
    assert pos["type"] == "LONG"
    assert pos["symbol"] == "BTC-USD"
    assert pos["entry_price"] == 50000.0
    # $100 capital * 50% = $50 Margin. $50 * 20x = $1000 Notional. Fee (0.05%) = $0.50. Investable = $999.50. Size = 999.50 / 50000 = 0.01999
    assert pos["margin_used"] == 50.0
    assert pos["size"] == pytest.approx(999.50 / 50000.0)
    assert pos["liquidation_price"] == 47500.0


def test_paper_broker_close_position_win(mock_db_and_redis: dict) -> None:
    """Test closing a winning LONG position and verify return calculations."""
    mock_trades = mock_db_and_redis["trades_coll"]
    broker = PaperBroker()
    
    entry_time = datetime.now(UTC)
    account = {
        "_id": "TestStrategy::BTC-USD",
        "strategy_name": "TestStrategy",
        "symbol": "BTC-USD",
        "capital": 100.0,
        "total_trades": 0,
        "winning_trades": 0,
        "win_rate": 0.0,
        "open_position": {
            "type": "LONG",
            "symbol": "BTC-USD",
            "entry_price": 50000.0,
            "size": 0.002,
            "capital_allocated": 100.0,
            "entry_time": entry_time
        }
    }
    
    exit_time = datetime.now(UTC)
    # Price went up to 60000 (Win!)
    updated_account = broker._close_position(account, 60000.0, exit_time)
    
    assert updated_account["open_position"] is None
    assert updated_account["total_trades"] == 1
    assert updated_account["winning_trades"] == 1
    assert updated_account["win_rate"] == 100.0
    
    assert updated_account["capital"] == pytest.approx(119.94)
    mock_trades.insert_one.assert_called_once()


def test_paper_broker_process_signal_buy(mock_db_and_redis: dict) -> None:
    """Test processing a BUY signal on a flat account."""
    mock_accounts = mock_db_and_redis["accounts_coll"]
    
    account = {
        "_id": "TestStrategy::BTC-USD",
        "strategy_name": "TestStrategy",
        "symbol": "BTC-USD",
        "capital": 100.0,
        "total_trades": 0,
        "winning_trades": 0,
        "win_rate": 0.0,
        "open_position": None
    }
    mock_accounts.find_one.return_value = account
    
    broker = PaperBroker()
    timestamp = datetime.now(UTC)
    broker.process_signal("TestStrategy", "BTC-USD", SignalType.BUY, 50000.0, timestamp)
    
    # Check that update_one was called to save the new position
    mock_accounts.update_one.assert_called_once()
    saved_account = mock_accounts.update_one.call_args[0][1]["$set"]
    assert saved_account["open_position"]["type"] == "LONG"
    assert saved_account["open_position"]["entry_price"] == 50000.0


def test_open_position_generates_position_id_and_records_opened_event(mock_db_and_redis: dict) -> None:
    """Opening a position stamps a position_id and logs an OPENED/SYSTEM audit event."""
    with patch("app.core.paper_broker.position_events_store.record_event") as mock_record:
        broker = PaperBroker()
        account = {
            "_id": "TestStrategy::BTC-USD",
            "strategy_name": "TestStrategy",
            "symbol": "BTC-USD",
            "capital": 100.0,
            "total_trades": 0,
            "winning_trades": 0,
            "win_rate": 0.0,
            "open_position": None,
        }

        entry_time = datetime.now(UTC)
        updated_account = broker._open_position(account, "LONG", "BTC-USD", 50000.0, entry_time)
        pos = updated_account["open_position"]

        assert pos["position_id"]
        mock_record.assert_called_once()
        kwargs = mock_record.call_args.kwargs
        assert kwargs["position_id"] == pos["position_id"]
        assert kwargs["event_type"] == "OPENED"
        assert kwargs["changed_by"] == "SYSTEM"


def test_update_protection_records_change_event_with_old_and_new_values(mock_db_and_redis: dict) -> None:
    """Adjusting SL/TP on an open position logs a PROTECTION_CHANGED/MANUAL event with previous and new values."""
    mock_accounts = mock_db_and_redis["accounts_coll"]
    account = {
        "_id": "TestStrategy::BTC-USD",
        "strategy_name": "TestStrategy",
        "symbol": "BTC-USD",
        "capital": 100.0,
        "open_position": {
            "position_id": "pos-123",
            "type": "LONG",
            "symbol": "BTC-USD",
            "entry_price": 50000.0,
            "stop_price": 48000.0,
            "target_price": 53000.0,
        },
    }
    mock_accounts.find_one.return_value = account

    with patch("app.core.paper_broker.position_events_store.record_event") as mock_record:
        broker = PaperBroker()
        broker.update_protection("TestStrategy", "BTC-USD", 48500.0, 53500.0, 50000.0)

        mock_record.assert_called_once()
        kwargs = mock_record.call_args.kwargs
        assert kwargs["position_id"] == "pos-123"
        assert kwargs["event_type"] == "PROTECTION_CHANGED"
        assert kwargs["changed_by"] == "MANUAL"
        assert kwargs["previous_stop_price"] == 48000.0
        assert kwargs["previous_target_price"] == 53000.0
        assert kwargs["new_stop_price"] == 48500.0
        assert kwargs["new_target_price"] == 53500.0


@pytest.mark.parametrize(
    ("reason", "expected_changed_by"),
    [("Manual Close", "MANUAL"), ("Stop Loss Hit", "SYSTEM")],
)
def test_close_position_records_closed_event_with_reason_and_changed_by(
    mock_db_and_redis: dict, reason: str, expected_changed_by: str
) -> None:
    """Closing a position logs a CLOSED event whose changed_by reflects a manual vs automatic reason,
    and stamps position_id onto the persisted trade record."""
    mock_trades = mock_db_and_redis["trades_coll"]
    broker = PaperBroker()

    entry_time = datetime.now(UTC)
    account = {
        "_id": "TestStrategy::BTC-USD",
        "strategy_name": "TestStrategy",
        "symbol": "BTC-USD",
        "capital": 100.0,
        "total_trades": 0,
        "winning_trades": 0,
        "win_rate": 0.0,
        "open_position": {
            "position_id": "pos-456",
            "type": "LONG",
            "symbol": "BTC-USD",
            "entry_price": 50000.0,
            "size": 0.002,
            "capital_allocated": 100.0,
            "entry_time": entry_time,
        },
    }

    with patch("app.core.paper_broker.position_events_store.record_event") as mock_record:
        exit_time = datetime.now(UTC)
        broker._close_position(account, 60000.0, exit_time, reason)

        trade_record = mock_trades.insert_one.call_args[0][0]
        assert trade_record["position_id"] == "pos-456"

        mock_record.assert_called_once()
        kwargs = mock_record.call_args.kwargs
        assert kwargs["position_id"] == "pos-456"
        assert kwargs["event_type"] == "CLOSED"
        assert kwargs["changed_by"] == expected_changed_by
        assert kwargs["reason"] == reason
