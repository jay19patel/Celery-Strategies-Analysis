"""PortfolioManager: trades the 2 Portfolio strategies together on ONE
shared, risk-managed paper account - ported from the Portfolio
project's livetest/portfolio_manager.py (same risk-management rules, same
math), adapted to resume from MongoDB-persisted state instead of a local
JSON file.

Signals from this system are meant to inform REAL trades (see the strategy
docstrings' out-of-sample caveats) - this is why every number here (entry
price, stop, target, position size, leverage) is computed exactly the same
way as the validated backtest, not simplified or approximated.

Risk management, unchanged from the validated design:
  - One LONG and one SHORT open at a time, max: a new signal is skipped if a
    position in that SAME direction is already open, no matter which strategy
    fired it.
  - Leverage cap: position notional capped at `max_leverage` x current equity.
  - Portfolio-level risk cap: total risk "on the table" across every open
    position is capped as a % of equity.
  - Drawdown throttle: risk-per-trade is automatically cut once the account
    is down past a trigger, restored once it recovers past a lower threshold.

Positions are tracked by absolute `entry_time` (not a bar-index into
`self.df`) specifically so a simulation can be RESUMED on a different
(later-fetched) df that doesn't contain the earlier bars at all - the Celery
task only ever fetches a small rolling window of candles, never the full
history since inception.

Usage:
    pm = PortfolioManager(df, strategies)
    trades, equity, equity_curve, open_positions, pending_entries, peak_equity, throttled = pm.run_incremental(prior_state)
"""

import numpy as np
import pandas as pd


