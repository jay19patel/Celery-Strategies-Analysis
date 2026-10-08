"""Simulated broker. Fills at the live WebSocket price with slippage and fees, uses
the real Delta contract size, and closes on SL / TP / liquidation / max-hold as
ticks arrive. State lives in SQLite, so it survives restarts.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from tradebuddy import structures
from tradebuddy.brokers.base import Account, Position, protection_error
from tradebuddy.brokers.paper_options import PaperOptions
from tradebuddy.errors import BrokerError
from tradebuddy.events import EventBus, PositionClosed, PositionUpdate, Tick
from tradebuddy.settings import Settings
from tradebuddy.store import Store
from tradebuddy.stream import PriceBook

Specs = Callable[[str], Awaitable[dict[str, Any]]]
HISTORY_OUTCOMES = {"win": "pnl > 0", "loss": "pnl <= 0"}  # fixed SQL for the history filter


class PaperBroker:
    name = "paper"

    def __init__(
        self, store: Store, bus: EventBus, prices: PriceBook, specs: Specs, settings: Callable[[], Settings],
        options_snapshot: Callable[[str], dict[str, Any] | None] = lambda _symbol: None,
    ) -> None:
        self.store = store
        self.db = store.db
        self.bus = bus
        self.prices = prices
        self.specs = specs
        self.settings = settings
        self.options = PaperOptions(store, bus, settings, options_snapshot, prices.price)

    def not_ready(self) -> str:
        return ""

    # -- account ------------------------------------------------------------

    def _account_row(self) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()
        if row is None:
            balance = self.settings().paper_starting_balance
            self.db.execute(
                "INSERT INTO paper_account (id, balance, starting_balance, created_at) VALUES (1, ?, ?, ?)", (balance, balance, time.time())
            )
            row = self.db.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()
        return dict(row)

    def _rows(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM paper_positions ORDER BY opened_at")]

    def _row(self, symbol: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM paper_positions WHERE symbol = ?", (symbol,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _direction(side: str) -> int:
        return 1 if side == "long" else -1

    def _upnl(self, pos: dict[str, Any], price: float) -> float:
        return (price - pos["entry_price"]) * pos["size"] * pos["contract_value"] * self._direction(pos["side"])

    async def account(self) -> Account:
        acct = self._account_row()
        rows = self._rows()
        margin = sum(p["margin"] for p in rows) + self.options.margin()
        upnl = sum(self._upnl(p, self.prices.price(p["symbol"]) or p["entry_price"]) for p in rows) + self.options.unrealized()
        return Account(
            broker=self.name,
            currency="USD",
            balance=acct["balance"],
            available=acct["balance"] - margin,
            margin_used=margin,
            unrealized_pnl=upnl,
            realized_pnl=acct["realized_pnl"],
            extra={"starting_balance": acct["starting_balance"], "fees_paid": acct["fees_paid"], "created_at": acct["created_at"]},
        )

    # -- orders -------------------------------------------------------------

    async def place_order(self, symbol, side, size, client_order_id, stop_loss=None, take_profit=None, strategy=""):
        if existing := await self.order_by_client_id(client_order_id):
            return existing  # idempotent on client_order_id
        if side not in ("buy", "sell"):
            raise BrokerError(f"paper: side must be buy or sell, not {side!r}")
        if size != int(size) or size < 1:
            raise BrokerError(f"paper: size must be a whole number of contracts, not {size!r}")
        spec = await self.specs(symbol)  # the only await: everything below is atomic on the event loop
        contract_value = float(spec.get("contract_value") or 1.0)

        price = self.prices.price(symbol)
        if price is None:
            raise BrokerError(f"paper: no fresh price for {symbol}")
        if self._row(symbol):
            raise BrokerError(f"paper: a {symbol} position is already open")
        if reason := protection_error(side, price, stop_loss, take_profit):
            raise BrokerError(f"paper: {reason}")

        s = self.settings()
        pos_side = "long" if side == "buy" else "short"
        d = self._direction(pos_side)
        fill = price * (1 + d * s.paper_slippage_pct / 100)
        notional = size * contract_value * fill
        margin = notional / s.paper_leverage
        fee = notional * s.paper_fee_pct / 100
        acct = self._account_row()
        available = acct["balance"] - sum(p["margin"] for p in self._rows()) - self.options.margin()
        if margin + fee > available:
            raise BrokerError(f"paper: insufficient margin (needs {margin + fee:.2f}, available {available:.2f})")

        # Liquidation when the loss eats the margin: entry * (1 -/+ 1/leverage).
        liquidation = fill * (1 - d / s.paper_leverage)
        with self.db:
            self.db.execute(
                "INSERT INTO paper_positions (symbol, client_order_id, strategy, side, size, contract_value, entry_price, leverage,"
                " margin, entry_fee, stop_loss, take_profit, liquidation_price, opened_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (symbol, client_order_id, strategy, pos_side, size, contract_value, fill, s.paper_leverage, margin, fee,
                 stop_loss, take_profit, liquidation, time.time()),
            )
            self.db.execute("UPDATE paper_account SET balance = balance - ?, fees_paid = fees_paid + ? WHERE id = 1", (fee, fee))
        self.bus.publish(PositionUpdate(broker=self.name, symbol=symbol, size=d * size, entry_price=fill))
        return {"id": f"paper-{client_order_id[-10:]}", "state": "closed", "average_fill_price": fill}

    async def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        for table, price in (("paper_positions", "entry_price"), ("paper_trades", "entry_price")):
            row = self.db.execute(f"SELECT {price} FROM {table} WHERE client_order_id = ?", (client_order_id,)).fetchone()  # noqa: S608 - fixed names
            if row:
                return {"id": f"paper-{client_order_id[-10:]}", "state": "closed", "average_fill_price": row[0]}
        return None

    async def open_orders(self) -> list[dict[str, Any]]:
        """The paper broker keeps SL/TP on the position; show them as the resting legs they would be."""
        legs = []
        for p in self._rows():
            exit_side = "sell" if p["side"] == "long" else "buy"
            for kind, price in (("stop_loss", p["stop_loss"]), ("take_profit", p["take_profit"])):
                if price:
                    legs.append({"id": f"{p['symbol']}-{kind}", "client_order_id": p["client_order_id"], "symbol": p["symbol"],
                                 "side": exit_side, "type": kind, "size": p["size"], "price": price, "state": "open"})
        return legs

    # -- positions ----------------------------------------------------------

    async def positions(self) -> list[Position]:
        out = []
        for p in self._rows():
            mark = self.prices.price(p["symbol"]) or p["entry_price"]
            out.append(Position(
                broker=self.name, symbol=p["symbol"], side=p["side"], size=p["size"], entry_price=p["entry_price"],
                mark_price=mark, unrealized_pnl=self._upnl(p, mark), margin=p["margin"],
                liquidation_price=p["liquidation_price"], stop_loss=p["stop_loss"], take_profit=p["take_profit"],
                opened_at=p["opened_at"], strategy=p["strategy"],
            ))
        return out

    async def close_position(self, symbol: str, reason: str = "manual close", price: float | None = None) -> dict[str, Any]:
        pos = self._row(symbol)
        if pos is None:
            raise BrokerError(f"no open {symbol} paper position")
        if price is None:
            live = self.prices.price(symbol)
            if live is None:
                raise BrokerError(f"paper: no fresh price to close {symbol}")
            price = live * (1 - self._direction(pos["side"]) * self.settings().paper_slippage_pct / 100)
        self._close(pos, price, reason)
        return {"closed": [symbol], "errors": []}

    async def close_all(self) -> dict[str, Any]:
        closed, errors = [], []
        for p in self._rows():
            try:
                await self.close_position(p["symbol"])
                closed.append(p["symbol"])
            except BrokerError as exc:
                errors.append(f"{p['symbol']}: {exc}")
        structures = self.options.close_all()
        return {"closed": closed + structures["closed"], "errors": errors + structures["errors"]}

    async def size_for_margin(self, symbol: str, price: float, margin: float) -> int:
        spec = await self.specs(symbol)
        contract_value = float(spec.get("contract_value") or 1.0)
        leverage = self.settings().paper_leverage
        # margin = size * contract_value * price / leverage
        # so size = (margin * leverage) / (price * contract_value)
        size = (margin * leverage) / (price * contract_value)
        return max(0, int(size))  # a budget that buys no whole contract buys nothing

    def _close(self, pos: dict[str, Any], exit_price: float, reason: str) -> None:
        gross = self._upnl(pos, exit_price)
        exit_fee = pos["size"] * pos["contract_value"] * exit_price * self.settings().paper_fee_pct / 100
        # A leveraged loss cannot exceed the margin posted.
        gross = max(gross, -pos["margin"])
        pnl = gross - pos["entry_fee"] - exit_fee
        now = time.time()
        with self.db:
            self.db.execute("DELETE FROM paper_positions WHERE symbol = ?", (pos["symbol"],))
            self.db.execute(
                "UPDATE paper_account SET balance = balance + ?, realized_pnl = realized_pnl + ?, fees_paid = fees_paid + ? WHERE id = 1",
                (gross - exit_fee, pnl, exit_fee),
            )
            self.db.execute(
                "INSERT INTO paper_trades (client_order_id, strategy, symbol, side, size, contract_value, entry_price, exit_price,"
                " leverage, margin, gross_pnl, fees, pnl, reason, opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pos["client_order_id"], pos["strategy"], pos["symbol"], pos["side"], pos["size"], pos["contract_value"],
                 pos["entry_price"], exit_price, pos["leverage"], pos["margin"], gross, pos["entry_fee"] + exit_fee, pnl,
                 reason, pos["opened_at"], now),
            )
        self.bus.publish(PositionUpdate(broker=self.name, symbol=pos["symbol"], size=0, entry_price=0))
        self.bus.publish(PositionClosed(
            broker=self.name, strategy=pos["strategy"], symbol=pos["symbol"], side=pos["side"],
            entry_price=pos["entry_price"], exit_price=exit_price, pnl=round(pnl, 6), reason=reason,
        ))

    # -- protective exits, driven by ticks -----------------------------------

    async def on_tick(self, tick: Tick) -> None:
        pos = self._row(tick.symbol)
        if pos is None:
            return
        price, long = tick.price, pos["side"] == "long"
        sl, tp, liq = pos["stop_loss"], pos["take_profit"], pos["liquidation_price"]
        max_hold = self.settings().paper_max_hold_hours

        if (price <= liq) if long else (price >= liq):
            self._close(pos, liq, "liquidation")
        elif sl and ((price <= sl) if long else (price >= sl)):
            self._close(pos, sl, "stop loss hit")
        elif tp and ((price >= tp) if long else (price <= tp)):
            self._close(pos, tp, "take profit hit")
        elif max_hold and time.time() - pos["opened_at"] >= max_hold * 3600:
            self._close(pos, price, f"max hold {max_hold:g}h reached")

    # -- paper-only controls -------------------------------------------------

    async def update_protection(self, symbol: str, stop_loss: float, take_profit: float | None) -> None:
        pos = self._row(symbol)
        if pos is None:
            raise BrokerError(f"no open {symbol} paper position")
        price = self.prices.price(symbol)
        if price is None:
            raise BrokerError(f"paper: no fresh price for {symbol}")
        if reason := protection_error(pos["side"], price, stop_loss, take_profit):
            raise BrokerError(f"paper: {reason}")
        self.db.execute("UPDATE paper_positions SET stop_loss = ?, take_profit = ? WHERE symbol = ?", (stop_loss, take_profit, symbol))

    def reset(self) -> None:
        balance = self.settings().paper_starting_balance
        with self.db:
            self.db.execute("DELETE FROM paper_positions")
            self.db.execute("DELETE FROM paper_structures")
            self.db.execute("DELETE FROM paper_trades")
            self.db.execute("DELETE FROM paper_account")
            self.db.execute(
                "INSERT INTO paper_account (id, balance, starting_balance, created_at) VALUES (1, ?, ?, ?)", (balance, balance, time.time())
            )

    def trades(self, limit: int = 200) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM paper_trades ORDER BY closed_at DESC LIMIT ?", (limit,))]

    def stats(self) -> dict[str, Any]:
        acct = self._account_row()
        rows = [dict(r) for r in self.db.execute("SELECT * FROM paper_trades ORDER BY closed_at")]

        def summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
            wins = [t for t in trades if t["pnl"] > 0]
            losses = [t for t in trades if t["pnl"] <= 0]
            gross_win, gross_loss = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
            n = len(trades)
            return {
                "losses": len(losses),
                "avg_win": round(gross_win / len(wins), 4) if wins else None,
                "avg_loss": round(-gross_loss / len(losses), 4) if losses else None,
                "expectancy": round((gross_win - gross_loss) / n, 4) if n else None,  # average net P&L per trade
                "avg_hold_seconds": round(sum(t["closed_at"] - t["opened_at"] for t in trades) / n) if n else None,
                "last_closed_at": max((t["closed_at"] for t in trades), default=None),
                "trades": len(trades),
                "wins": len(wins),
                "win_rate": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
                "pnl": round(sum(t["pnl"] for t in trades), 4),
                "fees": round(sum(t["fees"] for t in trades), 4),
                "best": round(max((t["pnl"] for t in trades), default=0.0), 4),
                "worst": round(min((t["pnl"] for t in trades), default=0.0), 4),
                "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            }

        equity, peak, max_dd, curve = acct["starting_balance"], acct["starting_balance"], 0.0, [[acct["created_at"], acct["starting_balance"]]]
        for t in rows:
            equity += t["pnl"]
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak * 100 if peak else 0.0)
            curve.append([t["closed_at"], round(equity, 4)])

        by_strategy = {}
        for t in rows:
            by_strategy.setdefault(t["strategy"], []).append(t)
        total_abs = sum(abs(t["pnl"]) for t in rows) or 1.0
        reasons: dict[str, int] = {}
        for t in rows:
            reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
        return {
            "overall": summary(rows) | {"max_drawdown_pct": round(max_dd, 2)},
            "by_strategy": sorted(
                ({"strategy": k, "share_pct": round(100 * sum(abs(t["pnl"]) for t in v) / total_abs, 1)} | summary(v) for k, v in by_strategy.items()),
                key=lambda s: -s["pnl"],
            ),
            "equity_curve": curve,
            "exit_reasons": reasons,
        }

    def history(self, page: int = 1, per_page: int = 25, strategy: str = "", q: str = "", outcome: str = "") -> dict[str, Any]:
        """Closed trades, newest first, a page at a time, with each trade's order (its SL / TP) and, for an option
        structure, its leg orders. Filters: strategy, a symbol / reason / id search, win or loss."""
        where, args = ["1 = 1"], []
        if strategy:
            where.append("strategy = ?")
            args.append(strategy)
        if q:
            where.append("(symbol LIKE ? OR reason LIKE ? OR client_order_id LIKE ?)")
            args += [f"%{q}%"] * 3
        if outcome in HISTORY_OUTCOMES:
            where.append(HISTORY_OUTCOMES[outcome])  # fixed SQL from the dict above, never user text
        clause = " AND ".join(where)
        per_page = max(5, min(int(per_page), 200))
        agg = self.db.execute(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(pnl), 0) AS pnl, COALESCE(SUM(pnl > 0), 0) AS wins, COALESCE(SUM(fees), 0) AS fees FROM paper_trades WHERE {clause}",  # noqa: S608 - fixed clauses, values bound
            args,
        ).fetchone()
        pages = max(1, -(-agg["n"] // per_page))
        page = max(1, min(int(page), pages))
        rows = [dict(r) for r in self.db.execute(
            f"SELECT * FROM paper_trades WHERE {clause} ORDER BY closed_at DESC LIMIT ? OFFSET ?",  # noqa: S608 - fixed clauses, values bound
            (*args, per_page, (page - 1) * per_page),
        )]
        for t in rows:
            order = self.store.order(t["client_order_id"])
            t["stop_loss"] = order["stop_loss"] if order else None
            t["take_profit"] = order["take_profit"] if order else None
            t["order_id"] = order["order_id"] if order else None
            t["legs"] = [o for i in range(4) if (o := self.store.order(structures.leg_order_id(t["client_order_id"], i)))]
            t["hold_seconds"] = round(t["closed_at"] - t["opened_at"])
            t["return_pct"] = round(100 * t["pnl"] / t["margin"], 2) if t["margin"] else None
            risk = abs(t["entry_price"] - t["stop_loss"]) * t["size"] * t["contract_value"] if t["stop_loss"] and not t["legs"] else None
            t["risk_usd"] = round(risk, 4) if risk else None
            t["r_multiple"] = round(t["pnl"] / risk, 2) if risk else None
        strategies = [r[0] for r in self.db.execute("SELECT DISTINCT strategy FROM paper_trades ORDER BY strategy")]
        return {
            "rows": rows, "total": agg["n"], "page": page, "pages": pages, "per_page": per_page, "strategies": strategies,
            "summary": {"trades": agg["n"], "pnl": round(agg["pnl"], 4), "wins": agg["wins"], "fees": round(agg["fees"], 4),
                        "win_rate": round(100 * agg["wins"] / agg["n"], 1) if agg["n"] else 0.0},
        }

    def position_details(self, symbol: str) -> dict[str, Any] | None:
        """What a paper position knows beyond the Broker protocol: contract size, leverage, fees, its order id."""
        row = self._row(symbol)
        if row is None:
            return None
        exit_fee = row["size"] * row["contract_value"] * (self.prices.price(symbol) or row["entry_price"]) * self.settings().paper_fee_pct / 100
        return {k: row[k] for k in ("contract_value", "leverage", "entry_fee", "client_order_id")} | {"exit_fee_est": round(exit_fee, 4)}
