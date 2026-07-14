import time
from datetime import datetime
from app.models.strategy_models import SignalType
from app.database.mongodb import MongoDBConnection
from app.database.redis_publisher import get_redis_client
from app.core.logger import get_celery_logger

logger = get_celery_logger()

class PaperBroker:
    def __init__(self):
        self.db_conn = MongoDBConnection()
        self.db = self.db_conn.db
        self.accounts_coll = self.db.broker_accounts
        self.trades_coll = self.db.broker_trades
        self.redis = get_redis_client()
        self.fee_pct = 0.0005 # 0.05% per trade leg

    def _get_account(self, strategy_name: str) -> dict:
        account = self.accounts_coll.find_one({"_id": strategy_name})
        if not account:
            account = {
                "_id": strategy_name,
                "capital": 100.0,
                "total_trades": 0,
                "winning_trades": 0,
                "win_rate": 0.0,
                "open_position": None
            }
            self.accounts_coll.insert_one(account)
        return account

    def _close_position(self, account: dict, current_price: float, exit_time: datetime, reason: str = "Signal Reverse"):
        pos = account["open_position"]
        if not pos:
            return account
            
        entry_price = pos["entry_price"]
        size = pos["size"]
        pos_type = pos["type"]
        symbol = pos["symbol"]
        
        # Calculate gross return
        if pos_type == "LONG":
            gross_pnl = (current_price - entry_price) * size
        else: # SHORT
            gross_pnl = (entry_price - current_price) * size
            
        # Deduct exit fee (entry fee was deducted on open)
        exit_value = (size * current_price) if pos_type == "LONG" else (size * entry_price)
        exit_fee = exit_value * self.fee_pct
        
        net_pnl = gross_pnl - exit_fee
        
        # Update capital
        account["capital"] += net_pnl
        account["total_trades"] += 1
        
        is_win = net_pnl > 0
        if is_win:
            account["winning_trades"] += 1
            
        account["win_rate"] = (account["winning_trades"] / account["total_trades"]) * 100.0
        
        # Record trade
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
            "reason": reason
        }
        self.trades_coll.insert_one(trade_record)
        
        # Clear open position
        account["open_position"] = None
        
        logger.info(f"📊 BROKER | {account['_id']} CLOSED {pos_type} | PnL: ${net_pnl:.2f} | Balance: ${account['capital']:.2f}")
        return account

    def _open_position(self, account: dict, pos_type: str, symbol: str, price: float, entry_time: datetime):
        capital = account["capital"]
        
        # Deduct entry fee
        fee = capital * self.fee_pct
        investable = capital - fee
        
        size = investable / price
        
        account["open_position"] = {
            "type": pos_type,
            "symbol": symbol,
            "entry_price": price,
            "size": size,
            "capital_allocated": capital,
            "entry_time": entry_time
        }
        
        logger.info(f"📊 BROKER | {account['_id']} OPENED {pos_type} | Size: {size:.4f} @ ${price:.2f}")
        return account

    def process_signal(self, strategy_name: str, symbol: str, signal: SignalType, price: float, timestamp: datetime):
        if signal == SignalType.HOLD or price <= 0:
            return

        lock_name = f"lock:broker:{strategy_name}"
        lock = self.redis.lock(lock_name, timeout=10)
        
        if not lock.acquire(blocking=True, blocking_timeout=5):
            logger.warning(f"⚠️ BROKER | Could not acquire lock for {strategy_name}, skipping signal.")
            return

        try:
            account = self._get_account(strategy_name)
            
            # If bankrupt, do nothing
            if account["capital"] <= 1.0:
                return

            pos = account["open_position"]
            
            if signal == SignalType.BUY:
                if pos:
                    if pos["type"] == "SHORT":
                        account = self._close_position(account, price, timestamp, "Signal Reverse (BUY)")
                        account = self._open_position(account, "LONG", symbol, price, timestamp)
                    # If already LONG, do nothing (or could add to position, but we keep it simple)
                else:
                    account = self._open_position(account, "LONG", symbol, price, timestamp)
                    
            elif signal == SignalType.SELL:
                if pos:
                    if pos["type"] == "LONG":
                        account = self._close_position(account, price, timestamp, "Signal Reverse (SELL)")
                        account = self._open_position(account, "SHORT", symbol, price, timestamp)
                    # If already SHORT, do nothing
                else:
                    account = self._open_position(account, "SHORT", symbol, price, timestamp)

            # Save state
            self.accounts_coll.update_one({"_id": strategy_name}, {"$set": account})

        except Exception as e:
            logger.error(f"❌ BROKER | Error processing signal for {strategy_name}: {str(e)}", exc_info=True)
        finally:
            try:
                lock.release()
            except:
                pass