class PortfolioManager:
    def __init__(
        self,
        df,
        strategies,
        initial_capital=100.0,
        risk_per_trade_pct=2.0,
        stop_loss_pct=1.0,
        take_profit_pct=3.0,
        max_hold_bars=20,
        fee_pct=0.05,
        max_concurrent_trades=5,
        portfolio_risk_cap_pct=10.0,
        drawdown_throttle_trigger_pct=10.0,
        drawdown_recovery_pct=5.0,
        throttled_risk_pct=1.0,
        max_leverage=2.0,
        bar_duration=pd.Timedelta(hours=1),
    ):
        """
        strategies      : list of {"name": str, "direction_array": array} - a
                           +1/-1/0 direction per candle, one array per
                           strategy, all traded together on one account
        bar_duration    : fixed candle spacing, used to convert entry_time ->
                           held_bars. NOT inferred from adjacent rows in
                           self.df, since a resumed run's df may start
                           mid-history with no "previous" row to diff against.
        """
        self.df = df
        self.strategy_arrays = {s["name"]: np.asarray(s["direction_array"]) for s in strategies}
        self.strategy_names = list(self.strategy_arrays.keys())
        self.bar_duration = bar_duration

        self.initial_capital = initial_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_hold_bars = max_hold_bars
        self.fee_pct = fee_pct
        self.max_concurrent_trades = max_concurrent_trades
        self.portfolio_risk_cap_pct = portfolio_risk_cap_pct
        self.drawdown_throttle_trigger_pct = drawdown_throttle_trigger_pct
        self.drawdown_recovery_pct = drawdown_recovery_pct
        self.throttled_risk_pct = throttled_risk_pct
        self.max_leverage = max_leverage

    # ------------------------------------------------------------------
    # Core simulation - one shared account, walked bar by bar
    # ------------------------------------------------------------------
    def _simulate(self, start_i, equity, peak_equity, throttled, open_positions, pending_entries):
        df = self.df
        n = len(df)
        open_ = df["Open"].to_numpy()
        high = df["High"].to_numpy()
        low = df["Low"].to_numpy()
        close = df["Close"].to_numpy()
        sig_arrays = self.strategy_arrays

        trades = []
        equity_curve = [equity]

        for i in range(start_i, n):
            # 1. Open anything scheduled from the previous bar's signal.
            for strategy_name, direction in pending_entries:
                if len(open_positions) >= self.max_concurrent_trades:
                    continue  # slot full - this entry is missed, not queued (realistic)

                occupied_directions = {p["direction"] for p in open_positions}
                if direction in occupied_directions:
                    continue  # that direction already has an open position - one LONG and one SHORT max, never two of the same

                current_risk_pct = self.throttled_risk_pct if throttled else self.risk_per_trade_pct
                risk_dollars = equity * (current_risk_pct / 100)
                open_risk_dollars = sum(p["risk_dollars"] for p in open_positions)
                if open_risk_dollars + risk_dollars > equity * (self.portfolio_risk_cap_pct / 100):
                    continue  # would blow through the portfolio-level risk cap

                entry_price = open_[i]
                stop_dist = entry_price * (self.stop_loss_pct / 100)
                target_dist = entry_price * (self.take_profit_pct / 100)
                if direction == 1:
                    stop_price = entry_price - stop_dist
                    target_price = entry_price + target_dist
                else:
                    stop_price = entry_price + stop_dist
                    target_price = entry_price - target_dist

                position_size = risk_dollars / stop_dist
                max_position_size = (equity * self.max_leverage) / entry_price
                position_size = min(position_size, max_position_size)

                open_positions.append(
                    {
                        "strategy": strategy_name,
                        "direction": direction,
                        "entry_time": df.index[i],
                        "entry_price": entry_price,
                        "equity_at_entry": equity,
                        "stop_price": stop_price,
                        "target_price": target_price,
                        "stop_dist": stop_dist,
                        "risk_dollars": risk_dollars,
                        "position_size": position_size,
                    }
                )
            pending_entries = []

            # 2. Manage / close open positions using this bar's High/Low.
            still_open = []
            just_closed_directions = set()
            for pos in open_positions:
                direction = pos["direction"]
                held_bars = (df.index[i] - pos["entry_time"]) / self.bar_duration
                exit_price, exit_reason = None, None

                hit_stop = low[i] <= pos["stop_price"] if direction == 1 else high[i] >= pos["stop_price"]
                hit_target = high[i] >= pos["target_price"] if direction == 1 else low[i] <= pos["target_price"]
                if hit_stop and hit_target:
                    exit_price, exit_reason = close[i], "both_hit_use_close"
                elif hit_stop:
                    exit_price, exit_reason = pos["stop_price"], "stop"
                elif hit_target:
                    exit_price, exit_reason = pos["target_price"], "target"

                if exit_price is None and held_bars >= self.max_hold_bars:
                    exit_price, exit_reason = close[i], "time"

                if exit_price is None:
                    still_open.append(pos)
                    continue

                raw_pnl = pos["position_size"] * (exit_price - pos["entry_price"]) * direction
                fee = pos["position_size"] * pos["entry_price"] * (self.fee_pct / 100) * 2
                pnl = raw_pnl - fee
                equity += pnl
                just_closed_directions.add(pos["direction"])
                trades.append(
                    {
                        "strategy": pos["strategy"],
                        "entry_time": pos["entry_time"],
                        "exit_time": df.index[i],
                        "direction": "LONG" if direction == 1 else "SHORT",
                        "entry_price": round(pos["entry_price"], 6),
                        "exit_price": round(exit_price, 6),
                        "stop_price": round(pos["stop_price"], 6),
                        "target_price": round(pos["target_price"], 6),
                        "position_size": round(pos["position_size"], 6),
                        "leverage": round((pos["position_size"] * pos["entry_price"]) / pos["equity_at_entry"], 3),
                        "exit_reason": exit_reason,
                        "holding_bars": int(held_bars),
                        "holding_time": str(df.index[i] - pos["entry_time"]),
                        "planned_rr": round(self.take_profit_pct / self.stop_loss_pct, 3),
                        "rr_achieved": round((exit_price - pos["entry_price"]) * direction / pos["stop_dist"], 3),
                        "pnl": pnl,
                        "equity_after": equity,
                    }
                )

            open_positions = still_open
            equity_curve.append(equity)

            peak_equity = max(peak_equity, equity)
            drawdown_pct = (peak_equity - equity) / peak_equity * 100 if peak_equity else 0
            if not throttled and drawdown_pct >= self.drawdown_throttle_trigger_pct:
                throttled = True
            elif throttled and drawdown_pct <= self.drawdown_recovery_pct:
                throttled = False

            # 3. New signals firing on this bar get queued for next bar's open.
            reserved_directions = {p["direction"] for p in open_positions} | just_closed_directions
            for name, arr in sig_arrays.items():
                direction = arr[i]
                if direction == 0 or direction in reserved_directions:
                    continue
                if i + 1 < n:
                    pending_entries.append((name, direction))
                    reserved_directions.add(direction)  # first strategy to fire this direction this bar wins

        return trades, equity, equity_curve, open_positions, pending_entries, peak_equity, throttled

    def run(self):
        """Fresh start at initial_capital, no prior positions."""
        return self._simulate(
            start_i=0,
            equity=self.initial_capital,
            peak_equity=self.initial_capital,
            throttled=False,
            open_positions=[],
            pending_entries=[],
        )

    def run_incremental(self, prior_state):
        """Resume from persisted state instead of starting fresh. `prior_state`
        needs: balance, peak_equity, throttled, open_positions, pending_entries,
        last_processed_time (a pandas Timestamp, or None to process the whole df).

        Only bars strictly AFTER last_processed_time are simulated - self.df
        may be a small rolling window that doesn't contain the bars any
        already-open position was originally entered on, which is exactly why
        positions are keyed by entry_time rather than a bar index."""
        last_processed_time = prior_state.get("last_processed_time")
        if last_processed_time is None:
            start_i = 0
        else:
            after = self.df.index > last_processed_time
            if not after.any():
                return (
                    [],
                    prior_state["balance"],
                    [prior_state["balance"]],
                    prior_state.get("open_positions", []),
                    prior_state.get("pending_entries", []),
                    prior_state.get("peak_equity", prior_state["balance"]),
                    prior_state.get("throttled", False),
                )
            start_i = int(np.argmax(after))

        return self._simulate(
            start_i=start_i,
            equity=prior_state["balance"],
            peak_equity=prior_state.get("peak_equity", prior_state["balance"]),
            throttled=prior_state.get("throttled", False),
            open_positions=prior_state.get("open_positions", []),
            pending_entries=prior_state.get("pending_entries", []),
        )

    @staticmethod
    def max_drawdown_pct(equity_curve):
        peak = equity_curve[0]
        max_dd = 0.0
        for e in equity_curve:
            peak = max(peak, e)
            if peak:
                max_dd = max(max_dd, (peak - e) / peak * 100)
        return max_dd
