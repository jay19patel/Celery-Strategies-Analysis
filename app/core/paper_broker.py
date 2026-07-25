import time
from datetime import datetime
from typing import Any, Dict

from app.models.strategy_models import SignalType
from app.database.mongodb import MongoDBConnection
from app.database.redis_publisher import get_redis_client
from app.core.logger import get_celery_logger
from app.core.settings import settings

logger = get_celery_logger()


class PaperBroker:
    """
    Simulated ("paper") broker: one virtual account per strategy.
    
    Tracks capital, an open position (if any), and trade history in MongoDB.
    Reverses a position on an opposite signal, and freezes an account once its
    capital drops to $1 or below. A Redis lock guards each strategy's account
    against concurrent updates.
    """

    def __init__(self) -> None:
        """Sets up the fee rate. Connections are retrieved dynamically for fork-safety."""
        self.fee_pct: float = 0.0005  # 0.05% per trade leg

    @property
    def db(self) -> Any:
        """Returns the MongoDB database instance dynamically to ensure fork-safety."""
        return MongoDBConnection.get_database()

    @property
    def accounts_coll(self) -> Any:
        """Returns the broker_accounts MongoDB collection dynamically."""
        return self.db.broker_accounts

    @property
    def trades_coll(self) -> Any:
        """Returns the broker_trades MongoDB collection dynamically."""
        return self.db.broker_trades

    @property
    def redis(self) -> Any:
        """Returns the Redis client instance dynamically to ensure fork-safety."""
        return get_redis_client()

    def _get_account(self, strategy_name: str) -> Dict[str, Any]:
        """Returns a strategy's virtual account, creating a fresh $100 one if it doesn't exist yet."""
        account = self.accounts_coll.find_one({"_id": strategy_name})
        if not account:
            account = {
                "_id": strategy_name,
                "capital": 100.0,
                "total_trades": 0,
                "winning_trades": 0,
                "win_rate": 0.0,
                "open_position": None,
            }
            self.accounts_coll.insert_one(account)
        return account

    def _close_position(
        self, account: Dict[str, Any], current_price: float, exit_time: datetime, reason: str = "Signal Reverse"
    ) -> Dict[str, Any]:
        """Closes the account's open position, updates capital/win-rate, and records the trade."""
        pos = account["open_position"]
        if not pos:
            return account

        entry_price = pos["entry_price"]
        size = pos["size"]
        pos_type = pos["type"]
        symbol = pos["symbol"]

        if pos_type == "LONG":
            gross_pnl = (current_price - entry_price) * size
        else:  # SHORT
            gross_pnl = (entry_price - current_price) * size

        # Exit fee only - the entry fee was already deducted when the position was opened.
        exit_value = (size * current_price) if pos_type == "LONG" else (size * entry_price)
        exit_fee = exit_value * self.fee_pct

        net_pnl = gross_pnl - exit_fee

        account["capital"] += net_pnl
        account["total_trades"] += 1

        is_win = net_pnl > 0
        if is_win:
            account["winning_trades"] += 1

        account["win_rate"] = (account["winning_trades"] / account["total_trades"]) * 100.0

        trade_record = {
            "strategy_name": account["_id"],
            "symbol": symbol,
            "type": pos_type,
            "entry_time": pos["entry_time"],
            "exit_time": exit_time,
            "entry_price": entry_price,
            "exit_price": current_price,
            "size": size,
            "pnl": round(net_pnl, 4),
            "return_pct": round((net_pnl / pos["capital_allocated"]) * 100, 2),
            "reason": reason,
            "stop_price": pos.get("stop_price"),
            "target_price": pos.get("target_price"),
        }
        self.trades_coll.insert_one(trade_record)

        account["open_position"] = None

        logger.info(f"📊 BROKER | {account['_id']} CLOSED {pos_type} | PnL: ${net_pnl:.2f} | Balance: ${account['capital']:.2f}")
        return account

    def _open_position(
        self, account: Dict[str, Any], pos_type: str, symbol: str, price: float, entry_time: datetime
    ) -> Dict[str, Any]:
        """Opens a new position, allocating all available capital and deducting the entry fee.

        Also sets a stop-loss and take-profit level (from settings.broker_stop_loss_pct /
        broker_take_profit_pct) so the position has a protective exit even if the strategy
        itself keeps signaling HOLD - see check_protective_exit().
        """
        capital = account["capital"]

        fee = capital * self.fee_pct
        investable = capital - fee
        size = investable / price

        stop_dist = price * (settings.broker_stop_loss_pct / 100)
        target_dist = price * (settings.broker_take_profit_pct / 100)
        if pos_type == "LONG":
            stop_price = price - stop_dist
            target_price = price + target_dist
        else:  # SHORT
            stop_price = price + stop_dist
            target_price = price - target_dist

        account["open_position"] = {
            "type": pos_type,
            "symbol": symbol,
            "entry_price": price,
            "size": size,
            "capital_allocated": capital,
            "entry_time": entry_time,
            "stop_price": stop_price,
            "target_price": target_price,
        }

        logger.info(
            f"📊 BROKER | {account['_id']} OPENED {pos_type} | Size: {size:.4f} @ ${price:.2f} | "
            f"Stop: ${stop_price:.2f} | Target: ${target_price:.2f}"
        )
        return account

    def check_protective_exit(self, strategy_name: str, symbol: str, current_price: float, current_time: datetime) -> None:
        """Closes a strategy's open position if the current price has crossed its
        stop-loss or take-profit level.

        Called every batch cycle for every (strategy, symbol) pair regardless of that
        cycle's signal, so a protective exit isn't missed while the strategy is signaling
        HOLD in between actual BUY/SELL signals.
        """
        if current_price <= 0:
            return

        lock_name = f"lock:broker:{strategy_name}"
        lock = self.redis.lock(lock_name, timeout=10)

        if not lock.acquire(blocking=True, blocking_timeout=5):
            logger.warning(f"⚠️ BROKER | Could not acquire lock for {strategy_name}, skipping protective-exit check.")
            return

        try:
            account = self._get_account(strategy_name)
            pos = account.get("open_position")
            if not pos or pos.get("symbol") != symbol:
                return

            stop_price = pos.get("stop_price")
            target_price = pos.get("target_price")
            if stop_price is None or target_price is None:
                return  # position opened before this protection existed

            if pos["type"] == "LONG":
                hit_stop = current_price <= stop_price
                hit_target = current_price >= target_price
            else:  # SHORT
                hit_stop = current_price >= stop_price
                hit_target = current_price <= target_price

            if hit_stop:
                account = self._close_position(account, stop_price, current_time, "Stop Loss Hit")
            elif hit_target:
                account = self._close_position(account, target_price, current_time, "Take Profit Hit")
            else:
                return

            self.accounts_coll.update_one({"_id": strategy_name}, {"$set": account})

        except Exception as e:
            logger.error(f"❌ BROKER | Error checking protective exit for {strategy_name}: {str(e)}", exc_info=True)
        finally:
            try:
                lock.release()
            except Exception:
                pass

    def process_signal(self, strategy_name: str, symbol: str, signal: SignalType, price: float, timestamp: datetime) -> None:
        """Opens, closes, or reverses a strategy's position in response to a new signal.

        Acquires a per-strategy Redis lock first, so concurrent signals for the
        same strategy can't corrupt its account.
        """
        if signal == SignalType.HOLD or price <= 0:
            return

        lock_name = f"lock:broker:{strategy_name}"
        lock = self.redis.lock(lock_name, timeout=10)

        if not lock.acquire(blocking=True, blocking_timeout=5):
            logger.warning(f"⚠️ BROKER | Could not acquire lock for {strategy_name}, skipping signal.")
            return

        try:
            account = self._get_account(strategy_name)

            if account["capital"] <= 1.0:
                return  # bankrupt - do nothing

            pos = account["open_position"]

            if signal == SignalType.BUY:
                if pos:
                    if pos["type"] == "SHORT":
                        account = self._close_position(account, price, timestamp, "Signal Reverse (BUY)")
                        account = self._open_position(account, "LONG", symbol, price, timestamp)
                    # Already LONG - keep it simple, don't add to the position.
                else:
                    account = self._open_position(account, "LONG", symbol, price, timestamp)

            elif signal == SignalType.SELL:
                if pos:
                    if pos["type"] == "LONG":
                        account = self._close_position(account, price, timestamp, "Signal Reverse (SELL)")
                        account = self._open_position(account, "SHORT", symbol, price, timestamp)
                    # Already SHORT - no-op.
                else:
                    account = self._open_position(account, "SHORT", symbol, price, timestamp)

            self.accounts_coll.update_one({"_id": strategy_name}, {"$set": account})

        except Exception as e:
            logger.error(f"❌ BROKER | Error processing signal for {strategy_name}: {str(e)}", exc_info=True)
        finally:
            try:
                lock.release()
            except Exception:
                pass
