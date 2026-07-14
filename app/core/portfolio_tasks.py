"""Celery task: periodically advance the Portfolio paper-trading
portfolio (both strategies, risk-managed, one shared account).

Fetches a rolling window of OHLCV (via the same data_provider used by the
other strategies, so it benefits from the existing Redis cache), builds the
Portfolio feature set, advances the PortfolioManager simulation by
whatever new candles have arrived since the last run, and persists the result
to MongoDB. Signals from this feed are meant to inform REAL trades, so every
number persisted (entry price, stop, target, position size, leverage) is
exactly what the risk-managed simulation actually computed - nothing rounded
away or approximated for display purposes only.
"""

import pandas as pd

from app.core.celery_app import celery_app
from app.core.logger import get_celery_logger
from app.core.portfolio_manager import PortfolioManager
from app.core.settings import settings
from app.database.portfolio_store import (
    append_trades,
    deserialize_open_positions,
    load_state,
    save_state,
)
from app.utility.features import STRATEGIES, build_direction_array, build_features
from app.utility.data_provider import fetch_historical_data

logger = get_celery_logger()

# Use settings instead of hardcoded params
SYMBOL = settings.portfolio_symbol
INTERVAL = settings.portfolio_interval

FETCH_PERIOD_DAYS = 30

# Fetched every cycle - enough for every indicator's own warmup (~100 bars =
# ~4 days at 1h) plus comfortable margin. Data isn't cached separately here;
# it reuses data_provider's existing Redis cache (2 min TTL) like every other
# strategy in this repo.
FETCH_PERIOD_DAYS = 30


@celery_app.task(bind=True, name="run_portfolio_task", acks_late=True)
def run_portfolio_task(self):
    lock = None
    try:
        # Prevent concurrent runs via Redis lock
        from app.database.redis_publisher import get_redis_client
        redis_client = get_redis_client()
        lock = redis_client.lock("lock:portfolio_task", timeout=300)
        
        if not lock.acquire(blocking=False):
            logger.warning("⚠️ Portfolio task already running. Skipping this cycle.")
            return {"ok": False, "reason": "locked"}

        df = fetch_historical_data(SYMBOL, period=FETCH_PERIOD_DAYS, interval=INTERVAL)
        if df is None or df.empty or len(df) < 150:
            logger.warning(f"⚠️  Portfolio portfolio: not enough data yet for {SYMBOL} ({INTERVAL})")
            return {"ok": False, "error": "not enough data"}

        features_df = build_features(df)

        strategy_arrays = [
            {"name": key, "direction_array": build_direction_array(features_df, strat)}
            for key, strat in STRATEGIES.items()
        ]

        pm = PortfolioManager(
            features_df,
            strategy_arrays,
            initial_capital=settings.portfolio_initial_capital,
            risk_per_trade_pct=settings.portfolio_risk_per_trade_pct,
            stop_loss_pct=settings.portfolio_stop_loss_pct,
            take_profit_pct=settings.portfolio_take_profit_pct,
            max_hold_bars=settings.portfolio_max_hold_bars,
            fee_pct=settings.portfolio_fee_pct,
            max_leverage=settings.portfolio_max_leverage,
            max_concurrent_trades=settings.portfolio_max_concurrent_trades,
            portfolio_risk_cap_pct=settings.portfolio_risk_cap_pct,
            drawdown_throttle_trigger_pct=settings.portfolio_drawdown_trigger_pct,
            drawdown_recovery_pct=settings.portfolio_drawdown_recovery_pct,
            throttled_risk_pct=settings.portfolio_throttled_risk_pct,
        )

        state = load_state()
        is_first_run_ever = "last_processed_time" not in state

        if is_first_run_ever:
            # Establish baseline only - don't retroactively "trade" the whole
            # fetch window. Live tracking starts from the next genuinely new
            # candle onward, same as the reference livetest implementation.
            new_state = {
                "balance": settings.portfolio_initial_capital,
                "peak_equity": settings.portfolio_initial_capital,
                "throttled": False,
                "open_positions": [],
                "pending_entries": [],
                "last_processed_time": str(features_df.index[-1]),
                "symbol": SYMBOL,
                "interval": INTERVAL,
            }
            save_state(new_state)
            logger.info(f"🚀 Portfolio portfolio bootstrapped | {SYMBOL} {INTERVAL} | balance=${settings.portfolio_initial_capital}")
            return {"ok": True, "bootstrap": True, "balance": settings.portfolio_initial_capital}

        prior_pending = [tuple(p) for p in state.get("pending_entries", [])]
        prior_state = {
            "balance": state["balance"],
            "peak_equity": state.get("peak_equity", state["balance"]),
            "throttled": state.get("throttled", False),
            "open_positions": deserialize_open_positions(state.get("open_positions", [])),
            "pending_entries": prior_pending,
            "last_processed_time": pd.Timestamp(state["last_processed_time"]),
        }

        trades, equity, _, open_positions, pending_entries, peak_equity, throttled = pm.run_incremental(prior_state)

        if trades:
            append_trades(trades)
            for t in trades:
                logger.info(
                    f"📈 Portfolio trade CLOSED | {t['strategy']} {t['direction']} | "
                    f"{t['exit_reason']} | pnl={t['pnl']:+.2f} | equity=${t['equity_after']:.2f}"
                )

        if pending_entries and pending_entries != prior_pending:
            for name, direction in pending_entries:
                logger.info(
                    f"🔔 Portfolio NEW SIGNAL | {name} | {'LONG' if direction == 1 else 'SHORT'} | "
                    f"will fill on next candle's open"
                )

        new_state = {
            "balance": round(equity, 2),
            "peak_equity": round(peak_equity, 2),
            "throttled": throttled,
            "open_positions": open_positions,
            "pending_entries": list(pending_entries),
            "last_processed_time": str(features_df.index[-1]),
            "symbol": SYMBOL,
            "interval": INTERVAL,
        }
        save_state(new_state)

        return {
            "ok": True,
            "balance": new_state["balance"],
            "new_trades": len(trades),
            "open_positions": len(open_positions),
            "pending_signals": len(pending_entries),
        }

    except Exception as e:
        logger.error(f"❌ Portfolio task failed: {str(e)}", exc_info=True)
        raise e  # Ensure Celery marks the task as failed
    finally:
        if lock and lock.locked():
            try:
                lock.release()
            except Exception:
                pass
