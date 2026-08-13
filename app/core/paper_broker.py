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
    Simulated ("paper") broker: one virtual account per (strategy, symbol) pair, so a
    strategy trading multiple symbols (e.g. ETHUSD and BTCUSD) holds an independent
    position and capital balance in each rather than sharing one slot across symbols.

    Tracks capital, an open position (if any), and trade history in MongoDB.
    On an opposite signal, the existing position is closed and no new position is opened
    immediately (no signal reversal). Freezes an account once its capital drops to $1 or
    below. A Redis lock guards each account against concurrent updates.
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

    def _get_account(self, strategy_name: str, symbol: str) -> Dict[str, Any]:
        """Returns a strategy's virtual account for one symbol, creating a fresh $100
        one if it doesn't exist yet."""
        account_id = f"{strategy_name}::{symbol}"
        account = self.accounts_coll.find_one({"_id": account_id})
        if not account:
            account = {
                "_id": account_id,
                "strategy_name": strategy_name,
                "symbol": symbol,
                "capital": 100.0,
                "total_trades": 0,
                "winning_trades": 0,
                "win_rate": 0.0,
                "open_position": None,
            }
            self.accounts_coll.insert_one(account)
        return account

    def _calc_pnl(self, pos: Dict[str, Any], exit_price: float) -> Dict[str, float]:
        """Computes gross PnL, the exit fee, and the resulting net PnL for a position -
        shared by an actual close and a live unrealized-PnL preview.

        Net PnL is charged both the entry fee and the exit fee: sizing the position off
        (capital - entry_fee) only shrinks gross PnL in proportion to price movement, it
        never actually removes the entry fee's cost from the account - so it must still
        be subtracted here, or a flat trade at an unchanged price would net $0 fee cost
        instead of the ~2x fee_pct a real round-trip costs.
        """
        entry_price = pos["entry_price"]
        size = pos["size"]
        pos_type = pos["type"]
        entry_fee = pos.get("entry_fee", 0.0)

        if pos_type == "LONG":
            gross_pnl = (exit_price - entry_price) * size
        else:  # SHORT
            gross_pnl = (entry_price - exit_price) * size

        exit_value = (size * exit_price) if pos_type == "LONG" else (size * entry_price)
        exit_fee = exit_value * self.fee_pct

        return {"gross_pnl": gross_pnl, "exit_fee": exit_fee, "net_pnl": gross_pnl - entry_fee - exit_fee}

    def _close_position(
        self, account: Dict[str, Any], current_price: float, exit_time: datetime, reason: str = "Signal Reverse"
    ) -> Dict[str, Any]:
        """Closes the account's open position, updates capital/win-rate, and records the trade."""
        pos = account["open_position"]
        if not pos:
            return account

        pnl_components = self._calc_pnl(pos, current_price)
        gross_pnl = pnl_components["gross_pnl"]
        exit_fee = pnl_components["exit_fee"]
        net_pnl = pnl_components["net_pnl"]
        entry_fee = pos.get("entry_fee", 0.0)

        account["capital"] += net_pnl
        account["total_trades"] += 1

        is_win = net_pnl > 0
        if is_win:
            account["winning_trades"] += 1

        account["win_rate"] = (account["winning_trades"] / account["total_trades"]) * 100.0

        trade_record = {
            "strategy_name": account["strategy_name"],
            "symbol": pos["symbol"],
            "type": pos["type"],
            "entry_time": pos["entry_time"],
            "exit_time": exit_time,
            "entry_price": pos["entry_price"],
            "exit_price": current_price,
            "size": pos["size"],
            "capital_allocated": pos.get("capital_allocated", account["capital"]),
            "leverage": pos.get("leverage", getattr(settings, "broker_leverage", 20.0)),
            "notional_value": round(pos["size"] * pos["entry_price"], 2),
            "gross_pnl": round(gross_pnl, 4),
            "entry_fee": round(entry_fee, 4),
            "exit_fee": round(exit_fee, 4),
            "total_fees": round(entry_fee + exit_fee, 4),
            "pnl": round(net_pnl, 4),
            "return_pct": round((net_pnl / pos["capital_allocated"]) * 100, 2) if pos.get("capital_allocated") else round((net_pnl / account["capital"]) * 100, 2),
            "reason": reason,
            "stop_price": pos.get("stop_price"),
            "target_price": pos.get("target_price"),
        }
        self.trades_coll.insert_one(trade_record)

        account["open_position"] = None

        logger.info(f"📊 BROKER | {account['_id']} CLOSED {pos['type']} | PnL: ${net_pnl:.2f} (fees: ${entry_fee + exit_fee:.2f}) | Balance: ${account['capital']:.2f}")
        return account

    def _open_position(
        self, account: Dict[str, Any], pos_type: str, symbol: str, price: float, entry_time: datetime
    ) -> Dict[str, Any]:
        """Opens a new position using 20x default leverage, allocating available capital as margin.

        Also sets a stop-loss and take-profit level (from settings.broker_stop_loss_pct /
        broker_take_profit_pct) so the position has a protective exit even if the strategy
        itself keeps signaling HOLD - see check_protective_exit().
        """
        capital = account["capital"]
        leverage = getattr(settings, "broker_leverage", 20.0)

        notional_capital = capital * leverage
        fee = notional_capital * self.fee_pct
        investable_notional = notional_capital - fee
        size = investable_notional / price

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
            "leverage": leverage,
            "notional_value": round(size * price, 2),
            "entry_fee": round(fee, 4),
            "entry_time": entry_time,
            "stop_price": stop_price,
            "target_price": target_price,
            "last_price": None,
            "last_price_time": None,
            "unrealized_pnl": None,
        }

        logger.info(
            f"📊 BROKER | {account['_id']} OPENED {pos_type} ({leverage:.0f}x Margin) | "
            f"Size: {size:.4f} @ ${price:.2f} (Notional: ${size * price:.2f}, Margin: ${capital:.2f}) | "
            f"Fee: ${fee:.2f} | Stop: ${stop_price:.2f} | Target: ${target_price:.2f}"
        )
        return account

    def check_protective_exit(self, strategy_name: str, symbol: str, current_price: float, current_time: datetime) -> None:
        """Closes a strategy's open position if the current price has crossed its
        stop-loss or take-profit level; otherwise snapshots the live price and
        unrealized PnL onto the position so the dashboard can show it.

        Called every batch cycle for every (strategy, symbol) pair regardless of that
        cycle's signal, so a protective exit isn't missed while the strategy is signaling
        HOLD in between actual BUY/SELL signals.
        """
        if current_price <= 0:
            return

        lock_name = f"lock:broker:{strategy_name}:{symbol}"
        lock = self.redis.lock(lock_name, timeout=10)

        if not lock.acquire(blocking=True, blocking_timeout=5):
            logger.warning(f"⚠️ BROKER | Could not acquire lock for {strategy_name}:{symbol}, skipping protective-exit check.")
            return

        try:
            account = self._get_account(strategy_name, symbol)
            pos = account.get("open_position")
            if not pos:
                return

            stop_price = pos.get("stop_price")
            target_price = pos.get("target_price")

            hit_stop = hit_target = False
            if stop_price is not None and target_price is not None:
                if pos["type"] == "LONG":
                    hit_stop = current_price <= stop_price
                    hit_target = current_price >= target_price
                else:  # SHORT
                    hit_stop = current_price >= stop_price
                    hit_target = current_price <= target_price

            # ── 24-Hour Time Limit Check ─────────────────────────────────────
            entry_time = pos.get("entry_time")
            hit_time_limit = False
            if entry_time:
                if isinstance(entry_time, str):
                    try:
                        entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                    except Exception:
                        entry_dt = None
                else:
                    entry_dt = entry_time

                if entry_dt:
                    if entry_dt.tzinfo is None:
                        from datetime import timezone
                        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                    curr_dt = current_time
                    if curr_dt.tzinfo is None:
                        from datetime import timezone
                        curr_dt = curr_dt.replace(tzinfo=timezone.utc)

                    holding_hours = (curr_dt - entry_dt).total_seconds() / 3600.0
                    if holding_hours >= 24.0:
                        hit_time_limit = True

            if hit_stop:
                account = self._close_position(account, stop_price, current_time, "Stop Loss Hit")
            elif hit_target:
                account = self._close_position(account, target_price, current_time, "Take Profit Hit")
            elif hit_time_limit:
                account = self._close_position(account, current_price, current_time, "Time Exceeded (24h Limit)")
            else:
                pos["last_price"] = current_price
                pos["last_price_time"] = current_time
                pos["unrealized_pnl"] = round(self._calc_pnl(pos, current_price)["net_pnl"], 4)

            self.accounts_coll.update_one({"_id": account["_id"]}, {"$set": account})

        except Exception as e:
            logger.error(f"❌ BROKER | Error checking protective exit for {strategy_name}:{symbol}: {str(e)}", exc_info=True)
        finally:
            try:
                lock.release()
            except Exception:
                pass

    def process_signal(self, strategy_name: str, symbol: str, signal: SignalType, price: float, timestamp: datetime) -> None:
        """Opens a new position in response to a signal — only when no position is currently open.

        If a position is already open, ALL signals are ignored. The position will only be
        closed by check_protective_exit() when Stop Loss or Take Profit is hit.
        Acquires a per-(strategy, symbol) Redis lock to prevent concurrent corruption.
        """
        if signal == SignalType.HOLD or price <= 0:
            return

        lock_name = f"lock:broker:{strategy_name}:{symbol}"
        lock = self.redis.lock(lock_name, timeout=10)

        if not lock.acquire(blocking=True, blocking_timeout=5):
            logger.warning(f"⚠️ BROKER | Could not acquire lock for {strategy_name}:{symbol}, skipping signal.")
            return

        try:
            account = self._get_account(strategy_name, symbol)

            if account["capital"] <= 1.0:
                return  # bankrupt - do nothing

            pos = account["open_position"]

            # ── ACTIVE LOGIC ─────────────────────────────────────────────────────────
            # If a position is already open, ignore all signals.
            # Position closes ONLY when SL or TP is hit (check_protective_exit).
            if pos:
                logger.debug(
                    f"📊 BROKER | {strategy_name}:{symbol} already has an open {pos['type']} position. "
                    f"Signal {signal.value} ignored — waiting for SL/TP."
                )
                return

            # No open position — open a new one based on signal.
            if signal == SignalType.BUY:
                account = self._open_position(account, "LONG", symbol, price, timestamp)
            elif signal == SignalType.SELL:
                account = self._open_position(account, "SHORT", symbol, price, timestamp)

            # ── COMMENTED: Signal Exit (no reversal, but closes on opposite signal) ──
            # if signal == SignalType.BUY:
            #     if pos:
            #         if pos["type"] == "SHORT":
            #             account = self._close_position(account, price, timestamp, "Signal Exit (BUY)")
            #         # Already LONG - no-op.
            #     else:
            #         account = self._open_position(account, "LONG", symbol, price, timestamp)
            # elif signal == SignalType.SELL:
            #     if pos:
            #         if pos["type"] == "LONG":
            #             account = self._close_position(account, price, timestamp, "Signal Exit (SELL)")
            #         # Already SHORT - no-op.
            #     else:
            #         account = self._open_position(account, "SHORT", symbol, price, timestamp)

            # ── COMMENTED: Signal Reverse (closes + immediately opens opposite) ──────
            # if signal == SignalType.BUY:
            #     if pos:
            #         if pos["type"] == "SHORT":
            #             account = self._close_position(account, price, timestamp, "Signal Reverse (BUY)")
            #             account = self._open_position(account, "LONG", symbol, price, timestamp)
            #         # Already LONG - no-op.
            #     else:
            #         account = self._open_position(account, "LONG", symbol, price, timestamp)
            # elif signal == SignalType.SELL:
            #     if pos:
            #         if pos["type"] == "LONG":
            #             account = self._close_position(account, price, timestamp, "Signal Reverse (SELL)")
            #             account = self._open_position(account, "SHORT", symbol, price, timestamp)
            #         # Already SHORT - no-op.
            #     else:
            #         account = self._open_position(account, "SHORT", symbol, price, timestamp)

            self.accounts_coll.update_one({"_id": account["_id"]}, {"$set": account})

        except Exception as e:
            logger.error(f"❌ BROKER | Error processing signal for {strategy_name}:{symbol}: {str(e)}", exc_info=True)
        finally:
            try:
                lock.release()
            except Exception:
                pass
